"""Runs inside the existing isolated SAM runtime. It never makes network calls."""
import argparse
import inspect
import json
import time
from pathlib import Path

import cv2
import numpy as np
from sam3.model_builder import build_sam3_predictor


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--target", choices=["saucepan", "spoon", "sauce", "hand", "dough", "croquette"], required=True)
    args = parser.parse_args()
    cap = cv2.VideoCapture(str(args.input))
    fps = cap.get(cv2.CAP_PROP_FPS)
    if not 0 < fps <= 8:
        raise ValueError("Expected a bounded preprocessed video.")
    frames = []
    while len(frames) < 32:
        ok, frame = cap.read()
        if not ok:
            break
        if max(frame.shape[:2]) > 1280 or frame.shape[1] > 640:
            raise ValueError("Video dimensions exceed the inference bound.")
        frames.append(frame)
    cap.release()
    if not frames:
        raise ValueError("No decodable video frames were found.")
    height, width = frames[0].shape[:2]
    frame_dir = args.output / "input-frames"
    frame_dir.mkdir(exist_ok=False)
    for index, frame in enumerate(frames):
        cv2.imwrite(str(frame_dir / f"{index:05d}.jpg"), frame)
    started = time.monotonic()
    predictor = build_sam3_predictor(version="sam3.1", compile=False, warm_up=False,
                                    max_num_objects=16, multiplex_count=16, use_fa3=False,
                                    async_loading_frames=False)
    # The installed release forwards optional args not present in its model's init_state.
    init_state = predictor.model.init_state
    allowed = set(inspect.signature(init_state).parameters)
    predictor.model.init_state = lambda *a, **kw: init_state(*a, **{k: v for k, v in kw.items() if k in allowed})
    load_seconds = time.monotonic() - started
    session = predictor.handle_request({"type": "start_session", "resource_path": str(frame_dir)})["session_id"]
    try:
        initial = predictor.handle_request({"type": "add_prompt", "session_id": session, "frame_index": 0,
                                            "text": args.target, "output_prob_thresh": .3})
        events = {0: initial["outputs"]}
        for event in predictor.handle_stream_request({"type": "propagate_in_video", "session_id": session,
                                                       "propagation_direction": "forward", "start_frame_index": 0,
                                                       "max_frame_num_to_track": len(frames), "output_prob_thresh": .3}):
            events[int(event["frame_index"])] = event["outputs"]
        writer = cv2.VideoWriter(str(args.output / "overlay-raw.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height))
        rows = []
        for index, frame in enumerate(frames):
            data = events.get(index, {})
            masks = data.get("out_binary_masks", [])
            ids = data.get("out_obj_ids", [])
            scores = data.get("out_probs", [])
            objects = []
            overlay = frame.copy()
            for offset, mask in enumerate(masks):
                m = np.asarray(mask).squeeze().astype(bool)
                if m.shape != (height, width):
                    raise ValueError("Unexpected mask dimensions.")
                obj_id = int(ids[offset])
                mask_name = f"{index:03d}-object-{obj_id}.png"
                cv2.imwrite(str(args.output / mask_name), m.astype("uint8") * 255)
                color = np.array((125, 221, 160))
                overlay[m] = (overlay[m] * .62 + color * .38).astype("uint8")
                contours, _ = cv2.findContours(m.astype("uint8"), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
                cv2.drawContours(overlay, contours, -1, tuple(int(c) for c in color), 1)
                y, x = np.where(m)
                bbox = [int(x.min()), int(y.min()), int(x.max()-x.min()+1), int(y.max()-y.min()+1)] if len(x) else [0, 0, 0, 0]
                objects.append({"id": obj_id, "score": float(scores[offset]), "pixels": int(m.sum()),
                                "mask": mask_name, "bbox": bbox})
            cv2.putText(overlay, f"SAM 3.1 / {args.target}", (14, 26), cv2.FONT_HERSHEY_SIMPLEX, .55, (210, 245, 220), 1)
            writer.write(overlay)
            if index == len(frames) // 2:
                cv2.imwrite(str(args.output / "poster.jpg"), overlay)
            rows.append({"frame": index, "seconds": round(index / fps, 3), "objects": objects})
        writer.release()
        result = {"model": "SAM 3.1", "model_prompt": args.target, "frame_count": len(frames), "sample_fps": fps,
                  "width": width, "height": height, "source_start_seconds": 0,
                  "analysis_seconds": round(len(frames) / fps, 3), "load_seconds": round(load_seconds, 3),
                  "frames_with_detections": sum(bool(r["objects"]) for r in rows), "frames": rows,
                  "provenance": "Fresh offline inference on the uploaded source; no cached demonstration masks.",
                  "interpretation_limits": ["Masks and scores are model predictions, not verified ground truth.",
                                            "Segmentation does not establish weight, temperature, scale, texture or doneness.",
                                            "Object identity can change under occlusion; inspect the source video."]}
        (args.output / "result.json").write_text(json.dumps(result, indent=2))
        print(json.dumps({"frames": len(frames), "frames_with_detections": result["frames_with_detections"]}), flush=True)
    finally:
        predictor.handle_request({"type": "close_session", "session_id": session, "run_gc_collect": True})


if __name__ == "__main__":
    main()
