"""Compare a learner's check with the teacher's demonstration of the same check (scene 11).

CoTracker3 (Meta) follows dense points through both clips; the most-moving points describe the
gesture (the hand and the tool). Dynamic time warping aligns the two gestures, so the split screen
shows the same phase side by side even if one person is slower. Muse Spark then compares the
aligned frames and names one concrete, visible difference. It does not judge doneness.
"""
from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
import subprocess
import tempfile
import warnings

import numpy as np
from PIL import Image, ImageDraw

from .models import StrictModel
from .store import uid
from pydantic import Field

FPS = 10
WIDTH = 512


class GestureDifference(StrictModel):
    same_gesture: bool
    difference: str = Field(max_length=300)
    suggestion: str = Field(max_length=300)
    phase: str = Field(max_length=120)


COMPARE_PROMPT = '''Devuelve el JSON exacto en español, tuteando al aprendiz. Recibes pares de imágenes ALINEADOS en el tiempo: en cada par, la primera
es la demostración de quien enseñó y la segunda el intento del aprendiz, en la misma fase del gesto. También recibes lo que la persona
que enseñó dijo que comprueba. same_gesture es false si el aprendiz no está haciendo esa comprobación. difference describe UNA
diferencia que se VE en las imágenes y que importa para esa comprobación (por ejemplo cómo cae la masa, la altura o el ángulo de la
herramienta, el ritmo), en una frase para decir en voz alta. suggestion es una acción concreta para repetirla como la enseñó. No
afirmes que está o no está en su punto, ni inventes cantidades, temperaturas o sensaciones. phase nombra la fase comparada.'''


def extract_frames(video: Path, start: float, end: float, out: Path, fps: int = FPS, width: int = WIDTH) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", f"{max(0, start):.3f}", "-t", f"{max(.5, end - start):.3f}",
                    "-i", str(video), "-vf", f"fps={fps},scale={width}:-2", "-q:v", "3", str(out / "%04d.jpg")], check=True)
    return sorted(out.glob("*.jpg"))


