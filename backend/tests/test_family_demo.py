import json

from fastapi.testclient import TestClient

from backend.app import create_app
from backend.tests.test_agent import FakeProvider, FakeSam


def test_demo_uses_local_original_and_does_not_return_private_paths(tmp_path):
    app = create_app(tmp_path, FakeProvider(), FakeSam())
    with TestClient(app) as client:
        assert client.get("/api/demo").json() == {"available": False}
        sid = client.post("/api/sessions", json={"title": "Dad's recipe"}).json()["id"]
        media = client.post(f"/api/sessions/{sid}/evidence", files={"file": ("original.mp4", b"\x00\x00\x00\x18ftypisom", "video/mp4")}).json()
        (tmp_path / "family-demo.json").write_text(json.dumps({"session_id": sid, "original_evidence_id": media["id"],
            "title": "Dad's recipe", "duration_seconds": 60, "source_sha256": "private-source-hash",
            "clips": [{"evidence_id": media["id"], "start_seconds": 0, "duration_seconds": 60, "status": "prepared",
                       "path": "/private/file.mp4", "metadata": {"provider": "private"}}]}))
        result = client.get("/api/demo")
        assert result.status_code == 200 and result.json()["available"] is True
        assert "path" not in result.text and "private" not in result.text
        (tmp_path / "media" / (media["id"] + ".mp4")).unlink()
        assert client.get("/api/demo").status_code == 503


def test_sdk_media_is_declared_as_mock_and_does_not_trigger_inference(tmp_path):
    provider = FakeProvider()
    with TestClient(create_app(tmp_path, provider, FakeSam())) as client:
        sid = client.post("/api/sessions", json={"title": "Replay"}).json()["id"]
        response = client.post(f"/api/sessions/{sid}/evidence", data={"capture_source": "meta_dat_mock"},
            files={"file": ("frame.jpg", b"\xff\xd8\xfftest", "image/jpeg")})
        assert response.status_code == 201
        assert response.json()["capture_source"] == "meta_dat_mock"
        assert response.json()["capture_source_verification"] == "client_declared"
        assert provider.calls == []
        assert client.post(f"/api/sessions/{sid}/evidence", data={"capture_source": "invented"},
            files={"file": ("frame.jpg", b"\xff\xd8\xfftest", "image/jpeg")}).status_code == 400
