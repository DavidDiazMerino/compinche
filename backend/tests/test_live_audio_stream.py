"""Deterministic local PCM bridge tests with a fake upstream WebSocket."""
import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.app import create_app
from backend.tests.test_agent import FakeProvider, FakeSam
from backend.tests.test_live_api import API, LiveProvider, Voice


PCM = b"\x01\x00" * 2400  # Real nonzero PCM fixture, 100 ms at 24 kHz.
TEXT = "Paro cuando el surco se mantiene al levantar la cuchara."


class Upstream:
    def __init__(self):
        self.loop = asyncio.get_running_loop()
        self.events = asyncio.Queue()
        self.sent = []
        self.received_pcm = threading.Event()
        self.closed = False

    async def send(self, value):
        self.sent.append(value)
        if isinstance(value, bytes):
            self.received_pcm.set()

    async def recv(self):
        return json.dumps({"sessionId": "fake-asr-session"})

    def __aiter__(self):
        return self

    async def __anext__(self):
        return await self.events.get()

    def emit(self, **event):
        self.loop.call_soon_threadsafe(self.events.put_nowait, json.dumps(event))

    async def close(self):
        self.closed = True


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    sockets = []

    async def connect(*args, **kwargs):
        assert args[0].startswith("wss://api.meta.ai/")
        upstream = Upstream()
        sockets.append(upstream)
        return upstream

    monkeypatch.setattr("backend.live_audio_stream.websockets.connect", connect)
    provider, voice = LiveProvider(), Voice(tmp_path)
    provider.key = "test-key-never-used-for-network"
    app = create_app(tmp_path, FakeProvider(), FakeSam(), live_provider=provider, voice=voice)
    with TestClient(app) as client:
        yield API(app, client, provider, voice), sockets


def start(source="microphone", **changes):
    return {"type": "start", "origin": source, "sample_rate": 24000,
        "speaker_name": "David", "speaker_role": "wearer", "source_seconds": 1, "scene_epoch": 0, **changes}


def open_stream(api, sid, **kwargs):
    return api.client.websocket_connect(f"/api/live/{sid}/audio-stream", **kwargs)


def close_stream(ws):
    # Wait for route cleanup before TestClient cancels its ASGI scope on exit.
    ws.close()
    assert ws.receive()["type"] == "websocket.close"


def begin_turn(ws, upstream, question, *, onset=True, epoch=0):
    clock = {"type": "clock", "source_seconds": 1, "scene_epoch": epoch,
             "answer_to": question["id"], "agent_speaking": False}
    if onset:
        clock.update(utterance_id="local-vad-1", speech_started_at=time.time())
    ws.send_json(clock)
    ws.send_bytes(PCM)
    assert upstream.received_pcm.wait(2), "PCM did not reach simulated upstream"
    upstream.emit(type="speechStart", turnId=1, audioProcessedMs=100)


def finish_turn(ws, upstream):
    upstream.emit(type="speechEnd", turnId=1, audioProcessedMs=100)
    upstream.emit(type="speechComplete", turnId=1, audioProcessedMs=100, transcript=TEXT)
    result = ws.receive_json()
    assert result["type"] == "transcript", result
    return result


def test_realtime_final_uses_vad_frozen_question_and_declared_actor(bridge):
    api, sockets = bridge
    sid = api.create()
    question = api.question(sid)
    api.spoken(sid, question)
    with open_stream(api, sid, headers={"origin": "http://testserver"}) as ws:
        ws.send_json(start())
        assert ws.receive_json()["type"] == "ready"
        upstream = sockets[0]
        authorization = json.loads(upstream.sent[0])
        assert authorization["audioEncoding"] == "PCM_24KHZ"
        begin_turn(ws, upstream, question)
        api.until(sid, lambda s: s["speech_pending"] == 1)
        upstream.emit(type="transcript", turnId=1, transcript="Paro cuando")
        assert ws.receive_json()["type"] == "partial"
        assert not api.snap(sid)["cards"]
        final = finish_turn(ws, upstream)
        assert final["result"]["answered"] is True
        snapshot = api.until(sid, lambda s: len(s["cards"]) == 1 and not s["speech_pending"])
        card = snapshot["cards"][0]
        assert card["speaker"] == "David" and card["speaker_role"] == "wearer"
        assert card["quote"] == TEXT and card["confirmed"] is False
        audio = api.store.get(card["decision_card"]["answer_evidence_ids"][0], "evidence")
        assert audio["duration_seconds"] == .1 and audio["capture_source"] == "microphone"
        assert audio["asr_turn_bounds"] == "approximate_progress_markers"
        assert audio["timestamp_seconds"] == audio["source_end_seconds"] == 1
        close_stream(ws)
    assert upstream.closed


