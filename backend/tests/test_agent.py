"""No live paid calls: fake provider exercises the real graph and HTTP API."""
import copy
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from backend.agent import CookingAgent, ConversationConflict, GuardrailFailure, validate_output
from backend.app import create_app
from backend.provider import MetaProvider, ProviderFailure
from backend.store import Store


def answer(context=None, **changes):
    message_id = context["conversation"][-1]["id"] if context else "user-1"
    result = {"reply": "I have kept your explanation with its source.", "evidence_ids": [message_id], "question": None, "knowledge": [], "segmentation_targets": [], "uncertainty": []}
    return {**result, **changes}


class FakeProvider:
    configured = True
    model = "fake-for-tests-only"

    def __init__(self, respond=None):
        self.respond = respond or answer
        self.calls = []

    def status(self):
        return {"configured": self.configured, "provider": "test", "model": self.model, "orchestrator": "langgraph"}

    def generate(self, context, media):
        self.calls.append((copy.deepcopy(context), copy.deepcopy(media)))
        return self.respond(context)


class FakeSam:
    def status(self):
        return {"configured": False, "status": "unavailable", "reason": "Test runtime disabled."}


def session(store, **changes):
    return store.create_session({"title": "Teacher's sauce", "teacher": "Alex", "mode": "record", "source_session_id": None, **changes})


def turn(text="Stir until it leaves a trail.", **changes):
    return {"text": text, "evidence_ids": [], "answer_to": None, "idempotency_key": None, **changes}


def question(text="What changes when the sauce is ready?"):
    return {"text": text, "reason": "The decision is missing from the evidence.", "evidence_ids": []}


def test_persistence_idempotency_and_single_pending_question(tmp_path):
    store = Store(tmp_path)
    sid = session(store)["id"]
    provider = FakeProvider(lambda c: answer(c, question=question()))
    agent = CookingAgent(store, provider)
    first_request = turn(idempotency_key="first")
    first = agent.invoke(sid, first_request)
    assert first["question"]["status"] == "pending"
    assert agent.invoke(sid, first_request) == first
    assert len(provider.calls) == 1
    assert len(store.list(sid, "message")) == 2
    with pytest.raises(ValueError, match="different turn"):
        agent.invoke(sid, turn("Different text", idempotency_key="first"))
    agent.close()
    store.close()

    store = Store(tmp_path)
    agent = CookingAgent(store, provider)
    try:
        second = agent.invoke(sid, turn("Here is another note."))
        assert second["question"]["id"] == first["question"]["id"]
        assert len(store.list(sid, "question")) == 1
        assert provider.calls[-1][0]["conversation"][0]["text"] == first_request["text"]
        third = agent.invoke(sid, turn("When the spoon trail holds.", answer_to=first["question"]["id"]))
        # The provider repeated the exact question just answered. Keep its
        # grounded reply, but do not ask the cook for the same answer again.
        assert third["question"] is None
        assert third["message"]["question"] is None
        assert store.get(first["question"]["id"], "question")["status"] == "answered"
        with pytest.raises(ConversationConflict, match="no longer"):
            agent.invoke(sid, turn(answer_to=first["question"]["id"]))
        state = agent.graph.get_state({"configurable": {"thread_id": sid}})
        assert state.values["context"]["session"]["id"] == sid
    finally:
        agent.close()
        store.close()


def test_learn_context_preserves_teacher_provenance(tmp_path):
    store = Store(tmp_path)
    source = session(store)
    original = store.put(source["id"], "message", {"role": "user", "text": "I use 20 grams of butter."})
    claim = store.put(source["id"], "knowledge", {"text": "20 grams of butter", "source": "said", "confirmed": True, "evidence_ids": [original["id"]]})
    learner = session(store, title="My attempt", mode="learn", source_session_id=source["id"])
    provider = FakeProvider()
    agent = CookingAgent(store, provider)
    try:
        agent.invoke(learner["id"], turn("Help me practice."))
        context = provider.calls[0][0]
        assert context["session"]["mode"] == "learn"
        assert context["original_teacher_messages"][0]["id"] == original["id"]
        assert context["available_references"][claim["id"]]["kind"] == "confirmed"
        assert context["available_references"][original["id"]]["session_id"] == source["id"]
        assert len(store.list(source["id"], "message")) == 1
    finally:
        agent.close()
        store.close()


