from backend.agent import CookingAgent, session_window
from backend.store import Store


def test_long_teacher_history_does_not_evict_learner_correction_or_note(tmp_path):
    store = Store(tmp_path)
    teacher = store.create_session({"title": "Original", "mode": "record"})["id"]
    learner = store.create_session({"title": "Attempt", "mode": "learn", "source_session_id": teacher})["id"]
    for index in range(101):
        store.put(teacher, "knowledge", {"text": f"Original explanation {index}", "confirmed": True})
        store.put(teacher, "note", {"text": f"Original note {index}"})
    correction = store.put(learner, "knowledge", {"text": "My pan behaves differently", "confirmed": False})
    note = store.put(learner, "note", {"text": "This is my correction"})
    agent = CookingAgent(store, object())
    try:
        result = agent.retrieve({"session_id": learner, "request": {"evidence_ids": []},
                                 "user_message": {"id": "current", "role": "user", "text": "Help with my attempt"}})
        assert len(result["context"]["knowledge"]) == 100
        assert len(result["context"]["user_notes"]) == 100
        assert correction in result["context"]["knowledge"]
        assert note in result["context"]["user_notes"]
        assert result["references"][correction["id"]]["session_id"] == learner
        assert result["references"][correction["id"]]["kind"] == "inferred"
        assert result["references"][note["id"]]["session_id"] == learner
        assert result["context"]["context_omitted"]["knowledge"] == {learner: 0, teacher: 2}
    finally:
        agent.close()
        store.close()


def test_both_people_keep_recent_evidence_when_both_histories_are_long():
    items = [{"id": f"{owner}-{index}", "session_id": owner, "created_at": f"{index:04d}"}
             for owner in ("teacher", "learner", "unrelated") for index in range(120)]
    selected = session_window(items, "learner", "teacher")
    assert len(selected) == 100
    assert sum(item["session_id"] == "learner" for item in selected) == 50
    assert sum(item["session_id"] == "teacher" for item in selected) == 50
    assert {item["id"] for item in selected} >= {"learner-119", "teacher-119"}


def test_new_confirmation_of_old_claim_can_return_to_context():
    items = [{"id": str(index), "session_id": "teacher", "created_at": f"{index:04d}"} for index in range(120)]
    items[0]["confirmed_at"] = "9999"
    assert items[0] in session_window(items, "teacher")
    assert len(session_window(items[:2], "teacher")) == 2


def test_explicitly_attached_old_clip_is_not_evicted_by_newer_media(tmp_path):
    store = Store(tmp_path)
    teacher = store.create_session({"title": "Original", "mode": "record"})["id"]
    learner = store.create_session({"title": "Attempt", "mode": "learn", "source_session_id": teacher})["id"]
    attached = store.put(learner, "evidence", {"note": "My old attempt", "path": "/private/old.mp4"})
    for owner in (teacher, learner):
        for index in range(105):
            store.put(owner, "evidence", {"note": str(index), "path": "/private/new.mp4"})
    agent = CookingAgent(store, object())
    try:
        result = agent.retrieve({"session_id": learner, "request": {"evidence_ids": [attached["id"]]},
                                 "user_message": {"id": "current", "role": "user", "text": "Review this attempt"}})
        manifest = result["context"]["evidence_manifest"]
        assert len(manifest) == 100
        assert any(item["id"] == attached["id"] for item in manifest)
        assert sum(item["session_id"] == teacher for item in manifest) == 50
        assert all("path" not in item for item in manifest)
        assert result["media"] == [attached]
    finally:
        agent.close()
        store.close()