@pytest.mark.parametrize("source,onset,expected", [
    ("recorded_source", True, "recorded_source_is_not_a_live_answer"),
    ("microphone", False, "question_not_spoken_before_utterance"),
])
def test_realtime_source_audio_and_missing_vad_onset_never_answer(bridge, source, onset, expected):
    api, sockets = bridge
    sid = api.create()
    question = api.question(sid)
    api.spoken(sid, question)
    with open_stream(api, sid) as ws:
        ws.send_json(start(source, speaker_name="Papá" if source == "recorded_source" else "David",
                           speaker_role="cook" if source == "recorded_source" else "wearer"))
        assert ws.receive_json()["type"] == "ready"
        begin_turn(ws, sockets[0], question, onset=onset)
        api.until(sid, lambda s: s["speech_pending"] == 1)
        final = finish_turn(ws, sockets[0])
        assert final["result"]["answered"] is False
        assert final["result"]["ignored"] == expected
        assert not api.snap(sid)["cards"]
        assert api.snap(sid)["question"]["id"] == question["id"]
        close_stream(ws)


def test_realtime_old_epoch_final_cannot_answer_new_question(bridge):
    api, sockets = bridge
    sid = api.create()
    first = api.question(sid)
    api.spoken(sid, first)
    with open_stream(api, sid) as ws:
        ws.send_json(start())
        assert ws.receive_json()["type"] == "ready"
        begin_turn(ws, sockets[0], first)
        api.until(sid, lambda s: s["speech_pending"] == 1)
        api.control(sid, "seek", scene_epoch=1, source_seconds=0)
        second = api.question(sid, seq=0, epoch=1)
        api.spoken(sid, second)
        sockets[0].emit(type="speechEnd", turnId=1, audioProcessedMs=100)
        sockets[0].emit(type="speechComplete", turnId=1, audioProcessedMs=100, transcript=TEXT)
        # Ordered upstream barrier proves the previous final has been processed.
        sockets[0].emit(type="transcript", transcript="old-final-barrier")
        assert ws.receive_json() == {"type": "partial", "text": "old-final-barrier", "turn_id": 1}
        snapshot = api.snap(sid)
        assert snapshot["question"]["id"] == second["id"] and not snapshot["cards"]
        assert not api.store.list(sid, "note")
        assert not snapshot["speech_pending"]
        close_stream(ws)


def test_realtime_turn_before_pause_cannot_answer_after_resume_same_epoch(bridge):
    api, sockets = bridge
    sid = api.create()
    question = api.question(sid)
    api.spoken(sid, question)
    with open_stream(api, sid) as ws:
        ws.send_json(start())
        assert ws.receive_json()["type"] == "ready"
        begin_turn(ws, sockets[0], question)
        api.until(sid, lambda s: s["speech_pending"] == 1)
        paused = api.control(sid, "pause")
        resumed = api.control(sid, "resume")
        assert paused["scene_epoch"] == resumed["scene_epoch"] == 0
        sockets[0].emit(type="speechEnd", turnId=1, audioProcessedMs=100)
        sockets[0].emit(type="speechComplete", turnId=1, audioProcessedMs=100, transcript=TEXT)
        sockets[0].emit(type="transcript", transcript="after-resume-barrier")
        assert ws.receive_json() == {"type": "partial", "text": "after-resume-barrier", "turn_id": 1}
        snapshot = api.snap(sid)
        assert snapshot["question"]["id"] == question["id"]
        assert snapshot["question"]["status"] == "spoken" and not snapshot["cards"]
        assert not snapshot["speech_pending"] and not api.store.list(sid, "note")
        close_stream(ws)


@pytest.mark.parametrize("changes", [
    {"speaker_role": "automatic-diarization"}, {"speaker_name": ""},
    {"sample_rate": 16000}, {"origin": "made-up"},
])
def test_realtime_rejects_bad_actor_rate_and_origin(bridge, changes):
    api, sockets = bridge
    sid = api.create()
    with open_stream(api, sid) as ws:
        ws.send_json(start(**changes))
        assert ws.receive_json()["type"] == "error"
    assert not sockets
    assert not api.app.state.live_routes.ws_count


def test_realtime_missing_key_offers_wav_fallback_without_upstream(bridge):
    api, sockets = bridge
    sid = api.create()
    api.provider.key = None
    with open_stream(api, sid) as ws:
        result = ws.receive_json()
        assert result["type"] == "error" and result["fallback"] is True
    assert not sockets and not api.app.state.live_routes.ws_count


@pytest.mark.parametrize("headers", [{"origin": "https://foreign.example"}, {"sec-fetch-site": "cross-site"}])
def test_realtime_blocks_cross_origin_before_accept(bridge, headers):
    api, sockets = bridge
    sid = api.create()
    with pytest.raises(WebSocketDisconnect) as caught:
        with open_stream(api, sid, headers=headers):
            pass
    assert caught.value.code == 1008 and not sockets


def test_duplicate_channel_cleanup_preserves_original_slot_and_speaking_state(bridge):
    api, sockets = bridge
    sid = api.create()
    with open_stream(api, sid) as first:
        first.send_json(start())
        assert first.receive_json()["type"] == "ready"
        api.control(sid, "speaking", channel="human", speaking=True)
        with open_stream(api, sid) as duplicate:
            duplicate.send_json(start())
            assert duplicate.receive_json()["type"] == "error"
        assert api.app.state.live_routes.ws_count == {(sid, "microphone"): 1}
        assert api.snap(sid)["speaking"]["human"] is True
        assert len(sockets) == 1 and not sockets[0].closed
        close_stream(first)
    assert not api.app.state.live_routes.ws_count
    assert api.snap(sid)["speaking"]["human"] is False