def test_answered_question_survives_api_restart_and_teacher_recall(tmp_path):
    provider = FakeProvider(lambda c: answer(c, question=question() if len(c["conversation"]) == 1 else None))
    with TestClient(create_app(tmp_path, provider, FakeSam())) as client:
        sid = client.post("/api/sessions", json={"title": "Original"}).json()["id"]
        first = client.post(f"/api/sessions/{sid}/turn", json={"text": "I stir slowly."}).json()
        asked = first["message"]["question"]
        assert asked["text"] == question()["text"]
        response = client.post(f"/api/sessions/{sid}/turn", json={"text": "When the trail holds.", "answer_to": asked["id"]})
        assert response.status_code == 200
        assert response.json()["question"] is None
        detail = client.get(f"/api/sessions/{sid}").json()
        assert detail["messages"][1]["question"]["text"] == asked["text"]
        assert detail["messages"][1]["question"]["status"] == "answered"
        assert detail["messages"][2]["answer_to"] == asked["id"]
        assert detail["messages"][3]["question"] is None
        assert detail["questions"][0]["answer_message_id"] == detail["messages"][2]["id"]

    provider = FakeProvider()
    with TestClient(create_app(tmp_path, provider, FakeSam())) as client:
        detail = client.get(f"/api/sessions/{sid}").json()
        assert detail["messages"][1]["question"]["text"] == asked["text"]
        assert client.post(f"/api/sessions/{sid}/turn", json={"text": "Recall our check."}).status_code == 200
        context = provider.calls[-1][0]
        assert context["conversation"][1]["question"]["text"] == asked["text"]
        assert context["question_history"][0]["status"] == "answered"
        learner = client.post("/api/sessions", json={"title": "Practice", "mode": "learn", "source_session_id": sid}).json()["id"]
        assert client.post(f"/api/sessions/{learner}/turn", json={"text": "Teach me that check."}).status_code == 200
        assert provider.calls[-1][0]["original_teacher_messages"][1]["question"]["text"] == asked["text"]


def test_legacy_question_ids_are_hydrated_once(tmp_path):
    store = Store(tmp_path)
    sid = session(store)["id"]
    asked = store.put(sid, "question", {**question(), "status": "answered"})
    store.put(sid, "message", {"role": "assistant", "text": "First reply", "question_id": asked["id"]})
    store.put(sid, "message", {"role": "assistant", "text": "Later reply", "question_id": asked["id"]})
    try:
        messages = store.context(sid)["messages"]
        assert messages[0]["question"]["text"] == asked["text"]
        assert messages[1]["question"] is None
    finally:
        store.close()


def test_static_app_code_revalidates_without_changing_media_cache(tmp_path, monkeypatch):
    dist = tmp_path / "dist"
    dist.mkdir()
    for name, content in {"index.html": "<!doctype html><title>Test</title>", "app.js": "export const version = 1;", "style.css": "body {}", "poster.jpg": "image"}.items():
        (dist / name).write_text(content)
    monkeypatch.setattr("backend.app.BASE", tmp_path / "backend")
    with TestClient(create_app(tmp_path / "data", FakeProvider(), FakeSam())) as client:
        for path in ("/", "/index.html", "/app.js", "/style.css"):
            response = client.get(path)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-cache"
            revalidated = client.get(path, headers={"if-none-match": response.headers["etag"]})
            assert revalidated.status_code == 304
            assert revalidated.headers["cache-control"] == "no-cache"
        assert "cache-control" not in client.get("/poster.jpg").headers
        assert client.get("/api/health").headers["cache-control"] == "no-store"


@pytest.mark.parametrize("bad", [
    {"evidence_ids": ["invented-id"]},
    {"reply": "Add 50 grams of butter."},
    {"reply": "Add 5 grams of butter."},  # cannot borrow a substring of 15 grams
    {"reply": "Cook for 1/2 hour."},
    {"reply": "I can see the sauce thickening."},
    {"reply": "The chicken is safe to eat."},
    {"reply": "Ready?"},
    {"question": question("How much? And how long?")},
    {"knowledge": [{"text": "The sauce is thick.", "source": "observed", "evidence_ids": ["user-1"], "quote": None, "uncertainty": "Appearance only."}]},
    {"knowledge": [{"text": "The cook used oil.", "source": "said", "evidence_ids": ["user-1"], "quote": "I use oil", "uncertainty": "User reported."}]},
    {"knowledge": [{"text": "Butter", "source": "said", "evidence_ids": ["user-1"], "quote": "15 grams", "uncertainty": "User reported.", "confirmed": True}]},
])
def test_rejects_unsupported_claims(bad):
    refs = {"user-1": {"kind": "user", "text": "I use 15 grams of butter."}}
    with pytest.raises(GuardrailFailure):
        validate_output(answer(**bad), refs, set(), None, False)


