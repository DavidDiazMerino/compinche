import json

import pytest

from backend.agent import CookingAgent, GuardrailFailure, validate_output
from backend.replay import caption_words, transcript_until, reuse_segments
from backend.segmentation_context import summarise_job
from backend.store import Store
from backend.models import SegmentRequest


def test_rolling_captions_are_deduplicated_and_do_not_leak_future_words(tmp_path):
    path = tmp_path / "sample.vtt"
    path.write_text('''WEBVTT

00:00:01.000 --> 00:00:03.000
Mueve<00:00:02.000><c> despacio</c>

00:00:03.000 --> 00:00:06.000
Mueve despacio
y<00:00:04.000><c> añade</c><00:00:05.000><c> leche</c>
''')
    words = caption_words(path)
    assert transcript_until(words, 1, 4) == "Mueve despacio y"
    assert transcript_until(words, 4, 5) == "añade"
    assert "leche" not in transcript_until(words, 1, 5)


def test_segmentation_samples_keep_the_media_identity_and_two_time_origins():
    job = {"id": "job123", "evidence_id": "clip123", "target": "hand", "result": {
        "width": 100, "height": 50, "source_start_seconds": 12, "frame_count": 32,
        "frames": [{"seconds": n/4, "objects": [{"id": 4, "pixels": 500, "score": .8, "bbox": [1,2,20,25]}]} for n in range(32)]}}
    result = summarise_job(job, {"timestamp_seconds": 320})
    assert result["evidence_id"] == "clip123"
    assert len(result["samples"]) <= 9
    assert result["samples"][0]["media_seconds"] == 12
    assert result["samples"][0]["original_seconds"] == 332
    assert result["samples"][-1]["original_seconds"] == 339.75
    assert result["samples"][0]["objects"][0]["frame_area_fraction"] == .1


def test_spanish_measurements_require_the_exact_cited_source():
    refs = {"transcript1": {"kind": "transcript", "text": "añado 50 gramos de harina"}}
    output = {"reply": "La transcripción automática dice 50 gramos.", "evidence_ids": ["transcript1"],
              "question": None, "knowledge": [], "segmentation_targets": [], "uncertainty": ["Subtítulos sin verificar."]}
    assert validate_output(output, refs, set(), None, False)
    with pytest.raises(GuardrailFailure):
        validate_output({**output, "reply": "Añade 90 gramos de harina."}, refs, set(), None, False)
    with pytest.raises(GuardrailFailure):
        validate_output({**output, "knowledge": [{"text": "usa harina", "source": "said", "evidence_ids": ["transcript1"],
                        "quote": "200 gramos", "uncertainty": "Subtítulos sin verificar."}]}, refs, set(), None, False)


def test_segment_request_rejects_nonfinite_and_negative_start():
    assert SegmentRequest(evidence_id="clip", target="croquette", start_seconds=491).start_seconds == 491
    for start in [-1, float("nan"), float("inf")]:
        with pytest.raises(ValueError):
            SegmentRequest(evidence_id="clip", start_seconds=start)


@pytest.mark.parametrize("text", [
    "¿Qué tamaño coges y cómo sabes que está compacta?",
    "What portion do you take and when do you stop shaping?",
])
def test_one_question_mark_pair_cannot_hide_two_questions(text):
    output = {"reply": "He observado el gesto.", "evidence_ids": ["user1"],
              "question": {"text": text, "reason": "Falta un criterio.", "evidence_ids": ["user1"]},
              "knowledge": [], "segmentation_targets": [], "uncertainty": []}
    with pytest.raises(GuardrailFailure, match="combined"):
        validate_output(output, {"user1": {"kind": "user", "text": "Observa"}}, set(), None, False)


def test_reusing_verified_masks_preserves_both_session_identities(tmp_path):
    store = Store(tmp_path)
    source_sid = store.create_session({"title": "Original", "mode": "record"})["id"]
    target_sid = store.create_session({"title": "Revision", "mode": "record"})["id"]
    clip = tmp_path / "media" / "clip.mp4"
    clip.write_bytes(b"sample")
    job = store.put(source_sid, "job", {"evidence_id": "source-evidence", "target": "hand", "status": "completed",
                                       "result": {"source_sha256": "original", "artifacts": {"overlay": "/api/artifacts/source/overlay.mp4"}}})
    original = {"pixel_sha256": "same-pixels", "sam_jobs": [job]}
    case = {"pixel_sha256": "same-pixels", "clip": str(clip), "session_id": target_sid,
            "evidence_id": "revision-evidence", "sam_jobs": []}
    try:
        reuse_segments(store, original, case)
        reuse_segments(store, original, case)
        assert len(case["sam_jobs"]) == 1
        saved = case["sam_jobs"][0]
        assert saved["id"] != job["id"]
        assert saved["evidence_id"] == "revision-evidence"
        assert saved["session_id"] == target_sid
        assert store.get(job["id"], "job")["session_id"] == source_sid
        with pytest.raises(ValueError, match="differ"):
            reuse_segments(store, original, {**case, "pixel_sha256": "different-pixels"})
    finally:
        store.close()


def test_graph_receives_pixel_evidence_without_promoting_captions_or_leaking_other_sessions(tmp_path):
    store = Store(tmp_path)
    session = store.create_session({"title": "Audio", "mode": "record"})
    sid = session["id"]
    store.put(sid, "evidence", {"id": "clip123", "timestamp_seconds": 320, "note": "Public demonstration"})
    store.put(sid, "note", {"text": "ya está lista", "source": "transcript", "confirmed": False, "provenance": "automatic captions"})
    store.put(sid, "job", {"evidence_id": "clip123", "status": "completed", "target": "spoon", "start_seconds": 12,
                          "result": {"frames": [], "frame_count": 0}})
    class InspectProvider:
        model = "test-only"
        def generate(self, context, media):
            self.context = context
            return {"reply": "Observo el fragmento.", "evidence_ids": ["clip123"],
                    "question": {"text": "¿Qué señal usarías para dejar de remover?", "reason": "Criterio no visible.", "evidence_ids": ["clip123"]},
                    "knowledge": [], "segmentation_targets": [], "uncertainty": []}
    provider = InspectProvider()
    agent = CookingAgent(store, provider)
    try:
        result = agent.invoke(sid, {"text": "Observa", "evidence_ids": ["clip123"]})
        assert result["question"]["media_anchors"][0]["original_start_seconds"] == 320
        refs = provider.context["available_references"]
        assert any(ref["kind"] == "transcript" for ref in refs.values())
        assert provider.context["user_notes"][0]["confirmed"] is False
        assert provider.context["sam_results"][0]["evidence_id"] == "clip123"
        silent = store.create_session({"title": "Silent", "mode": "record"})
        state = agent.retrieve({"session_id": silent["id"], "request": {"evidence_ids": []},
                                "user_message": {"id": "user123", "role": "user", "text": "Sin audio"}})
        assert state["context"]["user_notes"] == []
        assert state["context"]["sam_results"] == []
    finally:
        agent.close()
        store.close()
