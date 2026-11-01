"""Real local HTTP contracts, simulated perception/ASR/voice; no external calls."""
import asyncio
import base64
import copy
import io
import threading
import time
import wave
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from backend.app import create_app
from backend.tests.test_agent import FakeProvider, FakeSam
from backend.tests.test_live import perception


class LiveProvider:
    configured = True
    model = "fake-live-http-only"

    def __init__(self):
        self.calls = []
        self.audio_calls = []
        self.audio_gate = threading.Event()
        self.audio_gate.set()
        self.audio_text = "Cuando el surco queda abierto y noto resistencia."
        self.audio_failure = False

    async def live_observe(self, context, media):
        self.calls.append((copy.deepcopy(context), copy.deepcopy(media)))
        return perception("gap-" + str(len(self.calls)))

    async def live_transcribe(self, path, duration):
        self.audio_calls.append((Path(path), duration))
        while not self.audio_gate.is_set():
            await asyncio.sleep(.002)
        if self.audio_failure:
            raise RuntimeError("simulated transcription failure")
        return {"text": self.audio_text, "model": "fake-asr-http-only"}

    async def close(self):
        self.audio_gate.set()


class Voice:
    def __init__(self, root):
        self.path = root / "test-voice.wav"
        self.path.write_bytes(wav())
        self.calls = []

    def status(self):
        return {"configured": True, "provider": "test", "audible_playback_verified": False}

    async def warm(self):
        pass

    async def synthesize(self, text):
        self.calls.append(text)
        return self.path

    async def close(self):
        pass


def wav(seconds=.1, channels=1, width=2):
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setnchannels(channels)
        audio.setsampwidth(width)
        audio.setframerate(16000)
        audio.writeframes(b"\0" * (int(seconds * 16000) * channels * width))
    return output.getvalue()


def jpeg(colour="red", size=(64, 48)):
    output = io.BytesIO()
    Image.new("RGB", size, colour).save(output, format="JPEG")
    return base64.b64encode(output.getvalue()).decode()


class API:
    def __init__(self, app, client, provider, voice):
        self.app, self.client, self.provider, self.voice = app, client, provider, voice
        self.store = app.state.store
        # Remove the conversational cooldown, retaining actual policy gates.
        app.state.live.cooldown = 0

    def create(self, **changes):
        response = self.client.post("/api/live", json={"consent": True, "source_kind": "phone",
            "teacher": "Papá", "speaker_name": "David", "speaker_role": "wearer", **changes})
        assert response.status_code == 201, response.text
        return response.json()["id"]

    def snap(self, sid, **kwargs):
        response = self.client.get(f"/api/live/{sid}", **kwargs)
        assert response.status_code == 200, response.text
        return response.json()

    def until(self, sid, condition):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            snapshot = self.snap(sid)
            if condition(snapshot):
                return snapshot
            time.sleep(.005)
        raise AssertionError("Simulated live loop did not reach expected state: " + repr(snapshot))

    def observe(self, sid, seq=0, second=1, epoch=0, **changes):
        return self.client.post(f"/api/live/{sid}/observe", json={"sequence": seq, "scene_epoch": epoch,
            "frames": [{"jpeg_base64": jpeg(), "source_seconds": second}], **changes})

    def question(self, sid, seq=0, second=1, epoch=0):
        response = self.observe(sid, seq, second, epoch)
        assert response.status_code == 202, response.text
        return self.until(sid, lambda s: s["question"] is not None)["question"]

    def control(self, sid, action, **changes):
        response = self.client.post(f"/api/live/{sid}/control", json={"action": action, **changes})
        assert response.status_code == 200, response.text
        return response.json()

    def spoken(self, sid, question):
        snapshot = self.control(sid, "spoken", question_id=question["id"])
        assert snapshot["speaking"]["agent"] is True
        return self.control(sid, "speaking", channel="agent", speaking=False)

    def audio(self, sid, raw=None, **changes):
        values = {"utterance_id": "reply-1", "speaker_name": "David", "speaker_role": "wearer",
            "origin": "microphone", "start_seconds": "1", "end_seconds": "1", "scene_epoch": "0",
            "speech_started_at": str(time.time()), **changes}
        return self.client.post(f"/api/live/{sid}/audio", data=values,
            files={"file": ("reply.wav", raw if raw is not None else wav(), "audio/wav")})

    def typed(self, sid, question, **changes):
        return self.client.post(f"/api/live/{sid}/answer", json={"utterance_id": "typed-1",
            "text": "La mezcla deja un surco al remover.", "speaker_name": "David", "speaker_role": "wearer",
            "source_seconds": 1, "answer_to": question["id"], **changes})


