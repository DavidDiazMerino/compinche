"""Session-local diagnostics and attributable learning exports, without inference."""
from collections import Counter
from html import escape
import math
from urllib.parse import quote

from fastapi import Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .store import now


METRIC_DEFINITIONS = {
    "last_admission_ms": {"unit": "ms", "clock": "server_monotonic", "definition": "Entrada al handler HTTP de observación hasta admisión por el coordinador; incluye espera y validación. No incluye captura ni red del cliente."},
    "queue_ms": {"unit": "ms", "clock": "server_monotonic", "definition": "Admisión de la ventana hasta inicio del análisis visual."},
    "last_vision_ms": {"unit": "ms", "clock": "server_monotonic", "definition": "Llamada al proveedor visual y validación de su respuesta. No es latencia de cámara a voz."},
    "last_question_ready_ms": {"unit": "ms", "clock": "server_monotonic", "definition": "Admisión de la observación hasta pregunta elegible, incluyendo cola, visión y espera por turnos de voz. Todavía no implica reproducción."},
    "last_question_source_lag_seconds": {"unit": "s", "clock": "source_media", "definition": "Cursor de vídeo al emitir la pregunta menos fin del gesto observado; no es una duración de proceso."},
    "voice_preparation_ms": {"unit": "ms", "clock": "server_monotonic", "definition": "Preparación de WAV solicitada por HTTP, incluyendo espera de la voz local o caché; no confirma reproducción."},
    "ready_to_playback_ack_ms": {"unit": "ms", "clock": "server_monotonic", "definition": "Pregunta elegible hasta recepción del ACK de inicio de reproducción declarado por el cliente."},
    "admission_to_playback_ack_ms": {"unit": "ms", "clock": "server_monotonic", "definition": "Admisión de observación hasta ACK de inicio de reproducción; no mide captura, red inicial ni audibilidad física."},
    "wav_asr_ms": {"unit": "ms", "clock": "server_monotonic", "definition": "Procesamiento del turno WAV por el proveedor y persistencia. No incluye captura de voz ni endpointing del canal continuo."},
    "capture_to_audio_ms": {"unit": "ms", "clock": None, "definition": "Captura física hasta audio audible. No medido: faltan un reloj de captura sincronizado y verificación física de reproducción."},
}


def public(value):
    if isinstance(value, dict):
        return {k: public(v) for k, v in value.items() if k not in {
            "path", "remote_file_id", "authorization", "access_token", "api_key", "token"}}
    if isinstance(value, list):
        return [public(v) for v in value]
    return value


def provider_configuration(provider):
    # Explicit allowlist: never serialize provider/client __dict__ or credentials.
    return {"schema_version": "compinche.live.provider.v1", "implementation": type(provider).__name__, "model": getattr(provider, "model", None),
        "configured": bool(getattr(provider, "configured", False)),
        "vision_reasoning_effort": getattr(provider, "vision_reasoning_effort", None),
        "learning_reasoning_effort": getattr(provider, "learning_reasoning_effort", None),
        "vision_frame_limit": getattr(provider, "vision_frame_limit", None),
        "vision_max_output_tokens": getattr(provider, "vision_max_output_tokens", None),
        "vision_prompt_sha256": getattr(provider, "vision_prompt_sha256", None),
        "learning_prompt_sha256": getattr(provider, "learning_prompt_sha256", None)}


def finite(value):
    return type(value) in {int, float} and math.isfinite(value)


def summary(values):
    measured = [float(v) for v in values if finite(v) and v >= 0]
    return {"count": len(measured), "min": min(measured) if measured else None,
        "mean": round(sum(measured)/len(measured), 2) if measured else None,
        "max": max(measured) if measured else None, "status": "measured" if measured else "not_measured"}


