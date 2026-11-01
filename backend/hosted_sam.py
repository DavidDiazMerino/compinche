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
            await self._client.close()
