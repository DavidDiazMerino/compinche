from pathlib import Path
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest

from backend.app import create_app
from backend.tests.test_agent import FakeProvider, FakeSam
from backend.clips import ClipFailure, ClipService, duration


def test_clips_keep_both_origins_cache_and_do_not_send_media_to_meta(tmp_path, monkeypatch):
    calls = []
    def encode(source, destination, start, length):
        calls.append((start, length))
        destination.write_bytes(b"\x00\x00\x00\x18ftypisom-encoded")
    monkeypatch.setattr("backend.clips.encode", encode)
    monkeypatch.setattr("backend.clips.duration", lambda path: 5 if ".tmp.mp4" in str(path) else 20)
    provider = FakeProvider()
    app = create_app(tmp_path, provider, FakeSam())
    with TestClient(app) as client:
        teacher = client.post("/api/sessions", json={"title": "Teacher"}).json()["id"]
        learner = client.post("/api/sessions", json={"title": "Learner", "mode": "learn", "source_session_id": teacher}).json()["id"]
        source = client.post(f"/api/sessions/{teacher}/evidence", data={"timestamp_seconds": "320"},
            files={"file": ("clip.mp4", b"\x00\x00\x00\x18ftypisom-original", "video/mp4")}).json()
        payload = {"evidence_id": source["id"], "start_seconds": 15, "duration_seconds": 10}
        response = client.post(f"/api/sessions/{learner}/clips", json=payload)
        assert response.status_code == 201, response.text
        clip = response.json()["evidence"]
        assert clip["session_id"] == learner
        assert clip["timestamp_seconds"] == 335
        assert clip["source_local_start_seconds"] == 15
        assert clip["original_evidence_id"] == source["id"]
        assert clip["duration_seconds"] == 5
        assert clip["uploaded_to_meta"] is False
        assert "path" not in clip
        retry = client.post(f"/api/sessions/{learner}/clips", json=payload).json()
        assert retry["cached"] is True and retry["evidence"]["id"] == clip["id"]
        assert calls == [(15, 5)]
        assert client.get(source["url"]).content == b"\x00\x00\x00\x18ftypisom-original"
        other = client.post("/api/sessions", json={"title": "Other"}).json()["id"]
        assert client.post(f"/api/sessions/{other}/clips", json=payload).status_code == 404
        assert provider.calls == []


@pytest.mark.parametrize("changes", [{"start_seconds": -1}, {"duration_seconds": 121}, {"duration_seconds": 0}])
def test_invalid_clip_bounds_are_rejected_before_encoding(tmp_path, changes):
    with TestClient(create_app(tmp_path, FakeProvider(), FakeSam())) as client:
        sid = client.post("/api/sessions", json={"title": "Recipe"}).json()["id"]
        result = client.post(f"/api/sessions/{sid}/clips", json={"evidence_id": "unused", **changes})
        assert result.status_code == 422


def test_search_finds_real_notes_corrections_and_recipes_with_accents(tmp_path):
    provider = FakeProvider()
    app = create_app(tmp_path, provider, FakeSam())
    with TestClient(app) as client:
        sid = client.post("/api/sessions", json={"title": "La receta del padre", "teacher": "Papá"}).json()["id"]
        note = app.state.store.put(sid, "note", {"text": "El frío cambia cómo noto la mezcla.", "confirmed": True})
        original = app.state.store.put(sid, "knowledge", {"text": "Esta corrección es de Papá.", "confirmed": True})
        guess = app.state.store.put(sid, "knowledge", {"text": "Una inferencia de la mezcla.", "confirmed": False})
        response = client.get("/api/search", params={"q": "frio"})
        assert response.status_code == 200
        assert {item["id"] for item in response.json()["items"]} == {note["id"]}
        assert original["id"] in {item["id"] for item in client.get("/api/search", params={"q": "correccion"}).json()["items"]}
        confirmed = client.get("/api/search", params={"category": "confirmed"}).json()["items"]
        assert {item["id"] for item in confirmed} == {original["id"]}
        assert guess["id"] in {item["id"] for item in client.get("/api/search", params={"category": "needs_review"}).json()["items"]}
        assert client.get("/api/search", params={"q": "%_"}).json()["items"] == []
        assert any(item["kind"] == "session" for item in client.get("/api/search", params={"q": "padre"}).json()["items"])
        assert client.get("/api/search", params={"category": "invalid"}).status_code == 400
        app.state.store.put(sid, "evidence", {"kind": "video", "role": "analysis_clip", "note": "A local interval from the source."})
        assert client.get("/api/search", params={"q": "interval"}).json()["items"] == []
        assert provider.calls == []


def test_mediarecorder_webm_without_duration_header_does_not_dump_all_packets(monkeypatch):
    responses = iter([
        {"streams": [{}], "format": {}},
    ])
    monkeypatch.setattr("backend.clips.subprocess.run", lambda *args, **kwargs: SimpleNamespace(stdout=json.dumps(next(responses))))
    assert duration(Path("recorded.webm")) is None


def test_unknown_source_duration_is_validated_on_the_encoded_interval(tmp_path, monkeypatch):
    from backend.clips import ClipService
    from backend.store import Store
    store = Store(tmp_path)
    try:
        sid = store.create_session({"title": "Phone", "mode": "record"})["id"]
        path = tmp_path / "media" / "phone.webm"
        path.write_bytes(b"recording")
        source = store.put(sid, "evidence", {"kind": "video", "path": str(path)})
        monkeypatch.setattr("backend.clips.duration", lambda path: 2 if ".tmp.mp4" in str(path) else None)
        monkeypatch.setattr("backend.clips.encode", lambda src, dst, start, length: dst.write_bytes(b"converted"))
        clip = ClipService(store).create(sid, source, 10, 15)["evidence"]
        assert clip["duration_seconds"] == 2
        assert clip["timestamp_seconds"] == 10
        assert clip["requested_duration_seconds"] == 15
    finally:
        store.close()


@pytest.mark.parametrize("start,length", [(-1, 15), (0, 121), (float("nan"), 5)])
def test_clip_service_also_enforces_bounds_for_non_http_callers(start, length):
    with pytest.raises(ClipFailure, match="finite"):
        ClipService(object()).create("unused", {}, start, length)
