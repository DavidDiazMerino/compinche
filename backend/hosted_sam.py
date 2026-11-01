"""SAM 3.1 through the Meta Model API: full-frame masks for one short noun phrase.

Uses Meta's official `meta_sam_parser` for the segmentation stream grammar
(https://github.com/meta-models/meta-model-cookbook/tree/main/07_sam/01_api_basics).
Masks are pixel localisation only; they carry no physical size by themselves.
"""
from __future__ import annotations

import base64
import mimetypes
import time
from contextlib import aclosing
from pathlib import Path

import numpy as np
from PIL import Image

BASE_URL = "https://api.meta.ai/v1"
MODEL = "sam-3.1"


class HostedSAMUnavailable(RuntimeError):
    pass


async def _events(stream):
    # The API closes lanes with content_part.done; the parser expects output_text.done.
    done = set()
    async for event in stream:
        data = event.model_dump() if hasattr(event, "model_dump") else dict(event)
        lane = (data.get("item_id"), data.get("output_index"), data.get("content_index"))
        if data.get("type") == "response.output_text.done":
            if lane not in done:
                done.add(lane)
                yield data
            continue
        yield data
        part = data.get("part")
        if (data.get("type") == "response.content_part.done" and lane not in done and isinstance(part, dict)
                and part.get("type") == "output_text" and isinstance(part.get("text"), str)):
            done.add(lane)
            yield {"type": "response.output_text.done", "item_id": data.get("item_id"),
                   "output_index": data.get("output_index"), "content_index": data.get("content_index"),
                   "text": part["text"]}


class HostedSAM:
    model = MODEL

    def __init__(self, key: str | None):
        self.key = key
        self.configured = bool(key)
        self._client = None

    def client(self):
        if not self.configured:
            raise HostedSAMUnavailable("Falta la clave de Meta en el servidor para SAM 3.1.")
        if self._client is None:
            from openai import AsyncOpenAI
            self._client = AsyncOpenAI(base_url=BASE_URL, api_key=self.key, timeout=45)
        return self._client

    async def segment(self, image: Path, concept: str) -> dict:
        """Return {'objects': [{'mask': bool HxW, 'box': [l,t,r,b], 'score'}], 'elapsed_ms'} in image pixels."""
        from meta_sam_parser import decode_mask_to_raster, image_segmentation_format, parse_responses_stream
        image = Path(image)
        mime = mimetypes.guess_type(image.name)[0] or "image/jpeg"
        url = f"data:{mime};base64,{base64.b64encode(image.read_bytes()).decode()}"
        started = time.monotonic()
        stream = await self.client().responses.create(model=MODEL, stream=True, input=[{"type": "message", "role": "user",
            "content": [{"type": "input_text", "text": concept.strip()}, {"type": "input_image", "image_url": url}]}])
        async with stream, aclosing(_events(stream)) as events:
            result = await parse_responses_stream(events, image_segmentation_format()).final_result()
        if result.outcome.status != "completed":
            raise HostedSAMUnavailable("SAM 3.1 no completó la segmentación.")
        width, height = Image.open(image).size
        boxes = {r.object_id: r for r in result.records if r.kind == "box"}
        objects = []
        for record in (r for r in result.records if r.kind == "mask"):
            box = boxes.get(record.object_id)
            if box is None:
                continue
            m = record.mask
            local = np.frombuffer(bytes(decode_mask_to_raster(m)), dtype=np.uint8).reshape(m.height, m.width) > 0
            # Hosted masks are cropped to their box; place them back in frame coordinates.
            full = np.zeros((height, width), bool)
            left, top = int(round(box.left)), int(round(box.top))
            h, w = min(m.height, height - top), min(m.width, width - left)
            full[top:top + h, left:left + w] = local[:h, :w]
            objects.append({"object_id": record.object_id, "mask": full,
                            "box": [float(box.left), float(box.top), float(box.right), float(box.bottom)],
                            "score": getattr(box, "score", None)})
        objects.sort(key=lambda o: int(o["mask"].sum()), reverse=True)
        return {"concept": concept, "objects": objects, "elapsed_ms": round((time.monotonic() - started) * 1000, 1),
                "model": MODEL}

    async def close(self):
        if self._client is not None:
            client, self._client = self._client, None
            await client.close()


CONCEPTS = {"saucepan": "pot", "spoon": "spoon", "food": "sauce", "hand": "hand", "dough": "dough", "croquette": "croquette"}


class HostedLiveSegmentation:
    """Live segmentation tool through the Meta Model API: the latest frames of a window, one concept.

    Same role as the local resident SAM worker, without a GPU: masks become boxes, area fractions and a
    highlighted evidence image. Pixel localisation only.
    """

    def __init__(self, store, sam: HostedSAM, clips=None, frames: int = 2):
        self.store, self.sam, self.clips, self.frames = store, sam, clips, frames
        self.jobs, self.busy = 0, False

    async def warm(self):
        return None

    async def close(self):
        await self.sam.close()

    def status(self):
        return {"provider": "sam-3.1 (Meta Model API)", "configured": self.sam.configured, "busy": self.busy,
                "jobs_in_runtime": self.jobs, "pixel_localisation_only": True}

    async def __call__(self, media, target, sid):
        import asyncio
        images = [m for m in media if m.get("kind") == "image"][-self.frames:]
        if not images:
            raise HostedSAMUnavailable("No hay imágenes en esta ventana.")
        concept = CONCEPTS.get(target, target)
        self.busy = True
        try:
            results = await asyncio.gather(*[self.sam.segment(Path(m["path"]), concept) for m in images])
        finally:
            self.busy = False
        self.jobs += 1
        samples, overlay_id = [], None
        for item, result in zip(images, results):
            h, w = (result["objects"][0]["mask"].shape if result["objects"] else Image.open(item["path"]).size[::-1])
            samples.append({"original_seconds": item.get("timestamp_seconds"), "objects_total": len(result["objects"]),
                "objects": [{"candidate_track_id": o["object_id"], "bbox_pixels": [round(v) for v in o["box"]],
                             "frame_area_fraction": round(float(o["mask"].sum()) / (h * w), 5)} for o in result["objects"][:8]]})
        last, latest = images[-1], results[-1]
        if latest["objects"]:
            img = Image.open(last["path"]).convert("RGB")
            tint = Image.new("RGB", img.size, (255, 196, 64))
            mask = np.zeros(latest["objects"][0]["mask"].shape, bool)
            for o in latest["objects"][:3]:
                mask |= o["mask"]
            img = Image.composite(Image.blend(img, tint, .5), img, Image.fromarray(mask.astype(np.uint8) * 255))
            media_dir = self.store.root / "media"
            media_dir.mkdir(exist_ok=True)
            from .store import uid
            path = media_dir / f"sam-{uid()}.jpg"
            img.save(path, quality=88)
            overlay_id = self.store.put(sid, "evidence", {"kind": "image", "path": str(path), "mime_type": "image/jpeg",
                "note": f"SAM 3.1 · {concept}", "created_by": "hosted_sam"})["id"]
        return {"model": MODEL, "concept": concept, "frame_count": len(images),
                "frames_with_detections": sum(bool(r["objects"]) for r in results), "samples": samples,
                "overlay_evidence_id": overlay_id, "elapsed_ms": max(r["elapsed_ms"] for r in results),
                "original_start_seconds": images[0].get("timestamp_seconds"), "original_end_seconds": last.get("timestamp_seconds"),
                "interpretation_limits": "Masks locate pixels in these frames; they do not establish texture, quantity or readiness."}
