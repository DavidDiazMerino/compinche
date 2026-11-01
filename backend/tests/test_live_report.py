"""Export regressions with simulated tools; no camera/audio latency claims."""
import hashlib

from backend.live_provider import LiveMetaProvider, PROMPT, LEARN_PROMPT
from backend.live_report import provider_configuration
from backend.tests.test_live_api import api


def report(api, sid, path="diagnostics", **params):
    response = api.client.get(f"/api/live/{sid}/{path}", params=params)
    assert response.status_code == 200, response.text
    return response


def test_diagnostics_explicitly_marks_missing_measurements_and_never_exports_key(api):
    api.provider.key = "secret-only-in-test-provider"
    sid = api.create()
    response = report(api, sid, download=True)
    data = response.json()
    assert data["schema_version"] == "compinche.live.diagnostics.v1"
    assert "attachment;" in response.headers["content-disposition"]
    assert "secret-only" not in response.text
    assert data["configuration"]["provider_at_start"]["model"] == api.provider.model
    assert data["configuration"]["provider_at_start"]["vision_reasoning_effort"] is None
    assert data["latencies"]["capture_to_audio_ms"]["status"] == "not_measured"
    assert data["latencies"]["capture_to_audio_ms"]["value"] is None
    assert data["latencies"]["vision_ms"]["count"] == 0
    assert data["latencies"]["vision_ms"]["mean"] is None
    assert data["playback_verification"]["physical_audibility_verified"] is False
    assert data["events_complete"] is True and data["event_count"] == 1
    assert api.snap(sid)["metric_definitions"]["last_question_ready_ms"]["clock"] == "server_monotonic"
    assert api.client.get("/api/live/nonexistent/diagnostics").status_code == 404
    assert api.client.get(f"/api/live/{sid}/learning-resource?format=exe").status_code == 422


def test_provider_configuration_records_actual_effort_and_prompt_hash_without_secrets():
    class Base:
        key = "private-test-credential"
        model = "test-spark-model"
    config = provider_configuration(LiveMetaProvider(Base()))
    assert config["vision_reasoning_effort"] == "minimal"
    assert config["learning_reasoning_effort"] == "low"
    assert config["vision_frame_limit"] == 2
    assert config["vision_prompt_sha256"] == hashlib.sha256(PROMPT.encode()).hexdigest()
    assert config["learning_prompt_sha256"] == hashlib.sha256(LEARN_PROMPT.encode()).hexdigest()
    assert "private-test-credential" not in repr(config)


def test_replay_export_records_pause_and_original_audio_preferences(api):
    source = api.store.create_session({"title": "Original", "mode": "record"})
    original = api.store.put(source["id"], "evidence", {"kind": "video", "duration_seconds": 60})
    sid = api.create(source_kind="replay", source_evidence_id=original["id"],
                     replay_pause_on_question=True, monitor_original_audio=True)
    config = report(api, sid).json()["configuration"]["session"]
    assert config["replay_pause_on_question"] is True
    assert config["monitor_original_audio"] is True
    continuous = api.create(source_kind="replay", source_evidence_id=original["id"])
    config = report(api, continuous).json()["configuration"]["session"]
    assert config["replay_pause_on_question"] is False
    assert config["monitor_original_audio"] is False


def test_diagnostics_distinguishes_ready_voice_preparation_ack_and_source_clock(api):
    sid = api.create()
    question = api.question(sid)
    before = report(api, sid).json()
    assert before["latencies"]["admission_ms"]["count"] == 1
    assert before["latencies"]["queue_ms"]["count"] == 1
    assert before["latencies"]["vision_ms"]["count"] == 1
    assert before["latencies"]["question_ready_ms"]["count"] == 1
    assert before["latencies"]["admission_to_playback_ack_ms"]["count"] == 0
    assert before["questions"][0]["observation_id"] == before["observations"][0]["id"]
    assert before["observations"][0]["http_received_wall"] <= before["observations"][0]["received_wall"]
    assert before["source_lag_seconds"]["clock"] == "source_media"
    assert api.client.get(f"/api/live/{sid}/questions/{question['id']}/voice").status_code == 200
    prepared = report(api, sid).json()
    assert prepared["latencies"]["voice_preparation_ms"]["count"] == 1
    assert prepared["latencies"]["admission_to_playback_ack_ms"]["count"] == 0
    api.spoken(sid, question)
    acknowledged = report(api, sid).json()
    assert acknowledged["latencies"]["admission_to_playback_ack_ms"]["count"] == 1
    assert acknowledged["questions"][0]["admission_to_playback_ack_ms"] >= acknowledged["questions"][0]["admission_to_ready_ms"]
    assert acknowledged["latencies"]["capture_to_audio_ms"]["value"] is None
    assert any(e["type"] == "voice_prepared" for e in acknowledged["events"])
    assert "path" not in repr(acknowledged["evidence"])