@pytest.fixture
def api(tmp_path):
    provider, voice = LiveProvider(), Voice(tmp_path)
    app = create_app(tmp_path, FakeProvider(), FakeSam(), live_provider=provider, voice=voice)
    with TestClient(app) as client:
        yield API(app, client, provider, voice)
    provider.audio_gate.set()


def test_consent_fresh_replay_context_and_manual_turn_isolation(api):
    response = api.client.post("/api/live", json={"consent": False, "source_kind": "phone"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "live_consent_required"
    response = api.client.post("/api/live", json={"consent": True, "source_kind": "replay"})
    assert response.status_code == 404
    teacher = api.store.create_session({"title": "Previously analysed", "teacher": "Papá", "mode": "record"})
    api.store.put(teacher["id"], "note", {"text": "SECRET FUTURE STEP", "timestamp_seconds": 80})
    video = api.store.put(teacher["id"], "evidence", {"kind": "video", "duration_seconds": 60})
    sid = api.create(source_kind="replay", source_evidence_id=video["id"])
    assert api.snap(sid)["session"]["source_session_id"] is None
    api.question(sid)
    assert "SECRET FUTURE" not in repr(api.provider.calls[0][0])
    assert api.provider.calls[0][0]["source_cutoff_seconds"] == 1
    for action in (None, "pause"):
        if action:
            api.control(sid, action)
        result = api.client.post(f"/api/sessions/{sid}/turn", json={"text": "Do not start a second question loop."})
        assert result.status_code == 409
        assert result.json()["detail"]["code"] == "live_session_active"


def test_jpeg_capture_idempotence_conflicts_and_epoch_validation(api):
    sid = api.create()
    first = api.observe(sid)
    assert first.status_code == 202
    duplicate = api.observe(sid)
    assert duplicate.status_code == 202 and duplicate.json()["cached"] is True
    api.until(sid, lambda s: s["metrics"]["observations_analysed"] == 1)
    assert len(api.provider.calls) == 1
    assert len(api.store.list(sid, "evidence")) == 1
    different = api.observe(sid, frames=[{"jpeg_base64": jpeg("blue"), "source_seconds": 1}])
    assert different.status_code == 409
    assert different.json()["detail"]["code"] == "live_key_conflict"
    for invalid in ("!" * 30, base64.b64encode(b"not a real JPEG image").decode(), jpeg(size=(961, 1))):
        assert api.observe(sid, seq=1, second=2, frames=[{"jpeg_base64": invalid, "source_seconds": 2}]).status_code == 400
    unordered = [{"jpeg_base64": jpeg(), "source_seconds": second} for second in (3, 2)]
    assert api.observe(sid, seq=1, frames=unordered).status_code == 400
    api.control(sid, "seek", scene_epoch=1, source_seconds=0)
    assert api.observe(sid, seq=1).status_code == 409
    assert api.observe(sid, seq=0, epoch=1).status_code == 202
    api.control(sid, "pause")
    assert api.observe(sid, seq=1, epoch=1, second=2).status_code == 409


def test_replay_cutoff_and_session_question_ownership(api):
    source = api.store.create_session({"mode": "record", "title": "Original"})
    video = api.store.put(source["id"], "evidence", {"kind": "video", "duration_seconds": 3})
    sid = api.create(source_kind="replay", source_evidence_id=video["id"])
    assert api.observe(sid, second=4).status_code == 400
    question = api.question(sid)
    other = api.create()
    response = api.client.get(f"/api/live/{other}/questions/{question['id']}/voice")
    assert response.status_code == 404 and not api.voice.calls
    response = api.typed(other, question)
    assert response.status_code == 200 and response.json()["answered"] is False
    assert api.snap(sid)["question"]["id"] == question["id"]


def test_voice_bytes_do_not_ack_playback_and_typed_answer_keeps_provenance(api):
    sid = api.create()
    question = api.question(sid)
    response = api.client.get(f"/api/live/{sid}/questions/{question['id']}/voice")
    assert response.status_code == 200
    with wave.open(io.BytesIO(response.content), "rb") as voice:
        assert voice.getnchannels() == 1 and voice.getnframes() > 0
    assert api.snap(sid)["question"]["status"] == "pending"
    unspoken = api.typed(sid, question)
    assert unspoken.status_code == 200 and unspoken.json()["answered"] is False
    api.spoken(sid, question)
    result = api.typed(sid, question, utterance_id="typed-after-playback")
    assert result.status_code == 200 and result.json()["answered"] is True
    card = result.json()["card"]
    assert card["speaker"] == "David" and card["speaker_role"] == "wearer"
    assert card["confirmed"] is False
    note = api.store.get(card["decision_card"]["answer_note_id"], "note")
    assert note["origin"] == "typed" and note["automatic"] is False
    assert not api.provider.audio_calls
    api.control(sid, "confirm", card_id=card["id"])
    assert api.snap(sid)["cards"][0]["confirmed"] is True


def test_wav_answer_anchors_actor_and_idempotence_are_preserved(api):
    sid = api.create()
    question = api.question(sid)
    api.spoken(sid, question)
    api.provider.audio_gate.clear()
    try:
        result = api.audio(sid, answer_to=question["id"])
        assert result.status_code == 202 and result.json()["status"] == "transcribing"
        assert result.json()["duration_seconds"] == .1
        duplicate = api.audio(sid, answer_to=question["id"])
        assert duplicate.status_code == 202, duplicate.text
        assert duplicate.json().get("cached") is True, duplicate.text
        assert api.audio(sid, wav(.2), answer_to=question["id"]).status_code == 409
        assert api.snap(sid)["speech_pending"] == 1
    finally:
        api.provider.audio_gate.set()
    snapshot = api.until(sid, lambda s: len(s["cards"]) == 1 and not s["speech_pending"])
    card = snapshot["cards"][0]
    assert card["speaker"] == "David" and card["speaker_role"] == "wearer"
    details = card["decision_card"]
    assert details["answer_start_seconds"] == details["answer_end_seconds"] == 1
    note = api.store.get(details["answer_note_id"], "note")
    assert note["provenance"] == {"capture": "microphone", "model": "fake-asr-http-only", "human_verified": False}
    assert note["speaker_verification"] == "user_declared"
    audio = api.store.get(details["answer_evidence_ids"][0], "evidence")
    assert audio["kind"] == "audio" and audio["duration_seconds"] == .1
    assert audio["timestamp_seconds"] == audio["source_end_seconds"] == 1
    assert details["demonstration_evidence_ids"] and not card["confirmed"]
    assert len(api.provider.audio_calls) == 1
    assert api.audio(sid, answer_to=question["id"]).json()["cached"] is True


def test_wav_format_duration_and_declared_actor_validation(api):
    sid = api.create()
    for raw in (b"not WAV", wav(channels=2), wav(width=1), wav(35.01), wav()[:-3]):
        assert api.audio(sid, raw).status_code == 400
    assert api.audio(sid, speaker_role="pretend-diarization").status_code == 400
    assert api.audio(sid, start_seconds="nan").status_code == 400
    assert api.audio(sid, speech_started_at="nan").status_code == 400
    # Exactly 35 seconds is valid; source anchors may stay equal while replay is paused.
    accepted = api.audio(sid, wav(35), utterance_id="longest-valid")
    assert accepted.status_code == 202 and accepted.json()["duration_seconds"] == 35
    api.until(sid, lambda s: s["metrics"]["utterances_received"] == 1)


def test_delayed_asr_after_seek_never_answers_new_epoch_question(api):
    sid = api.create()
    first = api.question(sid)
    api.spoken(sid, first)
    api.provider.audio_gate.clear()
    try:
        assert api.audio(sid, answer_to=first["id"]).status_code == 202
        api.control(sid, "seek", scene_epoch=1, source_seconds=0)
        second = api.question(sid, seq=0, second=1, epoch=1)
        assert second["id"] != first["id"]
        api.spoken(sid, second)
    finally:
        api.provider.audio_gate.set()
    deadline = time.monotonic() + 3
    while (sid, "microphone") in api.app.state.live_routes.last_audio and time.monotonic() < deadline:
        time.sleep(.005)
    assert (sid, "microphone") not in api.app.state.live_routes.last_audio
    snapshot = api.snap(sid)
    assert snapshot["question"]["id"] == second["id"]
    assert snapshot["question"]["status"] == "spoken" and not snapshot["cards"]
    assert not api.store.list(sid, "note")
    assert "Cuando el surco" not in repr(api.provider.calls[-1][0])


def test_http_asr_admitted_before_pause_cannot_answer_after_resume_same_epoch(api):
    sid = api.create()
    question = api.question(sid)
    api.spoken(sid, question)
    api.provider.audio_gate.clear()
    try:
        assert api.audio(sid, answer_to=question["id"], utterance_id="before-pause").status_code == 202
        api.until(sid, lambda s: s["speech_pending"] == 1)
        paused = api.control(sid, "pause")
        resumed = api.control(sid, "resume")
        assert paused["scene_epoch"] == resumed["scene_epoch"] == 0
        assert resumed["question"]["id"] == question["id"]
    finally:
        api.provider.audio_gate.set()
    snapshot = api.until(sid, lambda s: not s["speech_pending"])
    assert snapshot["question"]["id"] == question["id"]
    assert snapshot["question"]["status"] == "spoken" and not snapshot["cards"]
    assert snapshot["metrics"]["utterances_received"] == 0
    assert not api.store.list(sid, "note")
    assert any(item["kind"] == "audio" for item in api.store.list(sid, "evidence"))
    # A fresh capture in the resumed generation still completes normally.
    assert api.audio(sid, answer_to=question["id"], utterance_id="after-resume").status_code == 202
    assert api.until(sid, lambda s: len(s["cards"]) == 1)["cards"][0]["quote"] == api.provider.audio_text


def test_learner_only_receives_confirmed_teacher_decisions(api):
    teacher = api.create()
    question = api.question(teacher)
    api.spoken(teacher, question)
    reply = api.typed(teacher, question)
    assert reply.status_code == 200 and reply.json()["answered"] is True
    unconfirmed = reply.json()["card"]
    confirmed = api.store.put(teacher, "knowledge", {"text": "Regla revisada por Papá", "source": "said", "confirmed": True})
    learner = api.create(mode="learn", lesson_session_id=teacher)
    api.question(learner)
    context = api.provider.calls[-1][0]
    assert [item["id"] for item in context["confirmed_lesson"]] == [confirmed["id"]]
    assert unconfirmed["id"] not in repr(context["confirmed_lesson"])
    assert api.snap(learner)["session"]["source_session_id"] == teacher


def test_recorded_recipe_audio_is_context_and_cannot_answer_agent_question(api):
    sid = api.create()
    question = api.question(sid)
    api.spoken(sid, question)
    accepted = api.audio(sid, origin="recorded_source", speaker_name="Papá", speaker_role="cook",
                         answer_to=question["id"])
    assert accepted.status_code == 202
    snapshot = api.until(sid, lambda s: s["metrics"]["utterances_received"] == 1 and not s["speech_pending"])
    assert snapshot["question"]["id"] == question["id"] and not snapshot["cards"]
    note = api.store.list(sid, "note")[0]
    assert note["speaker"] == "Papá" and note["role"] == "cook"
    assert note["origin"] == "recorded_source"


def test_failed_asr_publishes_error_without_answer_and_next_turn_recovers(api):
    sid = api.create()
    question = api.question(sid)
    api.spoken(sid, question)
    api.provider.audio_failure = True
    assert api.audio(sid, answer_to=question["id"]).status_code == 202
    failed = api.until(sid, lambda s: any(e["type"] == "tool_error" and e["data"].get("tool") == "transcription"
                                        for e in s["events"]) and not s["speech_pending"])
    assert not failed["cards"] and failed["question"]["id"] == question["id"]
    assert not api.store.list(sid, "note")
    assert api.store.list(sid, "live_audio_job")[0]["status"] == "failed"
    api.provider.audio_failure = False
    assert api.audio(sid, utterance_id="reply-retry", answer_to=question["id"]).status_code == 202
    recovered = api.until(sid, lambda s: len(s["cards"]) == 1)
    assert recovered["cards"][0]["quote"] == api.provider.audio_text
