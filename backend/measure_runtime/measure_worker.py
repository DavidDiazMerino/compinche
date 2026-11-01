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
    # CoTracker3 is preloaded too, so the first gesture comparison does not pay its load time.
    repo = os.path.join(os.environ.get("TORCH_HOME", "/cache/torch"), "hub", "facebookresearch_co-tracker_main")
    tracker = torch.hub.load(repo, "cotracker3_offline", source="local").cuda().eval() if os.path.isdir(repo) else None
    emit({"ready": True, "load_seconds": round(time.monotonic() - started, 2), "tracker": tracker is not None})
    for line in sys.stdin:
        request = json.loads(line)
        try:
            t = time.monotonic()
            if request.get("task") == "track":
                # CoTracker3 (Meta): dense point tracks through a short clip of JPEG frames.
                import glob
                from PIL import Image
                if tracker is None:
                    raise RuntimeError("CoTracker3 is not in the local torch hub cache.")
                frames = np.stack([np.array(Image.open(f).convert("RGB")) for f in sorted(glob.glob(os.path.join(request["frames"], "*.jpg")))])
                video = torch.tensor(frames).permute(0, 3, 1, 2)[None].float().cuda()
                with torch.no_grad():
                    tracks, visible = tracker(video, grid_size=int(request.get("grid", 20)))
                path = os.path.join(request["output"], "tracks.npz")
                np.savez_compressed(path, tracks=tracks[0].float().cpu().numpy(), visible=visible[0].float().cpu().numpy(),
                                    size=np.array(frames.shape[1:3]))
                emit({"id": request["id"], "ok": True, "outputs": [path], "seconds": round(time.monotonic() - t, 3)})
                continue
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
