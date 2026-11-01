"""Reproduce three review probes without providers, media or application data.

From application/:
backend/.venv/bin/python docs/reviews/2026-10-31/verificar_hallazgos.py

Prints the behavior of the current code; does not assert that it is desirable.
"""
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from backend.agent import CookingAgent, GuardrailFailure, validate_output
from backend.store import Store


def main():
    common = {
        "evidence_ids": [], "question": None, "knowledge": [],
        "segmentation_targets": [], "uncertainty": [],
    }
    references = {"note-1": {"kind": "note", "text": "Estamos friendo las croquetas."}}
    cases = {
        "spanish_visual_claim_without_media": {
            **common, "reply": "Veo en el vídeo que la bechamel está lista.",
        },
        "unsupported_temperature_with_ordinal_symbol": {
            **common, "reply": "Entendido.", "knowledge": [{
                "text": "El aceite está a 180 ºC.", "source": "inferred",
                "evidence_ids": ["note-1"], "quote": None,
                "uncertainty": "Hipótesis sin medir.",
            }],
        },
    }
    results = {}
    for name, output in cases.items():
        try:
            validate_output(output, references, set(), None, False)
            results[name] = "accepted"
        except GuardrailFailure:
            results[name] = "rejected"

    with TemporaryDirectory(prefix="compinche-review-") as directory:
        store = Store(Path(directory))
        teacher = store.create_session({"title": "Teacher", "mode": "record"})["id"]
        learner = store.create_session({
            "title": "Learner", "mode": "learn", "source_session_id": teacher,
        })["id"]
        for number in range(101):
            store.put(teacher, "knowledge", {
                "id": f"teacher-{number}", "text": f"Original decision {number}",
                "confirmed": True,
            })
        store.put(learner, "knowledge", {
            "id": "learner-correction", "text": "My pan behaves differently",
            "confirmed": True,
        })
        agent = CookingAgent(store, object())
        try:
            state = agent.retrieve({
                "session_id": learner, "request": {"evidence_ids": []},
                "user_message": {"id": "new-message", "role": "user", "text": "Review my correction"},
            })
            results["context_with_101_teacher_claims"] = {
                "knowledge_count": len(state["context"]["knowledge"]),
                "learner_correction_present": any(
                    item["id"] == "learner-correction"
                    for item in state["context"]["knowledge"]
                ),
                "learner_reference_present": "learner-correction" in state["references"],
            }
        finally:
            agent.close()
            store.close()
    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
