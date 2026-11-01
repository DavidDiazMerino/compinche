"""Resident MapAnything worker: one JSON request per line, metric geometry written as .npz.

Runs inside the GPU container. MapAnything (Meta, Apache-2.0 weights) predicts camera
intrinsics and per-pixel metric 3D points from one or more images.
"""
import json
import os
import sys
import time

os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
PREFIX = "COMPINCHE_MEASURE_EVENT "


def emit(payload):
    sys.stdout.write(PREFIX + json.dumps(payload) + "\n")
    sys.stdout.flush()


def main():
    import numpy as np
    import torch
    from mapanything.models import MapAnything
    from mapanything.utils.image import load_images

    started = time.monotonic()
    model = MapAnything.from_pretrained(os.environ.get("COMPINCHE_MAPANYTHING", "facebook/map-anything-apache")).to("cuda").eval()
    emit({"ready": True, "load_seconds": round(time.monotonic() - started, 2)})
    for line in sys.stdin:
        request = json.loads(line)
        try:
            t = time.monotonic()
            views = load_images(request["images"])
            with torch.no_grad():
                predictions = model.infer(views, memory_efficient_inference=False, use_amp=True, amp_dtype="bf16",
                                          apply_mask=True, mask_edges=True)
            outputs = []
            for index, p in enumerate(predictions):
                path = os.path.join(request["output"], f"geometry-{index}.npz")
                np.savez_compressed(path, pts=p["pts3d_cam"][0].float().cpu().numpy(),
                                    K=p["intrinsics"][0].float().cpu().numpy(),
                                    conf=p["conf"][0].float().cpu().numpy(),
                                    metric_scale=p["metric_scaling_factor"].float().cpu().numpy())
                outputs.append(path)
            emit({"id": request["id"], "ok": True, "outputs": outputs, "seconds": round(time.monotonic() - t, 3)})
        except Exception as error:  # Report and keep the resident model alive.
            emit({"id": request.get("id"), "ok": False, "error": f"{type(error).__name__}: {error}"[:500]})


if __name__ == "__main__":
    main()
