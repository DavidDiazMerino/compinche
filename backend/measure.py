"""Vessel measurement: hosted SAM 3.1 masks + MapAnything metric geometry + a known reference.

The pipeline is deliberately explicit about what each piece establishes:
- SAM 3.1 (Meta Model API) localises the vessel and a reference object in pixels.
- MapAnything (Meta, open weights, local GPU) predicts intrinsics and metric 3D points.
- A reference of known size (a 1 L milk carton) checks and recalibrates MapAnything's scale.
- The rim ellipse gives the diameter and how oblique the view is; an oblique view asks for a better one.
The result is an estimate with a range and its evidence, never a weight or a doneness claim.
"""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import time

import numpy as np
from PIL import Image, ImageDraw

from .store import uid

# Known references: concept for SAM, which dimension, plausible range in cm.
REFERENCES = [
    {"concept": "milk carton", "label": "brick de leche de 1 L", "dimension": "height", "range_cm": (16.5, 19.5)},
]
VESSELS = {"pot": "pot", "saucepan": "pot", "frying pan": "frying pan", "pan": "frying pan"}
OBLIQUE_RATIO = 0.5  # rim minor/major below this (tilt > 60°) -> ask for a view from above


class MeasureUnavailable(RuntimeError):
    pass


class GeometryRuntime:
    """One resident MapAnything process inside the local GPU container."""

    def __init__(self, root: Path, image: str | None, hf_cache: str | None, torch_cache: str | None):
        self.root, self.image = Path(root), image
        self.hf_cache, self.torch_cache = hf_cache, torch_cache
        self.process, self.lock, self.load_seconds = None, asyncio.Lock(), None
        self.warm_lock = asyncio.Lock()
        self.configured = bool(image and hf_cache and torch_cache)

    def status(self):
        return {"provider": "mapanything_apache_local", "configured": self.configured,
                "warm": bool(self.process and self.process.returncode is None), "load_seconds": self.load_seconds}

    async def _packet(self, timeout):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = await asyncio.wait_for(self.process.stdout.readline(), max(1, deadline - time.monotonic()))
            if not line:
                raise MeasureUnavailable(f"El proceso de geometría terminó (código {await self.process.wait()}).")
            if line.startswith(b"COMPINCHE_MEASURE_EVENT "):
                return json.loads(line[len(b"COMPINCHE_MEASURE_EVENT "):])
        raise MeasureUnavailable("La geometría excedió el tiempo permitido.")

    async def warm(self):
        async with self.warm_lock:  # session start and the first measurement may race to start the container
            await self._warm()

    async def _warm(self):
        if self.process and self.process.returncode is None:
            return
        if not self.configured:
            raise MeasureUnavailable("Configura COMPINCHE_MEASURE_IMAGE, COMPINCHE_SAM_CACHE y COMPINCHE_TORCH_CACHE.")
        worker = Path(__file__).parent / "measure_runtime" / "measure_worker.py"
        work = self.root / "measure"
        work.mkdir(parents=True, exist_ok=True)
        log = (self.root / "measure-worker.log").open("ab")
        args = ["docker", "run", "--rm", "-i", "--name", "compinche-measure-" + uid()[:8], "--network", "none",
                "--gpus", "all", "--ipc", "host", "--user", f"{os.getuid()}:{os.getgid()}", "--memory", "24g",
                "-e", "HOME=/tmp", "-e", "HF_HOME=/cache/hf", "-e", "HF_HUB_CACHE=/cache/hf/hub", "-e", "HF_HUB_OFFLINE=1",
                "-e", "TORCH_HOME=/cache/torch", "-e", "PYTHONUNBUFFERED=1",
                "-v", f"{self.hf_cache}:/cache/hf/hub:ro", "-v", f"{self.torch_cache}:/cache/torch:ro",
                "-v", f"{work}:/work", "-v", f"{worker}:/app/measure_worker.py:ro",
                self.image, "python3", "/app/measure_worker.py"]
        self.process = await asyncio.create_subprocess_exec(*args, stdin=asyncio.subprocess.PIPE,
                                                            stdout=asyncio.subprocess.PIPE, stderr=log)
        packet = await self._packet(240)
        if not packet.get("ready"):
            raise MeasureUnavailable("MapAnything no se pudo cargar.")
        self.load_seconds = packet["load_seconds"]

    async def geometry(self, image: Path) -> dict:
        async with self.lock:
            await self.warm()
            job = uid()
            folder = self.root / "measure" / job
            folder.mkdir(parents=True)
            target = folder / ("frame" + Path(image).suffix)
            target.write_bytes(Path(image).read_bytes())
            self.process.stdin.write((json.dumps({"id": job, "images": [f"/work/{job}/{target.name}"],
                                                  "output": f"/work/{job}"}) + "\n").encode())
            await self.process.stdin.drain()
            packet = await self._packet(60)
            while packet.get("id") != job:  # a cancelled earlier request may leave its reply in the pipe
                packet = await self._packet(60)
            if not packet.get("ok"):
                raise MeasureUnavailable("MapAnything no completó la geometría: " + str(packet.get("error")))
            data = np.load(folder / "geometry-0.npz")
            return {"pts": data["pts"], "K": data["K"], "conf": data["conf"], "seconds": packet["seconds"]}

    async def track(self, frames_dir: Path, grid: int = 20) -> dict:
        """CoTracker3 point tracks for the JPEG frames in a folder under this runtime's work directory."""
        async with self.lock:
            await self.warm()
            job = uid()
            folder = self.root / "measure" / job
            folder.mkdir(parents=True)
            target = folder / "frames"
            target.mkdir()
            for frame in sorted(Path(frames_dir).glob("*.jpg")):
                (target / frame.name).write_bytes(frame.read_bytes())
            self.process.stdin.write((json.dumps({"id": job, "task": "track", "frames": f"/work/{job}/frames",
                                                  "output": f"/work/{job}", "grid": grid}) + "\n").encode())
            await self.process.stdin.drain()
            packet = await self._packet(180)
            while packet.get("id") != job:
                packet = await self._packet(180)
            if not packet.get("ok"):
                raise MeasureUnavailable("CoTracker no completó el seguimiento: " + str(packet.get("error")))
            data = np.load(folder / "tracks.npz")
            return {"tracks": data["tracks"], "visible": data["visible"], "size": tuple(data["size"]), "seconds": packet["seconds"]}

    async def close(self):
        if self.process and self.process.returncode is None:
            self.process.stdin.close()
            try:
                await asyncio.wait_for(self.process.wait(), 10)
            except asyncio.TimeoutError:
                self.process.kill()