def diagnostics(live, sid):
    snapshot = live.snapshot(sid)
    store = live.store
    events = sorted(store.list(sid, "live_event"), key=lambda e: e.get("seq", 0))
    observations = store.list(sid, "live_observation")
    questions = store.list(sid, "question")
    jobs = store.list(sid, "live_audio_job")
    series = {"admission_ms": [o.get("admission_ms") for o in observations],
        "queue_ms": [o.get("queue_ms") for o in observations],
        "vision_ms": [o.get("model_ms") for o in observations],
        "question_ready_ms": [q.get("admission_to_ready_ms") for q in questions],
        "voice_preparation_ms": [e["data"].get("preparation_ms") for e in events if e["type"] == "voice_prepared"],
        "ready_to_playback_ack_ms": [q.get("ready_to_playback_ack_ms") for q in questions],
        "admission_to_playback_ack_ms": [q.get("admission_to_playback_ack_ms") for q in questions],
        "wav_asr_ms": [j.get("elapsed_ms") for j in jobs if j.get("status") == "completed"]}
    config = snapshot["session"]["live"]["config"]
    return public({"schema_version": "compinche.live.diagnostics.v1", "exported_at": now(),
        "session_id": sid, "status": snapshot["status"], "session": snapshot["session"],
        "configuration": {"session": config, "provider_at_start": config.get("provider_configuration"),
            "provider_now": provider_configuration(live.provider),
            "coordinator": {name: getattr(live.core, name) for name in
                ("max_result_age", "max_source_lag", "cooldown", "question_ttl", "segmentation_cooldown")}},
        "clocks": {"server_wall": "Unix UTC seconds for chronology and correlation; never subtract source media seconds from this clock.",
            "server_monotonic": "Durations measured within the server process; persisted as milliseconds, not an absolute timestamp.",
            "source_media": "Position in the source recording/capture timeline, scoped by scene_epoch; can pause or seek."},
        "metric_definitions": METRIC_DEFINITIONS,
        "latencies": {**{key: {"unit": "ms", **summary(values)} for key, values in series.items()},
            "capture_to_audio_ms": {"value": None, "status": "not_measured", "reason": "No synchronized physical capture timestamp or physical audio-output verification."},
            "realtime_endpoint_to_final_ms": {"value": None, "status": "not_measured", "reason": "Realtime endpoint/final duration has not been instrumented."}},
        "source_lag_seconds": {"unit": "s", "clock": "source_media", **summary([
            q.get("temporal_reference", {}).get("source_lag_seconds") for q in questions])},
        "counters": snapshot["metrics"], "observation_statuses": dict(Counter(o["status"] for o in observations)),
        "runtime": {k: snapshot[k] for k in ("scene_epoch", "source_seconds", "queue_depth", "speech_pending", "speaking", "tools")},
        "observations": observations, "questions": questions, "audio_jobs": jobs,
        "transcripts": store.list(sid, "note"), "evidence": store.list(sid, "evidence"),
        "events": [{**event, "storage_id": event["id"], "id": event["seq"]} for event in events],
        "event_count": len(events), "events_complete": True,
        "playback_verification": {"kind": "client_start_ack", "physical_audibility_verified": False}})