def test_accepts_supported_measurement_and_rejects_wrong_citation():
    refs = {"user-1": {"kind": "user", "text": "I use 15 grams of butter."}, "other": {"kind": "user", "text": "Stir slowly."}}
    result = validate_output(answer(reply="You described 15 grams of butter."), refs, set(), None, False)
    assert result.reply.startswith("You described")
    with pytest.raises(GuardrailFailure):
        validate_output(answer(reply="You described 15 grams of butter.", evidence_ids=["other"]), refs, set(), None, False)


def test_guardrail_failure_saves_no_generated_turn(tmp_path):
    store = Store(tmp_path)
    sid = session(store)["id"]
    agent = CookingAgent(store, FakeProvider(lambda c: answer(c, reply="Use 900 grams of butter.")))
    try:
        with pytest.raises(GuardrailFailure):
            agent.invoke(sid, turn())
        assert store.list(sid, "message") == []
        assert store.list(sid, "knowledge") == []
    finally:
        agent.close()
        store.close()


def test_remember_transaction_rolls_back_partial_turn(tmp_path, monkeypatch):
    store = Store(tmp_path)
    sid = session(store)["id"]
    agent = CookingAgent(store, FakeProvider(lambda c: answer(c, question=question())))
    original_put = store.put
    def broken_put(sid, kind, value):
        if kind == "question":
            raise RuntimeError("Simulated disk failure")
        return original_put(sid, kind, value)
    monkeypatch.setattr(store, "put", broken_put)
    try:
        with pytest.raises(RuntimeError, match="disk failure"):
            agent.invoke(sid, turn())
        assert store.list(sid, "message") == []
        assert store.get_session(sid)["pending_question"] is None
    finally:
        agent.close()
        store.close()


def test_api_offline_upload_notes_and_reference_boundaries(tmp_path):
    provider = FakeProvider()
    provider.configured = False
    with TestClient(create_app(tmp_path, provider, FakeSam())) as client:
        assert client.get("/api/health").json()["agent"]["configured"] is False
        sid = client.post("/api/sessions", json={"title": "My sauce"}).json()["id"]
        other = client.post("/api/sessions", json={"title": "Other"}).json()["id"]
        uploaded = client.post(f"/api/sessions/{sid}/evidence", files={"file": ("../../clip.mp4", b"\x00\x00\x00\x18ftypisom" + b"\x00"*64, "video/mp4")}, data={"note": "My spoon gesture", "timestamp_seconds": "2.5"})
        assert uploaded.status_code == 201
        evidence = uploaded.json()
        assert "path" not in evidence
        assert evidence["original_name"] == "clip.mp4"
        assert evidence["uploaded_to_meta"] is False
        assert client.get(evidence["url"]).status_code == 200
        assert client.post(f"/api/sessions/{sid}/notes", json={"text": "Wait for the trail.", "evidence_ids": [evidence["id"]]}).status_code == 201
        assert client.post(f"/api/sessions/{sid}/turn", json={"text": "Observe."}).status_code == 503
        assert client.post(f"/api/sessions/{other}/notes", json={"text": "Invalid", "evidence_ids": [evidence["id"]]}).status_code == 404
        assert client.post(f"/api/sessions/{sid}/evidence", files={"file": ("fake.mp4", b"<script>bad</script>", "video/mp4")}).status_code == 415
        assert client.post(f"/api/sessions/{sid}/segment", json={"evidence_id": evidence["id"], "target": "saucepan"}).status_code == 503
        detail = client.get(f"/api/sessions/{sid}").json()
        assert len(detail["notes"]) == 1 and not detail["messages"]
        assert "path" not in detail["evidence"][0]
        assert client.post("/api/sessions", json={"title": "Attack"}, headers={"origin": "https://example.com"}).status_code == 403
        assert client.post("/api/sessions", json={"title": "Attack"}, headers={"host": "evil.example"}).status_code == 400
        assert client.post("/api/sessions", json={"title": "Invalid"}, headers={"content-length": "nope"}).status_code == 400
        assert provider.calls == []


