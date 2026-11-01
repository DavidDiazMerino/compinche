"""Prepare the explicitly selected family video in ignored local storage.

Model calls require --live. All transcript notes and generated claims remain
unconfirmed. The original is copied, never edited or bundled into public assets.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import time
from uuid import uuid5, NAMESPACE_URL

from .agent import CookingAgent
from .provider import MetaProvider
from .replay import load_env, run_ffmpeg
from .store import Store, uid, now


def probe(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "json", str(path)], capture_output=True, text=True, check=True, timeout=30)
    return float(json.loads(result.stdout)["format"]["duration"])


def save_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def prepare(video, root, live=False):
    store = Store(root)
    try:
        return _prepare(video, root, live, store)
    finally:
        store.close()


def _prepare(video, root, live, store):
    video = video.resolve()
    with video.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    descriptor = root / "family-demo.json"
    previous = json.loads(descriptor.read_text()) if descriptor.exists() else None
    if previous and previous["source_sha256"] != digest:
        raise ValueError("This demo directory already belongs to another original. Choose a new data directory.")
    if previous:
        demo = previous
    else:
        duration = probe(video)
        sid = store.create_session({"title": "Dad’s recipe", "teacher": "Dad", "mode": "record", "source_session_id": None})["id"]
        eid, poster_id = uid(), uid()
        original = root / "media" / (eid + ".mp4")
        shutil.copyfile(video, original)
        evidence = store.put(sid, "evidence", {"id": eid, "kind": "video", "mime_type": "video/mp4",
            "path": str(original), "url": "/api/media/" + eid, "size": original.stat().st_size,
            "duration_seconds": duration, "timestamp_seconds": 0, "original_name": "family-original.mp4",
            "note": "The original recording of the user's father, explicitly selected for this local demo.",
            "uploaded_to_meta": False, "analysed_at": None, "capture_source": "family_recording"})
        poster = root / "media" / (poster_id + ".jpg")
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-ss", "5", "-i", str(original),
                        "-frames:v", "1", "-vf", "scale=854:-2", "-y", str(poster)], check=True, capture_output=True, timeout=30)
        store.put(sid, "evidence", {"id": poster_id, "kind": "image", "mime_type": "image/jpeg",
            "path": str(poster), "url": "/api/media/" + poster_id, "size": poster.stat().st_size,
            "note": "Poster extracted from the real original recording.", "role": "poster"})
        demo = {"version": 1, "session_id": sid, "original_evidence_id": evidence["id"],
            "poster_url": "/api/media/" + poster_id, "source_sha256": digest, "duration_seconds": duration,
            "title": "Dad’s recipe", "clips": [], "transcription_model": None, "analysis": []}
        save_json(descriptor, demo)
    provider = MetaProvider()
    original = store.get(demo["original_evidence_id"], "evidence")
    if not original or not Path(original["path"]).is_file():
        raise ValueError("The saved original is missing. Restore it before continuing.")
    # Two-minute source windows retain the whole recording, each below 40 MiB.
    for number in range(math.ceil(demo["duration_seconds"] / 120)):
        start = number * 120
        duration = min(120, demo["duration_seconds"] - start)
        clip = next((item for item in demo["clips"] if item["start_seconds"] == start), None)
        if not clip:
            eid = uid()
            path = root / "media" / (eid + ".mp4")
            run_ffmpeg(Path(original["path"]), path, start, duration)
            record = store.put(demo["session_id"], "evidence", {"id": eid, "path": str(path), "url": "/api/media/" + eid,
                "kind": "video", "mime_type": "video/mp4", "size": path.stat().st_size,
                "duration_seconds": duration, "timestamp_seconds": start, "original_evidence_id": original["id"],
                "original_name": f"family-moment-{number + 1}.mp4", "note": "A working clip from the real family recording. Original start: " + str(start) + " seconds.",
                "uploaded_to_meta": False, "analysed_at": None, "capture_source": "family_recording"})
            clip = {"evidence_id": record["id"], "start_seconds": start, "duration_seconds": duration, "status": "prepared"}
            demo["clips"].append(clip)
            save_json(descriptor, demo)
        if live and clip["status"] != "transcribed":
            record = store.get(clip["evidence_id"], "evidence")
            started = time.monotonic()
            cache = root / "transcripts" / (record["id"] + ".json")
            cache.parent.mkdir(exist_ok=True)
            if cache.exists():
                cached = json.loads(cache.read_text())
                transcript = cached["transcript"]
                metadata = cached["metadata"]
            else:
                store.put(demo["session_id"], "evidence", {**record, "meta_transmission_requested_at": now(),
                    "meta_transfer_status": "requested", "uploaded_to_meta": None})
                transcript = provider.transcribe({"clip_duration_seconds": duration}, [record])
                metadata = provider.last_response_metadata
                save_json(cache, {"transcript": transcript, "metadata": metadata, "model": provider.model})
            with store.atomic():
                for index, segment in enumerate(transcript["segments"]):
                    note_id = str(uuid5(NAMESPACE_URL, f"compinche/{digest}/{record['id']}/{index}/transcript-v1"))
                    if store.get(note_id, "note"):
                        continue
                    store.put(demo["session_id"], "note", {"id": note_id, "text": segment["text"], "source": "transcript", "confirmed": False,
                        "evidence_ids": [record["id"]], "timestamp_seconds": segment["start_seconds"],
                        "original_timestamp_seconds": start + segment["start_seconds"], "timestamp_basis": "media_local",
                        "end_seconds": segment["end_seconds"], "uncertainty": segment["uncertainty"],
                        "provenance": {"provider": "meta", "model": provider.model, "timing": "approximate", "human_verified": False}})
            clip.update(status="transcribed", summary=transcript["summary"],
                        seconds=round(time.monotonic() - started, 2), metadata=metadata)
            demo["transcription_model"] = provider.model
            if transcript["recipe_title"] and demo["title"] == "Dad’s recipe":
                demo["title"] = transcript["recipe_title"]
                store.update_session(demo["session_id"], title=demo["title"])
            save_json(descriptor, demo)
            print(json.dumps({"clip": number + 1, "status": "transcribed", "seconds": clip["seconds"]}), flush=True)
    if live:
        # Spark 1.3's audio is degraded upstream. Verify audio independently
        # with the dedicated speech model; keep the visual model unchanged.
        for clip in demo["clips"]:
            record = store.get(clip["evidence_id"], "evidence")
            if clip["status"] == "transcribed":
                store.put(demo["session_id"], "evidence", {**record, "meta_transfer_status": "sent", "uploaded_to_meta": True,
                    "automatic_description": clip.get("summary", ""),
                    "description_provenance": {"provider": "meta", "model": demo["transcription_model"], "human_verified": False}})
            cache = root / "transcripts" / (record["id"] + "-asr.json")
            if cache.exists():
                asr = json.loads(cache.read_text())
            else:
                audio = root / "audio" / (record["id"] + ".wav")
                audio.parent.mkdir(exist_ok=True)
                subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", record["path"], "-vn",
                                "-ac", "1", "-ar", "24000", "-c:a", "pcm_s16le", "-map_metadata", "-1", "-y", str(audio)],
                               check=True, capture_output=True, timeout=60)
                current = store.get(record["id"], "evidence")
                store.put(demo["session_id"], "evidence", {**current, "meta_audio_transfer_status": "requested"})
                asr = provider.transcribe_audio(audio, clip["duration_seconds"])
                save_json(cache, asr)
            with store.atomic():
                for index, segment in enumerate(asr["transcript"]["segments"]):
                    note_id = str(uuid5(NAMESPACE_URL, f"compinche/{digest}/{record['id']}/{index}/asr-v1"))
                    if store.get(note_id, "note"):
                        continue
                    store.put(demo["session_id"], "note", {"id": note_id, "text": segment["text"], "source": "transcript",
                        "confirmed": False, "evidence_ids": [record["id"]], "timestamp_seconds": segment["start_seconds"],
                        "original_timestamp_seconds": clip["start_seconds"] + segment["start_seconds"],
                        "timestamp_basis": "media_local", "end_seconds": segment["end_seconds"], "uncertainty": segment["uncertainty"],
                        "provenance": {"provider": "meta", "model": asr["model"], "timing": "speech_turn", "human_verified": False}})
                current = store.get(record["id"], "evidence")
                store.put(demo["session_id"], "evidence", {**current, "meta_audio_transfer_status": "sent"})
            clip["asr_turns"] = len(asr["transcript"]["segments"])
            demo["audio_transcription_model"] = asr["model"]
            save_json(descriptor, demo)
            print(json.dumps({"clip_start": clip["start_seconds"], "audio_turns": clip["asr_turns"]}), flush=True)
    if live and not any(item.get("basis") == "audio-reviewed-v1" for item in demo["analysis"]):
        agent = CookingAgent(store, provider)
        try:
            # One real turn; no fabricated human answer or auto confirmation.
            result = agent.invoke(demo["session_id"], {"text": "Responde en español. Este es un vídeo real de mi padre que quiero aprender a cocinar. Las notas son transcripciones automáticas sin verificar. Observa este fragmento y conserva lo que está sustentado; pregunta solo por una decisión que siga sin explicación. No inventes respuestas de mi padre ni confirmes descubrimientos.",
                "evidence_ids": [demo["clips"][0]["evidence_id"]], "answer_to": None,
                "idempotency_key": "family-demo-audio-observation-v1"})
            demo["analysis"].append({"model": provider.model, "message_id": result["message"]["id"],
                                      "basis": "audio-reviewed-v1", "question_id": (result.get("question") or {}).get("id"), "metadata": provider.last_response_metadata})
            save_json(descriptor, demo)
        finally:
            agent.close()
    if not demo.get("sdk_replay_evidence_id"):
        sdk_id = uid()
        sdk_path = root / "media" / (sdk_id + ".mp4")
        sdk_duration = min(60, demo["duration_seconds"])
        subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", original["path"], "-t", str(sdk_duration),
            "-vf", "scale=640:-2", "-c:v", "libx265", "-preset", "ultrafast", "-x265-params",
            "pools=2:frame-threads=2:log-level=error", "-tag:v", "hvc1", "-an", "-movflags", "+faststart", "-y", str(sdk_path)],
            check=True, capture_output=True, timeout=180)
        store.put(demo["session_id"], "evidence", {"id": sdk_id, "kind": "video", "mime_type": "video/mp4",
            "path": str(sdk_path), "url": "/api/media/" + sdk_id, "size": sdk_path.stat().st_size,
            "duration_seconds": sdk_duration, "timestamp_seconds": 0, "role": "sdk_replay", "codec": "hevc",
            "original_evidence_id": original["id"], "note": "HEVC working copy for Meta MockDeviceKit. Camera input only; original audio remains in the full recording.",
            "uploaded_to_meta": False, "capture_source": "glasses_replay"})
        demo["sdk_replay_evidence_id"] = sdk_id
        save_json(descriptor, demo)
    return demo


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("video", type=Path)
    parser.add_argument("--data-dir", type=Path, default=Path("backend/.data/family-demo"))
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--live", action="store_true")
    args = parser.parse_args()
    load_env(args.env_file)
    result = prepare(args.video, args.data_dir.resolve(), args.live)
    print(json.dumps({"session_id": result["session_id"], "clips": len(result["clips"]),
                      "transcribed": sum(item["status"] == "transcribed" for item in result["clips"]),
                      "model": result["transcription_model"], "analysis_turns": len(result["analysis"])}))


if __name__ == "__main__":
    main()
