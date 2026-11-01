"""Bounded CPU clips from server-owned media, with both source time origins."""
import hashlib
import json
import math
from pathlib import Path
import subprocess
import threading
from uuid import uuid5, NAMESPACE_URL


class ClipFailure(RuntimeError):
    pass


class ClipBusy(ClipFailure):
    pass


def duration(path):
    result = subprocess.run(["ffprobe", "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "v:0", "-show_entries",
        "stream=duration:format=duration", "-of", "json", str(path)], capture_output=True, text=True, check=True, timeout=20)
    metadata = json.loads(result.stdout)
    if not metadata.get("streams"):
        raise ValueError("No video track")
    try:
        value = float(metadata["streams"][0].get("duration", "nan"))
    except (ValueError, TypeError):
        value = math.nan
    if not math.isfinite(value) or value <= 0:
        # A MediaRecorder WebM can have no duration header. Encode only the
        # requested bounded interval; its MP4 must have a valid video duration.
        return None
    return value


def encode(source, destination, start, length):
    subprocess.run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-threads", "2",
        "-protocol_whitelist", "file,pipe", "-ss", str(start), "-i", str(source), "-t", str(length),
        "-map", "0:v:0", "-map", "0:a:0?", "-vf", "scale='min(960,iw)':-2", "-c:v", "libx264",
        "-preset", "veryfast", "-crf", "24", "-maxrate", "1800k", "-bufsize", "3600k", "-threads", "2",
        "-c:a", "aac", "-b:a", "96k", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(destination)],
        capture_output=True, check=True, timeout=120)


class ClipService:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()

    def create(self, sid, source, start, requested_length):
        if not math.isfinite(start) or not math.isfinite(requested_length) or start < 0 or not 0 < requested_length <= 120:
            raise ClipFailure("Choose a finite start and a clip length from zero to 120 seconds.")
        if not self.lock.acquire(blocking=False):
            raise ClipBusy("Another clip is being prepared. Try again shortly.")
        temporary = None
        created = None
        try:
            path = Path(source["path"]).resolve()
            if not path.is_relative_to(self.store.root / "media") or not path.is_file():
                raise ClipFailure("The original source is no longer available.")
            available = duration(path)
            if available is not None and start >= available:
                raise ClipFailure("Choose a start inside the selected video.")
            length = min(requested_length, available - start) if available is not None else requested_length
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
            clip_id = str(uuid5(NAMESPACE_URL, f"compinche/clip-v1/{sid}/{source['id']}/{digest}/{start:.6f}/{length:.6f}"))
            previous = self.store.get(clip_id, "evidence")
            if previous:
                if previous["session_id"] != sid:
                    raise ClipFailure("This clip belongs to another session.")
                if Path(previous["path"]).is_file():
                    return {"evidence": previous, "cached": True}
            destination = self.store.root / "media" / (clip_id + ".mp4")
            temporary = destination.with_suffix(".tmp.mp4")
            encode(path, temporary, start, length)
            if temporary.stat().st_size > 40 * 1024 * 1024:
                raise ClipFailure("Choose a shorter interval to keep the clip below 40 MiB.")
            actual_duration = duration(temporary)
            if actual_duration is None:
                raise ClipFailure("Choose an interval containing video frames.")
            temporary.replace(destination)
            created = destination
            evidence = self.store.put(sid, "evidence", {"id": clip_id, "kind": "video", "mime_type": "video/mp4",
                "path": str(destination), "url": "/api/media/" + clip_id, "size": destination.stat().st_size,
                "duration_seconds": actual_duration, "timestamp_seconds": (source.get("timestamp_seconds") or 0) + start,
                "source_local_start_seconds": start, "requested_duration_seconds": requested_length,
                "source_evidence_id": source["id"], "original_evidence_id": source.get("original_evidence_id") or source["id"],
                "source_sha256": digest, "capture_source": source.get("capture_source", "upload"),
                "original_name": "selected-moment.mp4", "role": "analysis_clip",
                "note": "A locally prepared interval from the selected original. Its time origin is stored separately.",
                "uploaded_to_meta": False, "analysed_at": None, "codec": "h264"})
            self.store.update_session(sid)
            return {"evidence": evidence, "cached": False}
        except ClipFailure:
            raise
        except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
            raise ClipFailure("The local clip could not be prepared. The original is unchanged.") from exc
        finally:
            if temporary:
                temporary.unlink(missing_ok=True)
            if created and not self.store.get(created.stem, "evidence"):
                created.unlink(missing_ok=True)
            self.lock.release()