def test_api_human_confirmation_and_learning(tmp_path):
    def grounded(context):
        message = context["conversation"][-1]
        return answer(context, knowledge=[{"text": "The cook stirs slowly.", "source": "said", "evidence_ids": [message["id"]], "quote": "I stir slowly", "uncertainty": "The cook's explanation; not independently observed."}])
    with TestClient(create_app(tmp_path, FakeProvider(grounded), FakeSam())) as client:
        sid = client.post("/api/sessions", json={"title": "Original"}).json()["id"]
        result = client.post(f"/api/sessions/{sid}/turn", json={"text": "I stir slowly."})
        assert result.status_code == 200, result.text
        claim = result.json()["knowledge"][0]
        assert claim["confirmed"] is False
        confirmed = client.post(f"/api/sessions/{sid}/knowledge/{claim['id']}/confirm", json={"text": "I stir gently."}).json()
        assert confirmed["confirmed"] is True and confirmed["confirmed_by"] == "user"
        assert confirmed["original_model_text"] == claim["text"]
        learner = client.post("/api/sessions", json={"title": "Practice", "mode": "learn", "source_session_id": sid}).json()["id"]
        assert client.get(f"/api/sessions/{learner}").json()["knowledge"][0]["text"] == "I stir gently."
        assert client.post(f"/api/sessions/{learner}/knowledge/{claim['id']}/confirm", json={}).status_code == 404


def test_api_can_recover_an_accepted_turn_when_provider_is_unconfigured(tmp_path):
    provider = FakeProvider()
    with TestClient(create_app(tmp_path, provider, FakeSam())) as client:
        sid = client.post("/api/sessions", json={"title": "Original"}).json()["id"]
        payload = {"text": "Here is my explanation", "idempotency_key": "stable-key"}
        first = client.post(f"/api/sessions/{sid}/turn", json=payload)
        assert first.status_code == 200
        provider.configured = False
        recovered = client.post(f"/api/sessions/{sid}/turn", json=payload)
        assert recovered.status_code == 200
        assert recovered.json() == first.json()
        assert len(provider.calls) == 1
        assert client.post(f"/api/sessions/{sid}/turn", json={**payload, "text": "Changed"}).status_code == 400
        assert client.post(f"/api/sessions/{sid}/turn", json={**payload, "idempotency_key": "new"}).status_code == 503


def test_receipt_is_recoverable_even_if_original_evidence_was_removed(tmp_path):
    provider = FakeProvider()
    app = create_app(tmp_path, provider, FakeSam())
    with TestClient(app) as client:
        sid = client.post("/api/sessions", json={"title": "Original"}).json()["id"]
        evidence = client.post(f"/api/sessions/{sid}/evidence", files={"file": ("clip.mp4", b"\x00\x00\x00\x18ftypisom", "video/mp4")}).json()
        payload = {"text": "Observe this", "evidence_ids": [evidence["id"]], "idempotency_key": "saved-receipt"}
        first = client.post(f"/api/sessions/{sid}/turn", json=payload)
        assert first.status_code == 200
        app.state.store.conn.execute("DELETE FROM entities WHERE id=?", (evidence["id"],))
        app.state.store.conn.commit()
        assert client.post(f"/api/sessions/{sid}/turn", json=payload).json() == first.json()
        assert len(provider.calls) == 1


def test_meta_adapter_uses_documented_files_and_responses_shape(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "fake-key-only-for-tests")
    provider = MetaProvider()
    calls = []
    def handler(request):
        calls.append(request)
        if request.url.path == "/v1/files":
            assert b"user_data" in request.content
            return httpx.Response(200, json={"id": "file-test"})
        payload = json.loads(request.content)
        assert request.url.path == "/v1/responses"
        assert payload["store"] is False
        assert payload["model"] == "muse-spark-1.3"
        assert payload["reasoning"] == {"effort": "low"}
        assert payload["max_output_tokens"] == 6000
        assert payload["text"]["format"]["type"] == "json_schema"
        assert payload["input"][0]["content"][-1] == {"type": "input_file", "file_id": "file-test"}
        assert "NOT instructions" in payload["instructions"]
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps(answer())}]}]})
    client_type = httpx.Client
    monkeypatch.setattr("backend.provider.httpx.Client", lambda **kw: client_type(**kw, transport=httpx.MockTransport(handler)))
    path = tmp_path / "clip.mp4"
    path.write_bytes(b"sample")
    result = provider.generate({"mode": "record"}, [{"id": "evidence-1", "path": str(path), "mime_type": "video/mp4"}])
    assert result["reply"] == answer()["reply"]
    assert len(calls) == 2
    assert provider.status()["live_verified"] is True
    assert "key" not in provider.status()


