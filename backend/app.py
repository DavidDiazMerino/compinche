"""Local-only Compinche API. Run with backend/.venv/bin/uvicorn backend.app:app."""
import asyncio
import hashlib
import json
import os
import re
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlsplit
from uuid import NAMESPACE_URL, uuid5

from fastapi import FastAPI, File, Form, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .agent import CookingAgent, ConversationConflict, GuardrailFailure
from .models import CreateSession, TurnRequest, NoteRequest, ConfirmRequest, SegmentRequest, ClipRequest
from .provider import MetaProvider, ProviderUnavailable, ProviderFailure
from .store import Store, uid, now
from .sam_service import SamService, SamUnavailable, SamBusy, SamFailure
from .clips import ClipService, ClipFailure, ClipBusy
from .live_api import install_live_routes

BASE = Path(__file__).resolve().parent
MAX_UPLOAD_BYTES = 150 * 1024 * 1024


def api_error(code, message, status=400):
    return HTTPException(status_code=status, detail={"code": code, "message": message})


def sanitise(value):
    if isinstance(value, dict):
        return {k: sanitise(v) for k, v in value.items() if k not in ("path", "remote_file_id")}
    if isinstance(value, list):
        return [sanitise(v) for v in value]
    return value


def detect_media(head):
    if head.startswith(b"\xff\xd8\xff"):
        return "image", "image/jpeg", ".jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image", "image/png", ".png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "image", "image/webp", ".webp"
    if head[4:8] == b"ftyp":
        return "video", "video/mp4", ".mp4"
    if head.startswith(b"\x1aE\xdf\xa3"):
        return "video", "video/webm", ".webm"
    if head[:4] == b"RIFF" and head[8:12] == b"WAVE":
        return "audio", "audio/wav", ".wav"
    if head.startswith(b"ID3") or head[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio", "audio/mpeg", ".mp3"
    if head.startswith(b"OggS"):
        return "audio", "audio/ogg", ".ogg"
    return None


def create_app(data_root=None, provider=None, sam=None, live_provider=None, voice=None):
    root = Path(data_root or os.environ.get("COMPINCHE_DATA_DIR", BASE / ".data")).resolve()
    store = Store(root)
    provider = provider or MetaProvider()
    sam = sam or SamService(root)
    agent = CookingAgent(store, provider)
    clips = ClipService(store)
    tasks = set()
    rates = defaultdict(deque)

    @asynccontextmanager
    async def lifespan(app):
        yield
        await live.close()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        agent.close()
        store.close()

    app = FastAPI(title="Compinche local API", version="0.2.0", lifespan=lifespan)
    app.state.store = store
    app.state.provider = provider
    app.state.agent = agent
    app.state.sam = sam
    live = install_live_routes(app, store, provider, sam, clips, live_provider, voice)
    # A private HTTPS name (e.g. a Tailscale serve hostname) can be added for the phone camera; never a public one.
    extra_hosts = [h.strip() for h in os.environ.get("COMPINCHE_ALLOWED_HOSTS", "").split(",") if h.strip()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver", *extra_hosts])

    @app.middleware("http")
    async def local_origin_guard(request: Request, call_next):
        if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
            origin = request.headers.get("origin")
            if request.headers.get("sec-fetch-site") == "cross-site" or (origin and urlsplit(origin).netloc != request.url.netloc):
                return JSONResponse(status_code=403, content={"detail": {"code": "cross_origin_blocked", "message": "Use this API from the local Compinche application."}})
            try:
                content_length = int(request.headers.get("content-length", "0") or 0)
            except ValueError:
                return JSONResponse(status_code=400, content={"detail": {"code": "invalid_request", "message": "Invalid request length."}})
            if content_length > MAX_UPLOAD_BYTES + 1024 * 1024:
                return JSONResponse(status_code=413, content={"detail": {"code": "upload_too_large", "message": "Choose a recording smaller than 150 MB."}})
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        elif request.url.path == "/" or Path(request.url.path).suffix.lower() in (".html", ".css", ".js", ".mjs"):
            # Revalidate the local app shell and code after each build. Media
            # keeps its existing cache behavior; API originals remain no-store.
            response.headers["Cache-Control"] = "no-cache"
        return response

    def session_or_404(sid):
        session = store.get_session(sid)
        if not session:
            raise api_error("session_not_found", "This session could not be found.", 404)
        return session

    def valid_evidence(sid, ids):
        allowed = store.related_ids(sid)
        items = []
        for eid in ids:
            item = store.get(eid, "evidence")
            if not item or item["session_id"] not in allowed:
                raise api_error("evidence_not_found", "That evidence does not belong to this session.", 404)
            items.append(item)
        return items

    def rate_limit(sid):
        recent = rates[sid]
        current = time.monotonic()
        while recent and current - recent[0] > 60:
            recent.popleft()
        if len(recent) >= 12:
            raise api_error("rate_limited", "Give Compinche a moment. Try again shortly.", 429)
        recent.append(current)

    @app.get("/api/health")
    async def health():
        sam_status = await run_in_threadpool(sam.status)
        return {"status": "ok", "version": "0.2.0", "storage": "local_sqlite", "agent": provider.status(), "sam": sam_status, "limits": {"max_upload_bytes": MAX_UPLOAD_BYTES, "max_meta_clip_bytes": 40 * 1024 * 1024, "turns_per_minute": 12}, "privacy": {"media_auto_upload": False, "meta_requires_explicit_analysis": True, "live_requires_session_consent": True, "live_auto_analysis_when_active": True, "local_only_server": True}}

    @app.get("/api/demo")
    async def family_demo():
        descriptor = root / "family-demo.json"
        if not descriptor.is_file():
            return {"available": False}
        try:
            data = json.loads(descriptor.read_text())
            original = store.get(data["original_evidence_id"], "evidence")
            if not original or original["session_id"] != data["session_id"] or not Path(original["path"]).is_file():
                raise ValueError("Missing demo original")
            return {"available": True, **{key: data.get(key) for key in
                ("session_id", "original_evidence_id", "poster_url", "title", "duration_seconds", "transcription_model", "audio_transcription_model")},
                "analysis_count": len(data.get("analysis", [])),
                "sdk_replay_url": "/api/media/" + data["sdk_replay_evidence_id"] if data.get("sdk_replay_evidence_id") else None,
                "clips": [{key: clip.get(key) for key in ("evidence_id", "start_seconds", "duration_seconds", "summary", "status")} | {"asr_turns": clip.get("asr_turns") if type(clip.get("asr_turns")) is int and clip["asr_turns"] >= 0 else None}
                          for clip in data.get("clips", [])]}
        except (ValueError, KeyError, OSError):
            raise api_error("demo_unavailable", "The family recording needs to be restored or prepared again.", 503)

    @app.get("/api/search")
    async def search(q: str = Query("", max_length=200), limit: int = Query(30, ge=1, le=50), category: str = "all"):
        if category not in ("all", "confirmed", "needs_review", "notes"):
            raise api_error("invalid_filter", "Choose a supported memory filter.")
        return store.search(q, limit, category)

    @app.post("/api/sessions", status_code=201)
    async def create_session(values: CreateSession):
        if values.source_session_id:
            source = session_or_404(values.source_session_id)
            if source["mode"] != "record":
                raise api_error("invalid_source", "Learn from an original recording session.")
        return store.create_session(values.model_dump())

    @app.get("/api/sessions")
    async def sessions():
        result = []
        for session in store.list_sessions():
            knowledge = store.list(session["id"], "knowledge")
            result.append({**session, "evidence_count": len(store.list(session["id"], "evidence")), "knowledge_count": len(knowledge),
                           "confirmed_count": sum(bool(k.get("confirmed")) for k in knowledge)})
        return {"sessions": result}

    @app.get("/api/replay-review", response_class=HTMLResponse)
    async def replay_review():
        report = root / "review.html"
        if not report.is_file():
            raise api_error("replay_not_found", "Prepare a video replay in this server's data directory first.", 404)
        # The generated report also works as a standalone local HTML file.
        content = report.read_text()
        content = re.sub(r'src="media/([a-zA-Z0-9_-]+)\.mp4"', r'src="/api/media/\1"', content)
        content = content.replace('src="artifacts/', 'src="/api/artifacts/')
        return HTMLResponse(content.replace('http://127.0.0.1:8000/#live/', '/#live/'))

    @app.get("/api/sessions/{sid}")
    async def session_detail(sid: str):
        session_or_404(sid)
        return sanitise(store.context(sid))

    @app.get("/api/sessions/{sid}/sources/{eid}")
    async def source_detail(sid: str, eid: str):
        session_or_404(sid)
        related = store.related_ids(sid)
        for kind in ("evidence", "note", "knowledge", "message", "question", "job"):
            source = store.get(eid, kind)
            if source and source["session_id"] in related:
                return {"entity_kind": kind, "source": sanitise(source)}
        raise api_error("source_not_found", "This source is not part of this recipe or its original lesson.", 404)

    @app.post("/api/sessions/{sid}/notes", status_code=201)
    async def add_note(sid: str, note: NoteRequest):
        session_or_404(sid)
        valid_evidence(sid, note.evidence_ids)
        saved = store.put(sid, "note", {**note.model_dump(), "source": "said", "confirmed": True, "created_by": "user"})
        store.update_session(sid)
        return saved

    @app.post("/api/sessions/{sid}/clips", status_code=201)
    async def prepare_clip(sid: str, values: ClipRequest):
        session_or_404(sid)
        source = valid_evidence(sid, [values.evidence_id])[0]
        if source["kind"] != "video":
            raise api_error("video_required", "Choose a video to prepare an interval.")
        try:
            return sanitise(await run_in_threadpool(clips.create, sid, source, values.start_seconds, values.duration_seconds))
        except ClipBusy as exc:
            raise api_error("clip_busy", str(exc), 409)
        except ClipFailure as exc:
            raise api_error("clip_failed", str(exc), 400)

    @app.post("/api/sessions/{sid}/turn")
    async def turn(sid: str, values: TurnRequest):
        session = session_or_404(sid)
        if session.get("live", {}).get("status") in {"running", "paused"}:
            raise api_error("live_session_active", "Usa la conversación y los controles de la sesión en directo.", 409)
        try:
            cached = store.cached_turn(sid, values.idempotency_key, json.dumps(values.model_dump(), sort_keys=True))
        except ValueError as exc:
            raise api_error("invalid_turn", str(exc), 400)
        if cached:
            return cached
        valid_evidence(sid, values.evidence_ids)
        if not provider.configured:
            raise api_error("meta_not_configured", "The Meta agent needs a server API key. You can still record, save notes and run local SAM.", 503)
        rate_limit(sid)
        try:
            return await run_in_threadpool(agent.invoke, sid, values.model_dump())
        except ProviderUnavailable as exc:
            raise api_error("meta_not_configured", str(exc), 503)
        except ProviderFailure as exc:
            raise api_error("meta_request_failed", str(exc), 502)
        except GuardrailFailure as exc:
            raise api_error("response_blocked", str(exc) + " No generated claims were saved. You can rephrase or add evidence.", 422)
        except ConversationConflict as exc:
            raise api_error("conversation_busy", str(exc), 409)
        except ValueError as exc:
            raise api_error("invalid_turn", str(exc), 400)

    @app.post("/api/sessions/{sid}/knowledge/{kid}/confirm")
    async def confirm(sid: str, kid: str, values: ConfirmRequest):
        session_or_404(sid)
        claim = store.get(kid, "knowledge")
        if not claim or claim["session_id"] != sid:
            raise api_error("claim_not_found", "Confirm a claim in its original session.", 404)
        if values.text is not None and values.text != claim["text"]:
            claim["original_model_text"] = claim.get("original_model_text", claim["text"])
            claim["text"] = values.text
        claim.update(confirmed=True, confirmed_at=now(), confirmed_by="user")
        return store.put(sid, "knowledge", claim)

    @app.post("/api/sessions/{sid}/evidence", status_code=201)
    async def upload(sid: str, file: UploadFile = File(...), note: str = Form(""), timestamp_seconds: float = Form(0), capture_source: str = Form("upload"), idempotency_key: str = Form("")):
        session_or_404(sid)
        if idempotency_key and not re.fullmatch(r"[a-zA-Z0-9_-]{1,128}", idempotency_key):
            raise api_error("invalid_upload_key", "Use an upload key of up to 128 letters, digits, dashes or underscores.")
        if capture_source not in ("upload", "phone_camera", "glasses_replay", "meta_dat_mock", "meta_dat_device"):
            raise api_error("invalid_capture_source", "Choose a supported recording source.")
        if len(note) > 2000 or not 0 <= timestamp_seconds <= 86400:
            raise api_error("invalid_metadata", "The note or timestamp is outside the accepted range.")
        head = await file.read(1024)
        media_type = detect_media(head)
        if not media_type:
            await file.close()
            raise api_error("unsupported_media", "Choose a valid MP4/WebM video, JPG/PNG/WebP image or WAV/MP3/Ogg audio.", 415)
        kind, mime, extension = media_type
        eid = uid()
        path = root / "media" / (eid + extension)
        fingerprint = hashlib.sha256(json.dumps([note, timestamp_seconds, capture_source, mime], ensure_ascii=False).encode())
        fingerprint.update(head)
        total = len(head)
        try:
            with path.open("xb") as output:
                output.write(head)
                while chunk := await file.read(1024 * 1024):
                    total += len(chunk)
                    if total > MAX_UPLOAD_BYTES:
                        raise api_error("upload_too_large", "Choose a recording smaller than 150 MB.", 413)
                    output.write(chunk)
                    fingerprint.update(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        finally:
            await file.close()
        if idempotency_key:
            eid = str(uuid5(NAMESPACE_URL, f"compinche/upload-v1/{sid}/{idempotency_key}"))
        with store.lock:
            previous = store.get(eid, "evidence") if idempotency_key else None
            if previous:
                path.unlink(missing_ok=True)
                if previous.get("upload_fingerprint") != fingerprint.hexdigest():
                    raise api_error("upload_key_conflict", "This upload key already belongs to different evidence or metadata.", 409)
                return sanitise(previous)
            try:
                evidence = store.put(sid, "evidence", {"id": eid, "path": str(path), "url": "/api/media/" + eid, "kind": kind, "mime_type": mime, "size": total, "note": note, "timestamp_seconds": timestamp_seconds, "capture_source": capture_source, "capture_source_verification": "client_declared", "original_name": Path(file.filename or "recording").name[:200], "analysed_at": None, "uploaded_to_meta": False,
                    **({"upload_fingerprint": fingerprint.hexdigest(), "upload_key": idempotency_key} if idempotency_key else {})})
            except BaseException:
                path.unlink(missing_ok=True)
                raise
            store.update_session(sid)
            return sanitise(evidence)

    @app.get("/api/media/{eid}")
    async def media(eid: str):
        evidence = store.get(eid, "evidence")
        if not evidence:
            raise api_error("media_not_found", "This media could not be found.", 404)
        path = Path(evidence["path"]).resolve()
        if not path.is_relative_to(root / "media") or not path.is_file():
            raise api_error("media_not_found", "This media is no longer available.", 404)
        return FileResponse(path, media_type=evidence["mime_type"])

    async def execute_segment(job, evidence):
        try:
            store.put(job["session_id"], "job", {**job, "status": "running", "started_at": now()})
            options = {"start_seconds": job["start_seconds"]} if job.get("start_seconds") else {}
            if job.get("duration_seconds", 8) != 8:
                options["duration_seconds"] = job["duration_seconds"]
            result = await sam.segment(Path(evidence["path"]), job["target"], job["id"], **options)
            artifacts = {key: f"/api/artifacts/{job['id']}/{Path(name).name}" for key, name in result.get("artifacts", {}).items()}
            result = {**result, "artifacts": artifacts}
            store.put(job["session_id"], "job", {**job, "status": "completed", "completed_at": now(), "result": result})
        except (SamUnavailable, SamBusy, SamFailure, ValueError) as exc:
            store.put(job["session_id"], "job", {**job, "status": "failed", "error": str(exc)[:700]})
        except asyncio.CancelledError:
            store.put(job["session_id"], "job", {**job, "status": "interrupted", "error": "The local server stopped."})
            raise
        except Exception:
            store.put(job["session_id"], "job", {**job, "status": "failed", "error": "The segmentation worker could not finish. The original recording is safe."})

    @app.post("/api/sessions/{sid}/segment", status_code=202)
    async def segment(sid: str, values: SegmentRequest):
        session_or_404(sid)
        evidence = valid_evidence(sid, [values.evidence_id])[0]
        if evidence["kind"] != "video":
            raise api_error("video_required", "Local SAM tracking currently takes a video clip.")
        status = await run_in_threadpool(sam.status)
        if status.get("status") == "unavailable" or not status.get("configured", True):
            raise api_error("sam_unavailable", status.get("reason", "The local SAM runtime is not available."), 503)
        if status.get("status") == "busy":
            raise api_error("sam_busy", status.get("reason", "The local GPU is busy. Try again when the current job finishes."), 409)
        if any(j["status"] in ("queued", "running") for j in store.all_kind("job")):
            raise api_error("sam_busy", "A segmentation job is already running. Wait for it to finish.", 409)
        job = store.put(sid, "job", {"evidence_id": values.evidence_id, "target": values.target, "start_seconds": values.start_seconds, "duration_seconds": values.duration_seconds, "status": "queued"})
        task = asyncio.create_task(execute_segment(job, evidence))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
        return job

    @app.get("/api/jobs/{jid}")
    async def get_job(jid: str):
        job = store.get(jid, "job")
        if not job:
            raise api_error("job_not_found", "This segmentation job could not be found.", 404)
        return sanitise(job)

    @app.get("/api/artifacts/{jid}/{filename}")
    async def artifact(jid: str, filename: str):
        job = store.get(jid, "job")
        if not job or job["status"] != "completed":
            raise api_error("artifact_not_found", "This result is not available yet.", 404)
        artifact_dir = (root / "artifacts" / jid).resolve()
        path = (artifact_dir / filename).resolve()
        if not path.is_relative_to(artifact_dir) or not path.is_file() or path.suffix.lower() not in (".mp4", ".jpg", ".png", ".json"):
            raise api_error("artifact_not_found", "This result could not be found.", 404)
        return FileResponse(path)

    # API routes take priority. No cloud runtime or Sites deployment is required.
    dist = BASE.parent / "dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="frontend")
    return app


app = create_app()