def _resize(mask, shape):
    h, w = shape
    return np.array(Image.fromarray(mask.astype(np.uint8) * 255).resize((w, h), Image.NEAREST)) > 127


def rim_ellipse(mask):
    """Rim from the silhouette: the widest row after a vertical median filter is the rim's major axis.

    Handles occupy few rows and are filtered out; the rim's upper edge to that row is half the minor axis,
    so minor/major is the cosine of the view tilt (1 = straight from above).
    """
    rows = np.where(mask.any(1))[0]
    if len(rows) < 6:
        return None
    lefts = np.array([mask[r].nonzero()[0].min() for r in rows], float)
    rights = np.array([mask[r].nonzero()[0].max() for r in rows], float)
    widths = rights - lefts + 1
    if widths.max() < 12:
        return None
    k = max(5, int(0.15 * widths.max())) | 1
    padded = np.pad(widths, k // 2, mode="edge")
    filtered = np.array([np.median(padded[i:i + k]) for i in range(len(widths))])
    i = int(filtered.argmax())
    major = float(filtered[i])
    minor = float(min(major, 2 * (rows[i] - rows[0]) + 1))
    window = slice(max(0, i - k // 2), i + k // 2 + 1)
    cx = float(np.median((lefts[window] + rights[window]) / 2))
    return {"center": [cx, float(rows[i])], "major_px": major, "minor_px": minor,
            "ratio": minor / major if major else 0.0, "angle": 0.0}


def _depth(pts, mask, rows=None):
    z = pts[..., 2]
    region = mask.copy()
    if rows is not None:
        keep = np.zeros_like(region)
        keep[max(0, rows[0]):rows[1]] = True
        region &= keep
    values = z[region & np.isfinite(z) & (z > 0)]
    return float(np.median(values)) if values.size else None


def measure_from_geometry(pts, K, vessel_mask, reference_mask=None, reference=None):
    """Pure geometry; separated so it can be tested without GPUs or network."""
    shape = pts.shape[:2]
    vessel = _resize(vessel_mask, shape)
    rim = rim_ellipse(vessel)
    if rim is None:
        return {"status": "insufficient_view", "reason": "No se ve el borde del recipiente."}
    cy = int(rim["center"][1])
    z = _depth(pts, vessel, (cy - 3, cy + 4)) or _depth(pts, vessel)
    raw_cm = float(rim["major_px"] * z / K[0, 0] * 100)
    result = {"status": "measured", "rim": rim, "depth_m": round(z, 3), "focal_px": round(float(K[0, 0]), 1),
              "raw_diameter_cm": round(raw_cm, 1), "tilt_degrees": round(float(np.degrees(np.arccos(min(1, rim["ratio"])))), 1),
              "view": "oblique" if rim["ratio"] < OBLIQUE_RATIO else "good", "reference": None}
    scale, spread = 1.0, 0.25  # without a reference, MapAnything's metric scale alone: wide range
    if reference_mask is not None and reference is not None:
        ref = _resize(reference_mask, shape)
        rows = np.where(ref.any(1))[0]
        zr = _depth(pts, ref)
        if rows.size and zr:
            measured = float(np.ptp(rows) * zr / K[1, 1] * 100)
            lo, hi = reference["range_cm"]
            mid = (lo + hi) / 2
            scale, spread = mid / measured, (hi - lo) / 2 / mid
            result["reference"] = {"label": reference["label"], "concept": reference["concept"],
                "assumed_cm": [lo, hi], "measured_raw_cm": round(measured, 1), "scale_correction": round(scale, 3)}
    diameter = raw_cm * scale
    result.update(diameter_cm=round(diameter, 1), range_cm=[round(diameter * (1 - spread), 1), round(diameter * (1 + spread), 1)])
    return result


def _font(weight, size):
    from PIL import ImageFont
    path = Path(__file__).resolve().parents[1] / "dist" / "assets" / f"font-{weight}.ttf"
    return ImageFont.truetype(str(path), size) if path.exists() else ImageFont.load_default()


def overlay(image_path, vessel_mask, reference_box, result, out_path):
    """Evidence image: vessel mask, fitted rim and the reference used for scale."""
    img = Image.open(image_path).convert("RGB")
    w, h = img.size
    tint = Image.new("RGB", img.size, (255, 196, 64))
    img = Image.composite(Image.blend(img, tint, .45), img, Image.fromarray(vessel_mask.astype(np.uint8) * 255))
    draw = ImageDraw.Draw(img)
    rim = result.get("rim")
    if rim:
        gh, gw = result["geometry_shape"]
        sx, sy = w / gw, h / gh
        cx, cy = rim["center"][0] * sx, rim["center"][1] * sy
        a, b = rim["major_px"] * sx / 2, rim["minor_px"] * sy / 2
        draw.ellipse([cx - a, cy - b, cx + a, cy + b], outline=(255, 255, 255), width=2)
        draw.line([cx - a, cy, cx + a, cy], fill=(255, 255, 255), width=2)
        label = f"≈ {result['diameter_cm']:.0f} cm  ({result['range_cm'][0]:.0f}–{result['range_cm'][1]:.0f})"
        font = _font(2, max(14, w // 48))
        left, top, right, bottom = draw.textbbox((0, 0), label, font=font)
        tx, ty = cx - (right - left) / 2, cy - b - (bottom - top) - 18
        draw.rounded_rectangle([tx - 10, ty - 6, tx + right - left + 10, ty + bottom - top + 8], 8, fill=(24, 22, 20))
        draw.text((tx - left, ty - top), label, font=font, fill=(255, 255, 255))
    if reference_box:
        draw.rectangle(reference_box, outline=(120, 200, 255), width=2)
        draw.text((reference_box[0], reference_box[3] + 4), "referencia de escala", font=_font(1, max(11, w // 70)), fill=(120, 200, 255))
    img.save(out_path, quality=90)
    return out_path


class MeasureService:
    def __init__(self, store, sam, geometry: GeometryRuntime):
        self.store, self.sam, self.geometry = store, sam, geometry

    def status(self):
        return {"segmentation": {"provider": "meta_model_api", "model": self.sam.model, "configured": self.sam.configured},
                "geometry": self.geometry.status()}

    async def measure(self, sid: str, evidence_id: str, vessel: str = "pot") -> dict:
        evidence = self.store.get(evidence_id, "evidence")
        if not evidence or evidence["session_id"] != sid or evidence.get("kind") != "image":
            raise MeasureUnavailable("Mide sobre una imagen de esta sesión.")
        image = Path(evidence["path"])
        concept = VESSELS.get(vessel, vessel)
        started = time.monotonic()
        seg, geo, *refs = await asyncio.gather(self.sam.segment(image, concept), self.geometry.geometry(image),
                                               *[self.sam.segment(image, r["concept"]) for r in REFERENCES])
        if not seg["objects"]:
            return self._save(sid, evidence_id, {"status": "not_found", "vessel": concept,
                "reason": f"SAM 3.1 no encontró «{concept}» en esta imagen."}, started)
        vessel_obj = seg["objects"][0]
        reference, reference_obj = None, None
        for spec, found in zip(REFERENCES, refs):
            if found["objects"]:
                reference, reference_obj = spec, found["objects"][0]
                break
        result = measure_from_geometry(geo["pts"], geo["K"], vessel_obj["mask"],
                                       reference_obj["mask"] if reference_obj else None, reference)
        result.update(vessel=concept, geometry_shape=list(geo["pts"].shape[:2]), vessel_box=vessel_obj["box"],
                      reference_box=reference_obj["box"] if reference_obj else None,
                      timings={"sam_ms": seg["elapsed_ms"], "geometry_s": geo["seconds"]},
                      models={"segmentation": "sam-3.1 (Meta Model API)", "geometry": "MapAnything Apache-2.0 (local)"})
        if result["status"] == "measured":
            (self.store.root / "media").mkdir(exist_ok=True)
            path = overlay(image, vessel_obj["mask"], reference_obj["box"] if reference_obj else None, result,
                           self.store.root / "media" / f"measure-{uid()}.jpg")
            art = self.store.put(sid, "evidence", {"kind": "image", "path": str(path), "mime_type": "image/jpeg", "derived_from": evidence_id,
                "note": "Medición: máscara SAM 3.1, borde ajustado y referencia de escala", "created_by": "measure"})
            result["overlay_evidence_id"] = art["id"]
        return self._save(sid, evidence_id, result, started)

    def _save(self, sid, evidence_id, result, started):
        result["elapsed_s"] = round(time.monotonic() - started, 2)
        result["source_evidence_id"] = evidence_id
        return self.store.put(sid, "measurement", result)

    async def close(self):
        await self.geometry.close()
        await self.sam.close()