def learning_resource(live, sid):
    snapshot = live.snapshot(sid)
    store, config = live.store, snapshot["session"]["live"]["config"]
    questions = store.list(sid, "question")
    notes = store.list(sid, "note")
    note_ids = {n["id"]: n for n in notes}
    confirmed, pending = [], []
    referenced = set()
    for knowledge in store.list(sid, "knowledge"):
        card = knowledge.get("decision_card")
        if not card:
            continue
        note = note_ids.get(card.get("answer_note_id"))
        is_confirmed = knowledge.get("confirmed") is True
        literal = note.get("text") if note else knowledge.get("quote", card.get("explanation"))
        refs = list(dict.fromkeys([*knowledge.get("evidence_ids", []), *card.get("demonstration_evidence_ids", []),
                                  *card.get("answer_evidence_ids", []), *([card["answer_note_id"]] if card.get("answer_note_id") else [])]))
        referenced.update(refs)
        item = {"id": knowledge["id"], "status": "confirmed" if is_confirmed else "needs_review",
            "reviewed_text": knowledge.get("text") if is_confirmed else None,
            "literal_explanation": literal, "speaker": knowledge.get("speaker", card.get("speaker")),
            "speaker_role": knowledge.get("speaker_role", card.get("role")),
            "speaker_verification": (note or {}).get("speaker_verification", card.get("speaker_verification", "unknown")),
            "transcript_provenance": (note or {}).get("provenance"), "question": card.get("question"),
            "observed_demonstration": {"action": card.get("action"), "cue": card.get("observed_cue"),
                "source_start_seconds": card.get("source_start_seconds"), "source_end_seconds": card.get("source_end_seconds"),
                "scene_epoch": card.get("scene_epoch"), "status": "model_observation"},
            "derived_interpretation": {"summary": card.get("summary"), "signal": card.get("signal"),
                "reported_sensation": card.get("reported_sensation"), "decision": card.get("decision"),
                "inferred_decision": card.get("inferred_decision"), "model": card.get("derived_by"),
                "confirmed": card.get("derivation_confirmed") is True,
                "status": "confirmed" if card.get("derivation_confirmed") is True else "needs_review"},
            "evidence_ids": refs, "confirmed_at": knowledge.get("confirmed_at"), "confirmed_by": knowledge.get("confirmed_by")}
        (confirmed if is_confirmed else pending).append(item)
    # Captured speech is available for review, never promoted to a recipe rule by export.
    for note in notes:
        referenced.update(note.get("evidence_ids", []))
    references = []
    for eid in sorted(referenced):
        item = store.get(eid)
        if not item or item.get("session_id") != sid:
            continue
        references.append({"id": eid, "kind": item.get("kind", "note"),
            "url": "/api/media/"+quote(eid, safe="") if item.get("kind") in {"image", "video", "audio"} else f"/api/sessions/{sid}/sources/{quote(eid, safe='')}",
            "source_seconds": item.get("timestamp_seconds"), "scene_epoch": item.get("scene_epoch")})
    original_id = config.get("source_evidence_id")
    original = store.get(original_id, "evidence") if original_id else None
    return public({"schema_version": "compinche.live.learning.v1", "exported_at": now(), "session_id": sid,
        "title": snapshot["session"]["title"], "teacher": snapshot["session"].get("teacher"),
        "source_kind": config.get("source_kind"), "session_status": snapshot["status"],
        "original_media": {"id": original_id, "url": "/api/media/"+quote(original_id, safe="")} if original else None,
        "counts": {"confirmed": len(confirmed), "needs_review": len(pending), "captured_explanations": len(notes)},
        "confirmed": confirmed, "needs_review": pending,
        "captured_explanations": notes,
        "open_questions": [q for q in questions if q.get("status") != "answered"], "references": references,
        "usage": {"instructions": "Usa como guía únicamente el texto revisado de las fichas confirmadas. Las pendientes, transcripciones y observaciones requieren revisión.",
            "identity": "El hablante es declarado por quien usa la sesión; no se ha identificado por su voz o imagen.",
            "scope": "Este recurso recoge lo capturado en esta sesión; no completa pasos ausentes ni inventa respuestas."}})


