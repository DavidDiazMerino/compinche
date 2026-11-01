"""Bounded offline SAM 3.1 jobs for server-owned media.

No downloads, shell commands, generic URL fetches or client-controlled filesystem paths.
Segmentation is evidence of a model's pixel prediction, not physical measurement.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

TARGETS = {"saucepan": "saucepan", "spoon": "spoon", "food": "sauce", "hand": "hand", "dough": "dough", "croquette": "croquette"}
DEFAULT_IMAGE = "compinche-sam:local"
DEFAULT_CACHE = Path.home() / ".cache" / "huggingface" / "hub"
DEFAULT_LOCK = Path.home() / ".cache" / "compinche" / "gpu.lock.d"
LIMITS = {"max_seconds": 8, "sample_fps": 4, "max_width": 640, "max_frames": 32}


class SamUnavailable(RuntimeError):
    pass


class SamBusy(RuntimeError):
    pass


class SamFailure(RuntimeError):
    pass


def _memory_available_gib() -> float:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 / 1024
    except (OSError, ValueError):
        pass
    return 0


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SamService:
    def __init__(self, data_root: Path):
        self.data_root = Path(data_root).resolve()
        self.media_root = self.data_root / "media"
        self.artifact_root = self.data_root / "artifacts"
        self.image = os.getenv("COMPINCHE_SAM_IMAGE", DEFAULT_IMAGE)
        self.cache = Path(os.getenv("COMPINCHE_SAM_CACHE", str(DEFAULT_CACHE))).expanduser().resolve()
        self.gpu_lock = Path(os.getenv("COMPINCHE_GPU_LOCK", str(DEFAULT_LOCK))).expanduser().resolve()
        self._lock = asyncio.Lock()
        self._cached_status: tuple[float, dict] | None = None
        self.timeout = 300
        # GB10 shares system RAM with the GPU. Reserve headroom for other services.
        self.start_memory_gib = 42
        self.stop_memory_gib = 20

    def _runtime_status(self) -> dict:
        now = time.monotonic()
        if self._cached_status and now - self._cached_status[0] < 15:
            return dict(self._cached_status[1])
        reason = None
        image_id = None
        if not shutil.which("docker") or not shutil.which("ffmpeg"):
            reason = "Docker and FFmpeg are required for local segmentation."
        elif not all((self.cache / name).exists() for name in ("models--facebook--sam3.1", "models--facebook--sam3")):
            reason = "The local SAM 3.1 model cache is not configured. No weights will be downloaded."
        else:
            try:
                r = subprocess.run(["docker", "image", "inspect", self.image, "--format", "{{.Id}}"],
                                   capture_output=True, text=True, timeout=5, check=False)
                if r.returncode:
                    reason = "The configured local SAM runtime image is unavailable."
                else:
                    image_id = r.stdout.strip()
            except (OSError, subprocess.TimeoutExpired):
                reason = "The local Docker runtime is unavailable."
        result = {"status": "unavailable" if reason else "ready", "configured": not bool(reason),
                  "provider": "local", "model": "SAM 3.1", "network": "offline",
                  "targets": list(TARGETS), "limits": LIMITS,
                  "message": reason or "Local SAM 3.1 is available; weights load when a job starts.",
                  "runtime_image_id": image_id,
                  "capabilities": ["object_masks", "object_tracking"],
                  "not_supported": ["weight", "temperature", "physical_scale", "doneness"]}
        self._cached_status = now, result
        return dict(result)

    def status(self) -> dict:
        result = self._runtime_status()
        if result["configured"]:
            if self._lock.locked() or self.gpu_lock.exists():
                result.update(status="busy", message="The shared GPU is in use. Try again after the current job.")
            elif _memory_available_gib() < self.start_memory_gib:
                result.update(status="busy", message="Waiting for enough free unified memory for a guarded SAM job.")
        return result

    def _validate(self, media_path: Path, target: str, job_id: str) -> Path:
        if target not in TARGETS:
            raise ValueError("Choose one of the supported segmentation targets.")
        if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_-]{5,79}", job_id):
            raise ValueError("Invalid segmentation job id.")
        source = Path(media_path).resolve()
        if not source.is_relative_to(self.media_root.resolve()) or not source.is_file():
            raise ValueError("Segmentation requires an uploaded server-owned video.")
        if source.suffix.lower() not in {".mp4", ".webm", ".mov", ".m4v", ".mkv"}:
            raise ValueError("Segmentation currently accepts uploaded video files only.")
        if source.stat().st_size > 250 * 1024 * 1024:
            raise ValueError("Segmentation input must be no larger than 250 MB.")
        return source

    def _studio_busy(self) -> bool:
        # Fixed local address: never derived from user media or model output.
        try:
            with urllib.request.urlopen("http://127.0.0.1:7870/api/jobs", timeout=2) as response:
                jobs = json.loads(response.read(1024 * 1024))
            return any(j.get("status") in {"running", "queued"} for j in jobs)
        except (urllib.error.URLError, OSError, ValueError, TypeError):
            # The shared directory lock remains authoritative if Studio is offline.
            return False

    def _acquire_gpu(self, owner: str) -> None:
        if self._studio_busy():
            raise SamBusy("Imagination Studio is using the shared GPU. Try segmentation when it is idle.")
        if _memory_available_gib() < self.start_memory_gib:
            raise SamBusy("Not enough free unified memory to start this SAM job safely.")
        self.gpu_lock.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.gpu_lock.mkdir()
        except FileExistsError as exc:
            raise SamBusy("The shared GPU is in use. Try again after the current job.") from exc
        (self.gpu_lock / "owner").write_text(owner + "\n")

    def _release_gpu(self, owner: str) -> None:
        try:
            owner_file = self.gpu_lock / "owner"
            if owner_file.read_text().strip() == owner:
                owner_file.unlink()
                self.gpu_lock.rmdir()
        except OSError:
            pass

    async def _run(self, args: list[str], timeout: float, log: Path, *, monitor: bool = False) -> None:
        with log.open("ab") as stream:
            process = await asyncio.create_subprocess_exec(*args, stdout=stream, stderr=stream)
            started = time.monotonic()
            try:
                while process.returncode is None:
                    try:
                        await asyncio.wait_for(process.wait(), timeout=1)
                    except asyncio.TimeoutError:
                        pass
                    if process.returncode is not None:
                        break
                    if time.monotonic() - started > timeout:
                        raise SamFailure("Segmentation reached its execution time limit.")
                    if monitor and _memory_available_gib() < self.stop_memory_gib:
                        raise SamFailure("Segmentation stopped to preserve memory for other applications.")
                if process.returncode != 0:
                    raise SamFailure("The local segmentation worker failed. Its diagnostic log is kept on the server.")
            except BaseException:
                if process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 5)
                    except asyncio.TimeoutError:
                        process.kill()
                        await process.wait()
                raise

    async def segment(self, media_path: Path, target: str, job_id: str, start_seconds: float = 0, duration_seconds: float = 8) -> dict:
        source = self._validate(media_path, target, job_id)
        if not math.isfinite(start_seconds) or not 0 <= start_seconds <= 86400:
            raise ValueError("Choose a finite video start between zero and 86400 seconds.")
        if not math.isfinite(duration_seconds) or not 0 < duration_seconds <= LIMITS["max_seconds"]:
            raise ValueError("Choose a finite analysis duration greater than zero and at most eight seconds.")
        if self._lock.locked():
            raise SamBusy("A segmentation job is already running. Try again when it finishes.")
        config = await asyncio.to_thread(self._runtime_status)
        if not config["configured"]:
            raise SamUnavailable(config["message"])
        async with self._lock:
            output = self.artifact_root / job_id
            output.mkdir(parents=True, exist_ok=False)
            log = output / "worker.log"
            prepared = output / "analysis.mp4"
            started = time.monotonic()
            # Decode only a bounded interval before allocating model/GPU memory.
            await self._run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                             "-protocol_whitelist", "file,pipe", "-ss", str(start_seconds), "-i", str(source), "-t", str(duration_seconds),
                             "-vf", "scale='min(640,iw)':-2,fps=4", "-frames:v", str(min(32, math.ceil(duration_seconds * 4))), "-an",
                             "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
                             "-pix_fmt", "yuv420p", "-threads", "2", str(prepared)], 45, log)
            owner = f"compinche-sam-api {os.getpid()} {job_id}"
            await asyncio.to_thread(self._acquire_gpu, owner)
            container = "compinche-sam-" + job_id.lower()
            try:
                worker = Path(__file__).with_name("sam_worker.py").resolve()
                args = ["docker", "run", "--rm", "--name", container, "--network", "none", "--gpus", "all",
                        "--ipc", "private", "--shm-size", "2g", "--user", f"{os.getuid()}:{os.getgid()}",
                        "--memory", "36g", "--memory-swap", "36g", "--cpus", "6", "--cap-drop", "ALL",
                        "--security-opt", "no-new-privileges", "-e", "HOME=/tmp", "-e", "HF_HOME=/cache/huggingface",
                        "-e", "HF_HUB_OFFLINE=1", "-e", "OMP_NUM_THREADS=4",
                        "-v", f"{self.cache}:/cache/huggingface/hub:ro",
                        "-v", f"{prepared}:/input/analysis.mp4:ro", "-v", f"{worker}:/app/sam_worker.py:ro",
                        "-v", f"{output}:/output", self.image, "python", "/app/sam_worker.py",
                        "--input", "/input/analysis.mp4", "--output", "/output", "--target", TARGETS[target]]
                await self._run(args, self.timeout, log, monitor=True)
            finally:
                # Target only this job, never another container or shared service.
                try:
                    await asyncio.to_thread(subprocess.run, ["docker", "stop", "--time", "5", container],
                                            capture_output=True, timeout=15, check=False)
                except (OSError, subprocess.TimeoutExpired):
                    pass
                finally:
                    self._release_gpu(owner)
            raw = output / "overlay-raw.mp4"
            overlay = output / "overlay.mp4"
            await self._run(["ffmpeg", "-nostdin", "-hide_banner", "-loglevel", "error", "-y", "-i", str(raw),
                             "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "21", "-pix_fmt", "yuv420p",
                             "-movflags", "+faststart", "-threads", "2", str(overlay)], 30, log)
            result = json.loads((output / "result.json").read_text())
            for frame in result.get("frames", []):
                frame["source_seconds"] = round(start_seconds + frame["seconds"], 3)
            result.update(job_id=job_id, status="succeeded", provider="local", target=target,
                          source_start_seconds=start_seconds,
                          elapsed_seconds=round(time.monotonic()-started, 3),
                          source_sha256=await asyncio.to_thread(_sha256, source),
                          completed_at=datetime.now(timezone.utc).isoformat(),
                          artifacts={"overlay": "overlay.mp4", "poster": "poster.jpg", "report": "result.json"},
                          limits=LIMITS, runtime_image_id=config["runtime_image_id"])
            (output / "result.json").write_text(json.dumps(result, indent=2))
            return result
