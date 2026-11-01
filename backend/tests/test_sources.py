from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_agent import FakeProvider, FakeSam


def test_sources_resolve_teacher_and_learner_without_exposing_other_recipes_or_paths(tmp_path):
    app = create_app(tmp_path, FakeProvider(), FakeSam())
    store = app.state.store
    with TestClient(app) as client:
        teacher = client.post("/api/sessions", json={"title": "Original"}).json()["id"]
        learner = client.post("/api/sessions", json={"title": "Practice", "mode": "learn", "source_session_id": teacher}).json()["id"]
        foreign = client.post("/api/sessions", json={"title": "Other recipe"}).json()["id"]
        spoken = store.put(teacher, "message", {"role": "user", "text": "I stop when it leaves a trail."})
        evidence = store.put(teacher, "evidence", {"kind": "video", "path": "/private/source.mp4", "url": "/api/media/example",
                                                  "timestamp_seconds": 320, "remote_file_id": "private-remote-id"})
        own = store.put(learner, "note", {"text": "My pan is smaller."})
        unrelated = store.put(foreign, "note", {"text": "Private other recipe."})
        for item, kind in ((spoken, "message"), (evidence, "evidence"), (own, "note")):
            response = client.get(f"/api/sessions/{learner}/sources/{item['id']}")
            assert response.status_code == 200
            assert response.json()["entity_kind"] == kind
            assert response.json()["source"]["id"] == item["id"]
            assert "path" not in response.json()["source"]
            assert "remote_file_id" not in response.json()["source"]
        assert client.get(f"/api/sessions/{learner}/sources/{unrelated['id']}").status_code == 404
        assert client.get(f"/api/sessions/{teacher}/sources/{own['id']}").status_code == 404


def test_corrected_explanation_preserves_original_and_is_available_to_the_learner(tmp_path):
    app = create_app(tmp_path, FakeProvider(), FakeSam())
    with TestClient(app) as client:
        teacher = client.post("/api/sessions", json={"title": "Original"}).json()["id"]
        original = app.state.store.put(teacher, "knowledge", {"text": "Stir quickly.", "source": "inferred", "confirmed": False})
        learner = client.post("/api/sessions", json={"title": "Practice", "mode": "learn", "source_session_id": teacher}).json()["id"]
        response = client.post(f"/api/sessions/{teacher}/knowledge/{original['id']}/confirm", json={"text": "Stir gently."})
        assert response.status_code == 200
        source = client.get(f"/api/sessions/{learner}/sources/{original['id']}").json()["source"]
        assert source["text"] == "Stir gently."
        assert source["original_model_text"] == "Stir quickly."
        assert source["confirmed"] is True
        assert client.post(f"/api/sessions/{learner}/knowledge/{original['id']}/confirm", json={"text": "Changed by learner"}).status_code == 404


def test_confirming_unchanged_wording_does_not_create_a_fake_correction(tmp_path):
    app = create_app(tmp_path, FakeProvider(), FakeSam())
    with TestClient(app) as client:
        sid = client.post("/api/sessions", json={"title": "Original"}).json()["id"]
        claim = app.state.store.put(sid, "knowledge", {"text": "Stir gently.", "confirmed": False})
        saved = client.post(f"/api/sessions/{sid}/knowledge/{claim['id']}/confirm", json={"text": claim["text"]}).json()
        assert saved["confirmed"] is True
        assert "original_model_text" not in saved