def learning_html(resource, base_url=""):
    e = lambda value: escape(str(value if value is not None else "Sin registrar"), quote=True)
    sections = []
    for key, title in (("confirmed", "Confirmado para practicar"), ("needs_review", "Pendiente de revisión: no usar como instrucción")):
        cards = []
        for item in resource[key]:
            demo, derived = item["observed_demonstration"], item["derived_interpretation"]
            links = " ".join(f'<a href="{e(base_url+ref["url"])}">{e(ref["kind"])} {e(ref["source_seconds"])}</a>'
                for ref in resource["references"] if ref["id"] in item["evidence_ids"])
            thumbnails = "".join(f'<figure><img loading="lazy" width="220" src="{e(base_url+ref["url"])}" alt="Fotograma de la demostración">'
                f'<figcaption>Vídeo {e(ref["source_seconds"])} s · época {e(ref["scene_epoch"])}</figcaption></figure>'
                for ref in resource["references"] if ref["id"] in item["evidence_ids"] and ref["kind"] == "image")
            reviewed = f'<p><strong>Texto revisado:</strong> {e(item["reviewed_text"])}</p>' if item["reviewed_text"] else ""
            derived_text = "; ".join(str(derived[k]) for k in ("summary", "signal", "reported_sensation", "decision") if derived.get(k))
            interpretation = f'<p><strong>Interpretación del modelo ({"confirmada" if derived["confirmed"] else "pendiente"}):</strong> {e(derived_text)}</p>' if derived_text else ""
            cards.append(f'<article><h3>{e(item["question"])}</h3>{reviewed}<blockquote>{e(item["literal_explanation"])}</blockquote>'
                f'<p>Hablante declarado: {e(item["speaker"])} · {e(item["speaker_role"])}</p>'
                f'<p>Gesto observado: {e(demo["action"])}. Señal visible: {e(demo["cue"])}. '
                f'Vídeo: {e(demo["source_start_seconds"])}–{e(demo["source_end_seconds"])} s · época {e(demo["scene_epoch"])}</p>'
                f'{interpretation}{thumbnails}<p>Fuentes: {links or "Sin enlaces conservados"}</p></article>')
        sections.append(f'<section><h2>{e(title)}</h2>{"".join(cards) or "<p>No hay fichas en este estado.</p>"}</section>')
    captures = "".join(f'<li><strong>{e(n.get("speaker"))}</strong> ({e(n.get("origin"))}; transcripción pendiente): {e(n.get("text"))}</li>'
                        for n in resource["captured_explanations"])
    questions = "".join(f'<li>{e(q["text"])} — {e(q["status"])} · vídeo {e(q.get("source_start_seconds"))}–{e(q.get("source_end_seconds"))} s · época {e(q.get("scene_epoch"))}</li>' for q in resource["open_questions"])
    original = f'<p><a href="{e(base_url+resource["original_media"]["url"])}">Abrir grabación original</a></p>' if resource.get("original_media") else ""
    return '<!doctype html><html lang="es"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' \
        '<title>Compinche · recurso de aprendizaje</title><style>body{font:17px/1.6 system-ui;max-width:860px;margin:2rem auto;padding:0 1rem;color:#1e302a}article{border:1px solid #b8c9c0;border-radius:12px;padding:1rem;margin:1rem 0}blockquote{border-left:4px solid #547b64;padding-left:1rem}a{color:#175e45}</style>' \
        f'<body><h1>{e(resource["title"])}</h1><p>{e(resource["usage"]["instructions"])}</p><p>{e(resource["usage"]["identity"])}</p>' \
        + original + "".join(sections) + f'<section><h2>Palabras capturadas para revisar</h2><ul>{captures}</ul></section>' \
        + f'<section><h2>Preguntas sin explicación guardada</h2><ul>{questions or "<li>Ninguna.</li>"}</ul></section>' \
        + f'<p>{e(resource["usage"]["scope"])}</p><p>Los enlaces a fotos, audio y vídeo requieren que el servidor local siga activo.</p><p>Sesión {e(resource["session_id"])} · exportado {e(resource["exported_at"])}</p></body></html>'


def install_report_routes(app, live):
    @app.get('/api/live/{sid}/diagnostics')
    async def diagnostic_export(sid: str, download: bool = False):
        data = diagnostics(live, sid)
        headers = {"Content-Disposition": f'attachment; filename="compinche-{sid}-diagnostics.json"'} if download else {}
        return JSONResponse(data, headers=headers)

    @app.get('/api/live/{sid}/learning-resource')
    async def learning_export(sid: str, request: Request, format: str = Query('json', pattern='^(json|html)$'), download: bool = False):
        data = learning_resource(live, sid)
        headers = {"Content-Disposition": f'attachment; filename="compinche-{sid}-learning.{format}"'} if download else {}
        if format == 'html':
            return HTMLResponse(learning_html(data, str(request.base_url).rstrip('/')), headers=headers)
        return JSONResponse(data, headers=headers)
