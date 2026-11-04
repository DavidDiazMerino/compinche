"""Attach a teacher's voice note, recorded after the session, to one of Compinche's questions.

Live answers only count while the question is open (see LiveCoordinator.transcript). When the
teacher cannot cook again, they can answer later, for example with a phone voice note. This tool
attaches that recording to the question it answers. It keeps the audio as evidence, saves the
transcript as the teacher's note, marks the question answered and derives the card with the same
prompt as a live answer. The quote is the transcript, never the model's wording.

The card stays unconfirmed unless --confirmed-by names who checked it. That confirmation is
declared by the operator, not clicked in the app, and the card records it as such.

    python -m backend.voice_answer --data backend/.data/family-demo --session SID \
        --question db0676be --audio nota.wav --speaker Papá --text "Ingredientes: …" \
        --confirmed-by "Papá (nota de voz)" --recipe --env-file backend/.env.local
"""
import argparse
import asyncio
from pathlib import Path
import shutil

from .live import invents_amount
from .store import Store, now, uid

DERIVED = ("summary", "signal", "reported_sensation", "decision", "category", "title")


def find_question(store, sid, prefix):
    matches = [q for q in store.list(sid, "question") if q["id"].startswith(prefix)]
    if len(matches) != 1:
        raise SystemExit(f"Question '{prefix}' matches {len(matches)} questions in session {sid}.")
    return matches[0]


async def attach(store, provider, sid, question, audio, *, text=None, speaker, role="cook", language="es",
                 confirmed_by=None, recipe=False, media_dir):
    """Attach one voice note to one question. Returns the card."""
    audio = Path(audio)
    transcription = {"text_source": "provided", "human_verified": True}
    if not text:
        # Muse Voice Transcribe takes turns of up to 35 s; longer notes need --text.
        duration = _duration(audio)
        result = await provider.live_transcribe(audio, duration, language=language)
        text = result["text"]
        transcription = {"text_source": result["model"], "human_verified": False}
    text = text.strip()
    if not text:
        raise ValueError("The voice note has no words to attach.")
    dst = Path(media_dir) / f"answer-{uid()}{audio.suffix or '.wav'}"
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy(audio, dst)
    evidence = store.put(sid, "evidence", {"kind": "audio", "path": str(dst), "mime_type": "audio/wav",
        "note": f"Nota de voz de {speaker} respondiendo a la pregunta de Compinche", "created_by": "voice_note"})
    note = store.put(sid, "note", {"text": text, "source": "transcript", "confirmed": True,
        "timestamp_seconds": question["source_end_seconds"], "end_seconds": question["source_end_seconds"],
        "scene_epoch": question.get("scene_epoch", 0), "speaker": speaker, "role": role,
        "speaker_verification": "user_declared", "origin": "voice_note", "automatic": True, "evidence_ids": [evidence["id"]],
        "provenance": {"capture": "voice_note", "answered_after_session": True, **transcription}})
    store.put(sid, "question", {**question, "status": "answered", "answered_at": now(), "answer_note_id": note["id"],
        "answer_speaker": speaker, "answer_role": role, "answered_by": "voice_note"})
    derived = await provider.live_learn({"question": question["text"], "action": question.get("action"),
        "observed_cue": question.get("cue"), "human_explanation": text, "speaker": speaker, "language": language})
    derived_by = getattr(provider, "last_text_model", None) or getattr(provider, "model", None)
    confirmation = ({"confirmed": True, "confirmed_by": confirmed_by, "confirmed_at": now(),
                     "reviewer_verification": "user_declared"} if confirmed_by else {"confirmed": False})
    card = store.put(sid, "knowledge", {"text": text, "source": "said", "quote": text, **confirmation,
        "created_by": "voice_note_answer", "speaker": speaker, "speaker_role": role, "question_id": question["id"],
        "evidence_ids": [note["id"], evidence["id"], *question.get("evidence_ids", [])][:6],
        "decision_card": {"action": question.get("action"), "observed_cue": question.get("cue"), "explanation": text,
            "question": question["text"], "speaker": speaker, "role": role,
            "demonstration_evidence_ids": question.get("evidence_ids", []), "answer_evidence_ids": [evidence["id"]],
            "answer_note_id": note["id"], "source_start_seconds": question["source_start_seconds"],
            "source_end_seconds": question["source_end_seconds"], "scene_epoch": question.get("scene_epoch", 0),
            "confirmed": bool(confirmed_by), **{k: derived.get(k) for k in DERIVED},
            "derived_by": derived_by, "derivation_confirmed": False}})
    if recipe:
        session = store.get_session(sid)
        live = session.get("live", {})
        config = live.get("config", {})
        merged = await provider.live_recipe({"seen": config.get("recipe"), "human_reply": text,
                                             "language": config.get("language", language)})
        for item in merged.get("ingredients", []):  # a quantity must come from the teacher's words, as in a live session
            if item.get("quantity") and invents_amount([item["quantity"]], text):
                item["quantity"] = None
        store.update_session(sid, live={**live, "config": {**config, "recipe": {**(config.get("recipe") or {}), **merged}}})
    return card


def _duration(path):
    import json
    import subprocess
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
                            capture_output=True, text=True, check=True, timeout=30)
    return float(json.loads(result.stdout)["format"]["duration"])


async def main(args):
    from .live_provider import LiveMetaProvider
    from .provider import MetaProvider
    from .replay import load_env
    load_env(args.env_file)
    store = Store(args.data)
    provider = LiveMetaProvider(MetaProvider())
    try:
        question = find_question(store, args.session, args.question)
        if question.get("status") == "answered" and not args.force:
            raise SystemExit(f"Question {question['id'][:8]} is already answered; pass --force to add another answer.")
        card = await attach(store, provider, args.session, question, args.audio, text=args.text, speaker=args.speaker,
            role=args.role, language=args.language, confirmed_by=args.confirmed_by, recipe=args.recipe,
            media_dir=Path(args.data) / "media")
        state = "confirmed by " + args.confirmed_by if args.confirmed_by else "unconfirmed"
        print(f"CARD {card['id'][:8]} [{card['decision_card'].get('category')}] {card['decision_card'].get('title')} ({state})")
    finally:
        await provider.close()
        store.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data", type=Path, required=True, help="Store folder, e.g. backend/.data/family-demo")
    parser.add_argument("--session", required=True)
    parser.add_argument("--question", required=True, help="Question id or its first characters")
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--text", help="Checked transcript. Without it, Muse Voice Transcribe is used (notes up to 35 s)")
    parser.add_argument("--speaker", required=True)
    parser.add_argument("--role", default="cook")
    parser.add_argument("--language", default="es", help="Language the teacher speaks")
    parser.add_argument("--confirmed-by", help="Who checked the card; without it the card stays unconfirmed")
    parser.add_argument("--recipe", action="store_true", help="Also merge ingredients and quantities into the recipe")
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--env-file", type=Path)
    asyncio.run(main(parser.parse_args()))