def test_diagnostics_export_contains_all_events_and_only_own_session(api):
    sid = api.create()
    other = api.create()
    api.store.put(other, "note", {"text": "FOREIGN PRIVATE WORDS"})
    for seq in range(2, 263):
        api.store.put(sid, "live_event", {"type": "test_historical_event", "seq": seq,
            "scene_epoch": 0, "source_seconds": 0, "wall_time": 1700000000+seq, "data": {}})
    snapshot = api.snap(sid)
    assert len(snapshot["events"]) == 200 and snapshot["more_events"] is True
    response = report(api, sid)
    assert response.json()["event_count"] == 262
    assert response.json()["events"][-1]["id"] == 262
    assert "FOREIGN PRIVATE" not in response.text


def test_learning_export_preserves_literal_reviewed_text_and_tentative_derivation(api):
    sid = api.create()
    first = api.question(sid)
    api.spoken(sid, first)
    literal = '<script>alert("literal-only")</script> Cuando queda el surco.'
    card = api.typed(sid, first, text=literal).json()["card"]
    card["decision_card"].update(summary="Interpretación provisional", decision="Parar", derived_by="fake-test-model")
    api.store.put(sid, "knowledge", card)
    reviewed = "Texto revisado por la persona: parar cuando queda el surco."
    response = api.client.post(f"/api/sessions/{sid}/knowledge/{card['id']}/confirm", json={"text": reviewed})
    assert response.status_code == 200
    second = api.question(sid, seq=1, second=2)
    api.spoken(sid, second)
    assert api.typed(sid, second, utterance_id="second-explanation", text="Noto resistencia.", source_seconds=2).json()["answered"] is True
    data = report(api, sid, "learning-resource").json()
    assert data["counts"] == {"confirmed": 1, "needs_review": 1, "captured_explanations": 2}
    confirmed = data["confirmed"][0]
    assert confirmed["reviewed_text"] == reviewed
    assert confirmed["literal_explanation"] == literal
    assert confirmed["speaker"] == "David" and confirmed["speaker_verification"] == "user_declared"
    assert confirmed["derived_interpretation"]["summary"] == "Interpretación provisional"
    assert confirmed["derived_interpretation"]["confirmed"] is False
    assert confirmed["derived_interpretation"]["status"] == "needs_review"
    assert data["needs_review"][0]["reviewed_text"] is None
    html = report(api, sid, "learning-resource", format="html", download=True)
    assert "text/html" in html.headers["content-type"]
    assert "attachment;" in html.headers["content-disposition"]
    assert "<script>" not in html.text and "&lt;script&gt;" in html.text
    assert "http://testserver/api/media/" in html.text
    assert '<img loading="lazy"' in html.text
    assert "requieren que el servidor local siga activo" in html.text
    assert "Pendiente de revisión: no usar como instrucción" in html.text


def test_learning_export_recorded_audio_remains_reviewable_not_an_answer_or_rule(api):
    sid = api.create()
    question = api.question(sid)
    api.spoken(sid, question)
    assert api.audio(sid, origin="recorded_source", speaker_name="Papá", speaker_role="cook",
                     answer_to=question["id"]).status_code == 202
    api.until(sid, lambda s: s["metrics"]["utterances_received"] == 1 and not s["speech_pending"])
    data = report(api, sid, "learning-resource").json()
    assert data["confirmed"] == data["needs_review"] == []
    assert data["captured_explanations"][0]["origin"] == "recorded_source"
    assert data["captured_explanations"][0]["text"] == api.provider.audio_text
    assert data["open_questions"][0]["status"] == "spoken"
    assert data["captured_explanations"][0]["confirmed"] is False


def test_replay_learning_links_original_without_importing_old_analysis(api):
    source = api.store.create_session({"title": "Original", "mode": "record"})
    original = api.store.put(source["id"], "evidence", {"kind": "video", "duration_seconds": 60,
        "path": "/private/path/father.mp4", "remote_file_id": "remote-secret"})
    api.store.put(source["id"], "knowledge", {"text": "SECRET FUTURE RECIPE", "confirmed": True})
    sid = api.create(source_kind="replay", source_evidence_id=original["id"])
    api.question(sid)
    response = report(api, sid, "learning-resource", format="html")
    assert "Abrir grabación original" in response.text
    assert f'http://testserver/api/media/{original["id"]}' in response.text
    assert "vídeo 1.0–1.0 s · época 0" in response.text
    assert "SECRET FUTURE" not in response.text
    assert "/private/path" not in response.text
    assert "remote-secret" not in response.text
