import asyncio
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

MODULE = Path(__file__).resolve().parents[1] / "sam_service.py"
spec = importlib.util.spec_from_file_location("sam_service", MODULE)
sam = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sam)


class SamServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        (self.root / "media").mkdir()
        self.source = self.root / "media" / "owned.mp4"
        self.source.write_bytes(b"test video contents")
        self.service = sam.SamService(self.root)
        self.service.gpu_lock = self.root / "gpu-lock"

    def tearDown(self):
        self.tmp.cleanup()

    def test_target_and_path_guards(self):
        self.assertEqual(self.service._validate(self.source, "spoon", "abc123"), self.source)
        for target in ["; rm -rf /", "person", "saucepan --name bad", ""]:
            with self.assertRaises(ValueError):
                self.service._validate(self.source, target, "abc123")
        for job_id in ["../outside", "bad id", "x", "bad/id"]:
            with self.assertRaises(ValueError):
                self.service._validate(self.source, "spoon", job_id)
        outside = self.root / "secret.mp4"
        outside.write_bytes(b"private")
        with self.assertRaises(ValueError):
            self.service._validate(outside, "spoon", "abc123")
        alias = self.root / "media" / "alias.mp4"
        alias.symlink_to(outside)
        with self.assertRaises(ValueError):
            self.service._validate(alias, "spoon", "abc123")

    def test_gpu_lock_never_removes_another_owner(self):
        self.service.gpu_lock.mkdir()
        (self.service.gpu_lock / "owner").write_text("someone else")
        self.service._release_gpu("our job")
        self.assertTrue(self.service.gpu_lock.exists())
        with patch.object(self.service, "_studio_busy", return_value=False), patch.object(sam, "_memory_available_gib", return_value=80):
            with self.assertRaises(sam.SamBusy):
                self.service._acquire_gpu("our job")
        self.service._release_gpu("someone else")
        self.assertFalse(self.service.gpu_lock.exists())

    async def test_no_runtime_is_reported_not_simulated(self):
        with patch.object(self.service, "_runtime_status", return_value={"configured": False, "message": "unavailable"}):
            with self.assertRaises(sam.SamUnavailable):
                await self.service.segment(self.source, "spoon", "job123")
        self.assertFalse((self.root / "artifacts").exists())

    async def test_active_job_does_not_queue_a_second_gpu_job(self):
        await self.service._lock.acquire()
        try:
            with self.assertRaises(sam.SamBusy):
                await self.service.segment(self.source, "spoon", "job123")
        finally:
            self.service._lock.release()

    async def test_fresh_worker_result_maps_controlled_artifacts(self):
        calls = []
        async def fake_run(args, timeout, log, **kwargs):
            calls.append(args)
            output = log.parent
            if args[0] == "docker":
                (output / "result.json").write_text(json.dumps({"model": "SAM 3.1", "frames": [], "frames_with_detections": 0}))
                (output / "poster.jpg").write_bytes(b"poster")
                (output / "overlay-raw.mp4").write_bytes(b"raw")
            else:
                Path(args[-1]).write_bytes(b"video")
        with patch.object(self.service, "_runtime_status", return_value={"configured": True, "runtime_image_id": "sha256:test"}), \
             patch.object(self.service, "_studio_busy", return_value=False), \
             patch.object(sam, "_memory_available_gib", return_value=80), \
             patch.object(self.service, "_run", side_effect=fake_run), \
             patch.object(sam.subprocess, "run"):
            result = await self.service.segment(self.source, "food", "job123")
        self.assertEqual(result["status"], "succeeded")
        self.assertEqual(result["artifacts"]["overlay"], "overlay.mp4")
        self.assertEqual(result["source_sha256"], sam._sha256(self.source))
        self.assertEqual(calls[1][-1], "sauce")
        self.assertIn("none", calls[1])
        self.assertFalse(self.service.gpu_lock.exists())
        self.assertTrue((self.root / "artifacts" / "job123" / "result.json").is_file())

    async def test_worker_failure_releases_owned_lock(self):
        async def fake_run(args, timeout, log, **kwargs):
            if args[0] == "docker":
                raise sam.SamFailure("worker failed")
            Path(args[-1]).write_bytes(b"video")
        with patch.object(self.service, "_runtime_status", return_value={"configured": True, "runtime_image_id": "sha256:test"}), \
             patch.object(self.service, "_studio_busy", return_value=False), \
             patch.object(sam, "_memory_available_gib", return_value=80), \
             patch.object(self.service, "_run", side_effect=fake_run), \
             patch.object(sam.subprocess, "run"):
            with self.assertRaises(sam.SamFailure):
                await self.service.segment(self.source, "spoon", "job123")
        self.assertFalse(self.service.gpu_lock.exists())


if __name__ == "__main__":
    unittest.main()