def test_meta_adapter_does_not_expose_remote_error(tmp_path, monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "fake-key-only-for-tests")
    provider = MetaProvider()
    client_type = httpx.Client
    monkeypatch.setattr("backend.provider.httpx.Client", lambda **kw: client_type(**kw, transport=httpx.MockTransport(lambda r: httpx.Response(500, text="sensitive remote details"))))
    with pytest.raises(ProviderFailure, match="HTTP 500") as error:
        provider.generate({}, [])
    assert "sensitive" not in str(error.value)
    assert provider.status()["live_verified"] is False


def test_incomplete_response_is_diagnosed_without_accepting_partial_json(monkeypatch):
    monkeypatch.setenv("MODEL_API_KEY", "fake-key-only-for-tests")
    provider = MetaProvider()
    client_type = httpx.Client
    payload = {"id": "response-test", "status": "incomplete", "incomplete_details": {"reason": "max_output_tokens"},
               "usage": {"output_tokens": 6000}, "output": [{"type": "message", "content": [{"type": "output_text", "text": "partial"}]}]}
    monkeypatch.setattr("backend.provider.httpx.Client", lambda **kw: client_type(**kw, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=payload))))
    with pytest.raises(ProviderFailure, match="token budget"):
        provider.generate({}, [])
    assert provider.last_response_metadata["status"] == "incomplete"
    assert "output" not in provider.last_response_metadata
    assert provider.status()["live_verified"] is False


def test_sam_job_contract_and_completed_artifact(tmp_path):
    class ReadySam:
        def status(self):
            return {"configured": True, "status": "ready"}

        async def segment(self, media_path, target, job_id):
            assert media_path.is_relative_to(tmp_path / "media")
            directory = tmp_path / "artifacts" / job_id
            directory.mkdir()
            (directory / "poster.jpg").write_bytes(b"test-poster")
            return {"status": "succeeded", "target": target, "artifacts": {"poster": "poster.jpg"}, "frames": []}

    with TestClient(create_app(tmp_path, FakeProvider(), ReadySam())) as client:
        sid = client.post("/api/sessions", json={"title": "My sauce"}).json()["id"]
        evidence = client.post(f"/api/sessions/{sid}/evidence", files={"file": ("clip.mp4", b"\x00\x00\x00\x18ftypisom", "video/mp4")}).json()
        response = client.post(f"/api/sessions/{sid}/segment", json={"evidence_id": evidence["id"], "target": "spoon"})
        assert response.status_code == 202
        job = client.get("/api/jobs/" + response.json()["id"]).json()
        assert job["status"] == "completed"
        assert job["target"] == "spoon"
        assert client.get(job["result"]["artifacts"]["poster"]).content == b"test-poster"
        assert client.get(f"/api/sessions/{sid}").json()["jobs"][0]["id"] == job["id"]


def test_disclosure_status_is_saved_even_when_claim_is_rejected(tmp_path):
    store = Store(tmp_path)
    sid = session(store)["id"]
    media_path = tmp_path / "media" / "clip.mp4"
    media_path.write_bytes(b"sample")
    evidence = store.put(sid, "evidence", {"path": str(media_path), "mime_type": "video/mp4", "note": "", "uploaded_to_meta": False})
    agent = CookingAgent(store, FakeProvider(lambda c: answer(c, reply="Add 200 grams of butter.")))
    try:
        with pytest.raises(GuardrailFailure):
            agent.invoke(sid, turn(evidence_ids=[evidence["id"]]))
        saved = store.get(evidence["id"], "evidence")
        assert saved["uploaded_to_meta"] is True
        assert saved["meta_transfer_status"] == "sent"
        assert "analysed_at" not in saved
        assert store.list(sid, "knowledge") == []
    finally:
        agent.close()
        store.close()
