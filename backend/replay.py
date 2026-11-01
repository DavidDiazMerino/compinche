"""Reproducible local video experiment using the application's real CookingAgent.

No synthetic model responses. --live explicitly opts into sending prepared clips
and their bounded context to Meta; without a key the report says blocked.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import os
import re
import subprocess
import time
from pathlib import Path

from .agent import CookingAgent, GuardrailFailure
from .provider import MetaProvider, ProviderFailure, ProviderUnavailable
from .sam_service import SamService, SamUnavailable, SamBusy, SamFailure
from .segmentation_context import summarise_job
from .store import Store, uid


def seconds(value):
    h, m, s = value.split(":")
    return int(h)*3600 + int(m)*60 + float(s)


def caption_words(path):
    """Read YouTube rolling VTT, deduplicating words by their actual timestamp."""
    words = {}
    for block in re.split(r"\n\s*\n", path.read_text().replace("\r", "")):
        lines = block.splitlines()
        if not lines or " --> " not in lines[0]:
            continue
        cue = seconds(lines[0].split()[0])
        for line in lines[1:]:
            # Rolling plain lines repeat previous words without word timings.
            if not re.search(r"<\d\d:\d\d:", line):
                continue
            parts = re.split(r"<(\d\d:\d\d:\d\d\.\d+)>", line)
            stamp = cue
            for i, part in enumerate(parts):
                if i % 2:
                    stamp = seconds(part)
                else:
                    text = html.unescape(re.sub(r"<[^>]*>", "", part)).strip()
                    if text:
                        words[(stamp, text)] = (stamp, text)
    return sorted(words.values())


def transcript_until(words, start, end):
    return " ".join(text for stamp, text in words if start <= stamp < end)


def run_ffmpeg(source, destination, start, duration, audio=True):
    args = ["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-protocol_whitelist", "file,pipe", "-ss", str(start), "-i", str(source),
            "-t", str(duration), "-map", "0:v:0", "-vf", "scale='min(960,iw)':-2",
            "-c:v", "libx264", "-crf", "24", "-preset", "veryfast", "-threads", "2"]
    args += ["-map", "0:a:0?", "-c:a", "aac", "-b:a", "96k"] if audio else ["-an"]
    subprocess.run(args + ["-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination)],
                   check=True, capture_output=True, timeout=120)


def video_pixel_hash(path):
    result = subprocess.run(["ffmpeg", "-nostdin", "-v", "error", "-i", str(path),
                             "-map", "0:v:0", "-an", "-f", "hash", "-hash", "sha256", "-"],
                            check=True, capture_output=True, text=True, timeout=90)
    return result.stdout.strip()


def file_hash(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_env(path):
    """Load only a file explicitly chosen by the operator; no shell evaluation."""
    if path:
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                if re.fullmatch(r"[A-Z][A-Z0-9_]*", key.strip()):
                    os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def media_record(store, sid, path, window, variant):
    eid = uid()
    return store.put(sid, "evidence", {
        "id": eid, "path": str(path), "url": "/api/media/" + eid,
        "kind": "video", "mime_type": "video/mp4", "size": path.stat().st_size,
        "timestamp_seconds": window["start"], "duration_seconds": window["duration"],
        "original_name": window["id"] + "-" + variant + ".mp4",
        "note": "Fragmento de una demostración pública de Loli Cocina. " +
                ("Con audio y subtítulos automáticos no verificados." if variant == "audio" else "Sin audio ni transcripción disponible para este turno."),
        "uploaded_to_meta": False, "analysed_at": None,
    })


def evaluate(result, evidence_id):
    """Mechanical checks only; useful questions still require human review."""
    question = result["message"].get("question")
    return {"schema_and_evidence_passed": result["guardrails"]["passed"],
            "question_count": int(bool(question)),
            "question_links_clip": evidence_id in question.get("evidence_ids", []) if question else None,
            "semantic_quality": "needs_human_review"}


def reuse_segments(store, original, case):
    if original.get("pixel_sha256") != case.get("pixel_sha256"):
        raise ValueError("Video frames differ; masks cannot be reused.")
    for source in original.get("sam_jobs", []):
        artifact_id = source.get("reused_from", source["id"])
        if source["status"] != "completed" or any(j.get("reused_from") == artifact_id for j in case["sam_jobs"]):
            continue
        result = {**source["result"], "mask_source_sha256": source["result"].get("mask_source_sha256", source["result"]["source_sha256"]),
                  "source_sha256": file_hash(Path(case["clip"])),
                  "provenance": "Masks reused after verifying identical decoded video pixels; source inference is preserved."}
        job = store.put(case["session_id"], "job", {**source, "id": uid(), "evidence_id": case["evidence_id"],
                                                 "reused_from": artifact_id, "result": result})
        case["sam_jobs"].append(job)


def write_report(root, report):
    (root / "replay-report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2))
    esc = lambda value: html.escape(str(value))
    cards = []
    rows = []
    for case in report["cases"]:
        video = Path(case["clip"]).relative_to(root).as_posix()
        response = case.get("agent", {})
        question = response.get("message", {}).get("question")
        output = question["text"] if question else ("Sin pregunta en este turno." if response else
                  "Meta no produjo una respuesta válida: " + case.get("meta_error", "error desconocido")
                  if case.get("meta_status") == "failed" else "Meta no ejecutado: " + case.get("meta_status", "pendiente"))
        rows.append(f'<tr><td><a href="#{esc(case["id"])}">{esc(case["id"])}</a></td><td>{esc(output)}</td><td>{esc(case.get("meta_seconds", "—"))}</td></tr>')
        overlays = []
        for job in case.get("sam_jobs", []):
            if job["status"] == "completed":
                rel = f"artifacts/{job.get('reused_from',job['id'])}/overlay.mp4"
                r = job["result"]
                stamp = case["start"] + r["source_start_seconds"]
                overlays.append(f'<p>{esc(job["target"])} · original {int(stamp)//60:02d}:{int(stamp)%60:02d} · {r["frames_with_detections"]}/{r["frame_count"]} fotogramas con candidatos (no es una medida de precisión).</p><video controls preload="metadata" src="{esc(rel)}"></video>')
            else:
                overlays.append(f'<p>{esc(job["target"])}: {esc(job.get("error",job["status"]))}</p>')
        review = f'<p><strong>Revisión de Codex:</strong> {esc(case["review_notes"])}</p>' if case.get("review_notes") else ""
        cards.append(f'''<article id="{esc(case['id'])}"><h2>{esc(case['label'])} · {esc(case['variant'])} · {esc(case.get('round','baseline'))}</h2>
<p>Original: {case['start']}–{case['start']+case['duration']} s · <a href="http://127.0.0.1:8000/#live/{esc(case['session_id'])}">Abrir conversación</a></p>
<video controls preload="metadata" src="{esc(video)}"></video><h3>Pregunta real de Meta</h3><p>{esc(output)}</p>
{review}
<details><summary>Respuesta y comprobaciones</summary><pre>{esc(json.dumps({'agent': response, 'checks':case.get('checks'), 'provider':case.get('meta_metadata'), 'elapsed_seconds':case.get('meta_seconds')},ensure_ascii=False,indent=2))}</pre></details>
<h3>Revisión humana</h3><p>{esc(case['review_focus'])}</p><p>Valorar: si aporta información, si ya está respondida, si llega a tiempo y si se apoya en el vídeo.</p>
<details><summary>Subtítulos automáticos del intervalo</summary><p>{esc(case.get('transcript','No suministrados al agente.'))}</p></details>
<h3>Segmentación local</h3>{''.join(overlays) or '<p>No ejecutada para esta variante.</p>'}</article>''')
    table = '<table><thead><tr><th>Prueba</th><th>Pregunta real</th><th>Segundos</th></tr></thead><tbody>' + "".join(rows) + '</tbody></table>'
    (root / "review.html").write_text('''<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Compinche · prueba de croquetas</title><style>body{font:17px system-ui;max-width:1100px;margin:40px auto;padding:0 20px;background:#f6f3eb;color:#25382d}article{background:white;padding:24px;margin:24px 0;border-radius:18px}video{width:min(100%,640px);display:block;border-radius:10px}pre{white-space:pre-wrap;overflow-wrap:anywhere;font-size:13px}h1{font-size:38px}a{color:#23704c}details{margin:16px 0}table{border-collapse:collapse;width:100%;background:white}th,td{text-align:left;padding:12px;border-bottom:1px solid #ddd;overflow-wrap:anywhere}th:first-child{width:24%}</style><h1>Ver el gesto. Encontrar la pregunta.</h1><p>Prueba local con el vídeo de Loli Cocina. Las preguntas solo aparecen si hubo una respuesta real de Meta. Los focos de revisión son una rúbrica humana y no se envían al modelo.</p>''' + table + "".join(cards) + "</html>")


async def replay(args):
    load_env(args.env_file)
    config = json.loads(args.manifest.read_text())
    source = (args.manifest.parent / config["source_file"]).resolve()
    captions = (args.manifest.parent / config["captions_file"]).resolve()
    if not source.is_file():
        raise SystemExit("No local source video exists. Download the source before replaying it.")
    root = args.data_dir.resolve()
    store = Store(root)
    provider = MetaProvider()
    agent = CookingAgent(store, provider)
    sam = SamService(root)
    words = caption_words(captions) if captions.is_file() else []
    report_path = root / "replay-report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text())
        if report["source_sha256"] != file_hash(source):
            raise SystemExit("The existing report belongs to a different video; choose another data directory.")
    else:
        report = {"source_url": config["source_url"], "source_sha256": file_hash(source),
                  "source_file": str(source), "orchestrator": "LangGraph / CookingAgent", "cases": []}
    try:
        for window in config["windows"]:
            if args.only and window["id"] != args.only:
                continue
            if not 0 <= window["start"] <= 86400 or not 0 < window["duration"] <= 40 or not 0 <= window["sam_offset"] <= window["duration"] - 8:
                raise ValueError("Replay windows must be bounded to 40 seconds with a valid eight-second SAM interval.")
            for variant in ("audio", "silent"):
                base_key = window["id"] + "-" + variant
                key = base_key + ("-" + args.revision if args.revision else "")
                case = next((c for c in report["cases"] if c["id"] == key), None)
                if case is None:
                    session = store.create_session({"title": config["title"] + " · " + key, "teacher": "Loli Cocina (vídeo público)", "mode": "record"})
                    baseline = next((c for c in report["cases"] if c["id"] == base_key), None) if args.revision else None
                    path = Path(baseline["clip"]) if baseline else root / "media" / (uid() + ".mp4")
                    if not baseline:
                        run_ffmpeg(source, path, window["start"], window["duration"], audio=variant == "audio")
                    evidence = media_record(store, session["id"], path, window, variant)
                    text = transcript_until(words, window["start"], window["start"] + window["duration"]) if variant == "audio" else ""
                    if text:
                        store.put(session["id"], "note", {"text": text, "source": "transcript", "confirmed": False,
                                  "created_by": "youtube_auto_captions", "provenance": "YouTube automatic Spanish captions; not verified by a human.",
                                  "timestamp_seconds": window["start"], "evidence_ids": [evidence["id"]]})
                    case = {"id": key, "round": args.revision or "baseline", "variant": variant, "label": window["label"], "start": window["start"], "duration": window["duration"],
                            "review_focus": window["review_focus"], "session_id": session["id"], "evidence_id": evidence["id"],
                            "clip": str(path), "transcript": text, "sam_jobs": []}
                    case["pixel_sha256"] = video_pixel_hash(path)
                    if baseline:
                        reuse_segments(store, baseline, case)
                    report["cases"].append(case)
                    write_report(root, report)
                sid = case["session_id"]
                if args.sam and variant == "audio":
                    for target in window["targets"]:
                        offset = window.get("target_offsets", {}).get(target, window["sam_offset"])
                        if not 0 <= offset <= window["duration"] - 2:
                            raise ValueError("The SAM interval must fit inside the replay clip.")
                        if any(j["target"] == target and j["status"] == "completed" and j.get("start_seconds") == offset and j.get("duration_seconds") == 2 for j in case["sam_jobs"]):
                            continue
                        job = store.put(sid, "job", {"evidence_id": case["evidence_id"], "target": target, "start_seconds": offset, "duration_seconds": 2, "status": "running"})
                        print(f"SAM {key}: {target}", flush=True)
                        try:
                            result = await sam.segment(Path(case["clip"]), target, job["id"], start_seconds=offset, duration_seconds=2)
                            result["artifacts"] = {k: f"/api/artifacts/{job['id']}/{Path(v).name}" for k, v in result["artifacts"].items()}
                            job = store.put(sid, "job", {**job, "status": "completed", "result": result})
                            print(f"  {result['frames_with_detections']}/{result['frame_count']} frames, {result['elapsed_seconds']}s", flush=True)
                        except (SamUnavailable, SamBusy, SamFailure, ValueError) as exc:
                            job = store.put(sid, "job", {**job, "status": "failed", "error": str(exc)})
                            print("  " + str(exc), flush=True)
                        case["sam_jobs"].append(job)
                        write_report(root, report)
                # The silent comparison uses the exact same image pixels; share only
                # pixel reports, never the audio session's notes or conversation.
                if variant == "silent":
                    audio_key = window["id"] + "-audio" + ("-" + args.revision if args.revision else "")
                    audio = next((c for c in report["cases"] if c["id"] == audio_key), {})
                    reuse_segments(store, audio, case)
                if args.live and provider.configured and not case.get("agent"):
                    prompt = "Responde en español. Estamos revisando un fragmento de una demostración grabada, no hablando con su autora. Observa únicamente este intervalo y su contexto disponible. Decide si merece la pena preguntar algo para aprender a reproducir el gesto o la decisión. Si hay una duda útil, formula una sola pregunta breve y concreta; si está explicado o no aporta nada, question:null. No inventes respuestas de la cocinera."
                    started = time.monotonic()
                    try:
                        result = agent.invoke(sid, {"text": prompt, "evidence_ids": [case["evidence_id"]], "idempotency_key": "replay-" + key})
                        case.update(agent=result, checks=evaluate(result, case["evidence_id"]), meta_status="succeeded", meta_seconds=round(time.monotonic()-started, 3), meta_metadata=provider.last_response_metadata)
                        case.pop("meta_error", None)
                        print("Meta " + key + ": " + (result["message"].get("question") or {}).get("text", "[silencio]"), flush=True)
                    except (ProviderFailure, ProviderUnavailable, GuardrailFailure) as exc:
                        case.update(meta_status="failed", meta_error=str(exc), meta_metadata=provider.last_response_metadata)
                        print("Meta " + key + ": " + str(exc), flush=True)
                elif not case.get("agent"):
                    case["meta_status"] = "blocked_missing_api_key" if not provider.configured else "not_requested_use_--live"
                write_report(root, report)
        print(str(root / "review.html"), flush=True)
    finally:
        agent.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--data-dir", type=Path, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--sam", action="store_true", help="Run bounded local GPU jobs")
    parser.add_argument("--live", action="store_true", help="Send six prepared clips to Meta using the server key")
    parser.add_argument("--only", choices=["bechamel", "formado", "fritura"], help="Evaluate just one stage")
    parser.add_argument("--revision", default="", help="Keep a new isolated round beside the baseline")
    args = parser.parse_args()
    if args.revision and not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", args.revision):
        parser.error("Use a short alphanumeric revision label.")
    asyncio.run(replay(args))


if __name__ == "__main__":
    main()
