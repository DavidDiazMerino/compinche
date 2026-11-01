"""Bounded pixel evidence for the conversation, with its original media identity."""


def summarise_job(job, evidence):
    result = job.get("result", {})
    width, height = result.get("width", 0), result.get("height", 0)
    area = width * height
    offset = result.get("source_start_seconds", job.get("start_seconds", 0))
    origin = evidence.get("timestamp_seconds", 0) or 0
    frames = result.get("frames", [])
    samples = []
    # At most nine samples and eight candidate objects per sample.
    indices = sorted(set(list(range(0, len(frames), max(1, len(frames) // 8)))[:8] + ([len(frames)-1] if frames else [])))
    for index in indices:
        frame = frames[index]
        source_seconds = frame.get("source_seconds", offset + frame.get("seconds", 0))
        samples.append({
            "media_seconds": round(source_seconds, 3),
            "original_seconds": round(origin + source_seconds, 3),
            "objects_total": len(frame.get("objects", [])),
            "objects": [{"candidate_track_id": obj["id"], "score": obj.get("score"),
                         "bbox_pixels": obj.get("bbox"),
                         "frame_area_fraction": round(obj.get("pixels", 0) / area, 5) if area else None}
                        for obj in frame.get("objects", [])[:8]],
        })
    return {
        "id": job["id"], "evidence_id": job["evidence_id"], "target": job["target"],
        "model": result.get("model"), "frame_count": result.get("frame_count"),
        "frames_with_detections": result.get("frames_with_detections"),
        "media_start_seconds": offset, "original_start_seconds": origin + offset,
        "analysis_seconds": result.get("analysis_seconds"), "samples": samples,
        "source_sha256": result.get("source_sha256"),
        "interpretation_limits": "Candidate masks locate pixels. Track IDs may change across occlusion or edits. Area changes may be camera motion, not a change in the food. They do not establish texture, weight, temperature, physical size or readiness.",
    }