def _smooth(x, k=5):
    pad = np.pad(x, ((k // 2, k // 2), (0, 0)), mode="edge")
    return np.stack([pad[i:i + len(x)] for i in range(k)]).mean(0)


def signature(tracks: np.ndarray, visible: np.ndarray, size) -> np.ndarray:
    """Position and velocity of the points with the largest vertical travel (the lifted tool and hand)."""
    h, w = size
    xy = tracks / np.array([w, h])
    vis = visible > .5
    y = np.where(vis, xy[..., 1], np.nan)
    travel = np.nan_to_num(np.nanmax(y, 0) - np.nanmin(y, 0))
    keep = travel >= np.quantile(travel, .9)
    pts = np.where(vis[:, keep, None], xy[:, keep], np.nan)
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        centroid = np.nanmean(pts, axis=1)
    centroid = np.where(np.isnan(centroid), np.nanmean(centroid, axis=0), centroid)
    centroid = _smooth(centroid - centroid.mean(0))
    velocity = np.gradient(centroid, axis=0)
    return np.concatenate([centroid, velocity * 3], 1)


def dtw(a: np.ndarray, b: np.ndarray) -> list[tuple[int, int]]:
    n, m = len(a), len(b)
    cost = np.linalg.norm(a[:, None] - b[None], axis=-1)
    acc = np.full((n + 1, m + 1), np.inf)
    acc[0, 0] = 0
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            acc[i, j] = cost[i - 1, j - 1] + min(acc[i - 1, j], acc[i, j - 1], acc[i - 1, j - 1])
    i, j, path = n, m, []
    while i > 0 and j > 0:
        path.append((i - 1, j - 1))
        step = int(np.argmin([acc[i - 1, j - 1], acc[i - 1, j], acc[i, j - 1]]))
        i, j = (i - 1, j - 1) if step == 0 else (i - 1, j) if step == 1 else (i, j - 1)
    return path[::-1]


def key_pairs(teacher_sig, learner_sig, count=3):
    """Peak to peak: the highest point of the tool in each clip is the same moment of the check, whatever the pace.

    The frames just before and after each peak, scaled to each clip's length, complete the comparison.
    """
    tp, lp = int(np.argmin(teacher_sig[:, 1])), int(np.argmin(learner_sig[:, 1]))
    ts, ls = max(3, len(teacher_sig) // 10), max(3, len(learner_sig) // 10)
    pairs = [(max(0, tp - ts), max(0, lp - ls)), (tp, lp),
             (min(len(teacher_sig) - 1, tp + ts), min(len(learner_sig) - 1, lp + ls))]
    return list(dict.fromkeys(pairs))[:count]


def _label(img, text, color):
    d = ImageDraw.Draw(img)
    from .measure import _font
    font = _font(2, 18)
    l, t, r, b = d.textbbox((0, 0), text, font=font)
    d.rounded_rectangle([10, 10, 30 + r - l, 22 + b - t], 8, fill=color)
    d.text((20 - l, 16 - t), text, font=font, fill=(255, 255, 255))
    return img


def render_split(teacher_frames, learner_frames, path, out_video: Path, teacher_name: str, learner_name: str,
                 teacher_tracks=None, learner_tracks=None) -> Path:
    """Side-by-side video that follows the alignment: both sides show the same phase of the gesture."""
    with tempfile.TemporaryDirectory() as tmp:
        for k, (i, j) in enumerate(path):
            a = _label(Image.open(teacher_frames[i]).convert("RGB"), teacher_name, (88, 110, 74))
            b = _label(Image.open(learner_frames[j]).convert("RGB"), learner_name, (176, 104, 78))
            b = b.resize((a.width, int(b.height * a.width / b.width)))
            canvas = Image.new("RGB", (a.width * 2 + 8, max(a.height, b.height)), (24, 22, 20))
            canvas.paste(a, (0, 0))
            canvas.paste(b, (a.width + 8, 0))
            canvas.save(Path(tmp) / f"{k:05d}.jpg", quality=88)
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-framerate", str(FPS), "-i", str(Path(tmp) / "%05d.jpg"),
                        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out_video)], check=True)
    return out_video


def pair_image(teacher_frame, learner_frame, out: Path) -> Path:
    a, b = Image.open(teacher_frame).convert("RGB"), Image.open(learner_frame).convert("RGB")
    b = b.resize((a.width, int(b.height * a.width / b.width)))
    canvas = Image.new("RGB", (a.width * 2 + 8, max(a.height, b.height)), (24, 22, 20))
    canvas.paste(a, (0, 0))
    canvas.paste(b, (a.width + 8, 0))
    canvas.save(out, quality=88)
    return out


async def compare_with_spark(provider, pairs_frames, teacher_said: str | None) -> dict:
    blocks = [{"type": "input_text", "text": json.dumps({"teacher_said_they_check": teacher_said}, ensure_ascii=False)}]
    for n, (t, l) in enumerate(pairs_frames, 1):
        for who, frame in (("demostración", t), ("aprendiz", l)):
            blocks += [{"type": "input_text", "text": f"Par {n} · {who}"},
                       {"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(Path(frame).read_bytes()).decode()}]
    raw = await provider.response(COMPARE_PROMPT, GestureDifference.model_json_schema(), blocks,
                                  "compinche_gesture_compare", 1600, effort="low")
    return GestureDifference.model_validate(raw).model_dump()


class GestureService:
    def __init__(self, store, geometry, provider):
        self.store, self.geometry, self.provider = store, geometry, provider

    async def compare(self, sid: str, teacher: dict, learner: dict, teacher_said: str | None,
                      teacher_name="Demostración", learner_name="Tu intento") -> dict:
        """teacher/learner: {'video': path, 'start': s, 'end': s}. Stores the split video and the comparison."""
        work = self.store.root / "measure" / ("gesture-" + uid())
        tf = await asyncio.to_thread(extract_frames, Path(teacher["video"]), teacher["start"], teacher["end"], work / "teacher")
        lf = await asyncio.to_thread(extract_frames, Path(learner["video"]), learner["start"], learner["end"], work / "learner")
        if len(tf) < 6 or len(lf) < 6:
            raise ValueError("Los fragmentos son demasiado cortos para comparar el gesto.")
        tt = await self.geometry.track(work / "teacher")
        lt = await self.geometry.track(work / "learner")
        ts, ls = signature(tt["tracks"], tt["visible"], tt["size"]), signature(lt["tracks"], lt["visible"], lt["size"])
        path = dtw(ts, ls)
        pairs = key_pairs(ts, ls)
        difference = await compare_with_spark(self.provider, [(tf[i], lf[j]) for i, j in pairs], teacher_said)
        media = self.store.root / "media"
        media.mkdir(exist_ok=True)
        video = await asyncio.to_thread(render_split, tf, lf, path, media / f"gesture-{uid()}.mp4", teacher_name, learner_name)
        peak = pairs[len(pairs) // 2]
        still = pair_image(tf[peak[0]], lf[peak[1]], media / f"gesture-{uid()}.jpg")
        video_ev = self.store.put(sid, "evidence", {"kind": "video", "path": str(video), "mime_type": "video/mp4",
            "note": "Pantalla partida alineada con CoTracker3 + DTW", "created_by": "gesture"})
        still_ev = self.store.put(sid, "evidence", {"kind": "image", "path": str(still), "mime_type": "image/jpeg",
            "note": "Fase comparada: demostración e intento", "created_by": "gesture"})
        return self.store.put(sid, "gesture_comparison", {**difference, "split_video_evidence_id": video_ev["id"],
            "still_evidence_id": still_ev["id"], "aligned_pairs": [list(p) for p in pairs], "alignment_length": len(path),
            "teacher_interval": [teacher["start"], teacher["end"]], "learner_interval": [learner["start"], learner["end"]],
            "timings": {"track_teacher_s": tt["seconds"], "track_learner_s": lt["seconds"]},
            "models": {"tracking": "CoTracker3 (Meta, local)", "comparison": getattr(self.provider, "model", "muse-spark")}})
