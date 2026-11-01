"""Vessel geometry on a synthetic scene with known sizes; no GPU or network."""
import asyncio
import copy
from pathlib import Path
import tempfile
import unittest

import numpy as np

from backend.live import LiveCoordinator
from backend.measure import measure_from_geometry, rim_ellipse
from backend.store import Store

F = 500.0


def scene(diameter_m=0.28, depth=1.5, tilt_degrees=70, carton_height=0.18, carton_depth=1.6, scale=1.0, size=(300, 520)):
    """Points with a pinhole camera; MapAnything's scale error is simulated with `scale`."""
    h, w = size
    K = np.array([[F, 0, w / 2], [0, F, h / 2], [0, 0, 1]])
    ys, xs = np.mgrid[0:h, 0:w]
    z = np.full((h, w), 3.0)
    vessel = np.zeros((h, w), bool)
    a = diameter_m / 2 * F / depth
    b = a * np.cos(np.radians(tilt_degrees))
    cx, cy = w / 2, h / 2
    rim = ((xs - cx) / a) ** 2 + ((ys - cy) / b) ** 2 <= 1
    body = (np.abs(xs - cx) <= a) & (ys >= cy) & (ys <= cy + 0.6 * a)
    vessel = rim | body
    z[vessel] = depth
    carton = np.zeros((h, w), bool)
    ch = carton_height * F / carton_depth
    carton[int(cy - ch / 2):int(cy + ch / 2), int(w * .85):int(w * .85) + 20] = True
    z[carton] = carton_depth
    pts = np.stack([(xs - cx) * z / F, (ys - cy) * z / F, z], -1) * scale
    return pts, K, vessel, carton


REFERENCE = {"concept": "milk carton", "label": "brick", "dimension": "height", "range_cm": (16.5, 19.5)}


def test_reference_recalibrates_a_scale_error_and_reports_a_range():
    pts, K, vessel, carton = scene(scale=1.45)
    result = measure_from_geometry(pts, K, vessel, carton, REFERENCE)
    assert result["status"] == "measured"
    assert result["raw_diameter_cm"] > 36  # the uncorrected metric scale is wrong
    assert 25 <= result["diameter_cm"] <= 31
    assert result["range_cm"][0] < result["diameter_cm"] < result["range_cm"][1]
    assert result["reference"]["label"] == "brick"


def test_oblique_view_is_flagged_and_top_view_is_good():
    pts, K, vessel, carton = scene(tilt_degrees=75)
    assert measure_from_geometry(pts, K, vessel, carton, REFERENCE)["view"] == "oblique"
    pts, K, vessel, carton = scene(tilt_degrees=20)
    top = measure_from_geometry(pts, K, vessel, carton, REFERENCE)
    assert top["view"] == "good" and 25 <= top["diameter_cm"] <= 31


def test_without_reference_the_range_is_wide_and_unlabelled():
    pts, K, vessel, _ = scene()
    result = measure_from_geometry(pts, K, vessel)
    assert result["reference"] is None
    assert result["range_cm"][1] - result["range_cm"][0] > 0.4 * result["diameter_cm"]


def test_no_rim_is_not_a_measurement():
    assert rim_ellipse(np.zeros((50, 50), bool)) is None


def perception():
    return {"observation": "Una olla en el fuego.", "phase": None, "gap": None, "segmentation_targets": []}


class Provider:
    model = "fake"
    async def live_observe(self, context, media):
        return perception()


class FakeMeasure:
    def __init__(self, store, results):
        self.store, self.results, self.calls = store, list(results), []
    async def measure(self, sid, evidence_id, vessel="pot"):
        self.calls.append(vessel)
        return self.store.put(sid, "measurement", copy.deepcopy(self.results.pop(0)))


def measured(view, diameter=27.0):
    return {"status": "measured", "vessel": "pot", "diameter_cm": diameter, "range_cm": [diameter - 2, diameter + 2],
            "raw_diameter_cm": 40.0, "tilt_degrees": 77 if view == "oblique" else 15, "view": view,
            "reference": {"label": "brick de leche de 1 L"}, "overlay_evidence_id": None}


class _LiveBase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.tick = 100.0
    async def asyncTearDown(self):
        await self.co.close()
        self.store.close()
        self.tmp.cleanup()
    def make(self, results):
        self.measure = FakeMeasure(self.store, results)
        self.co = LiveCoordinator(self.store, Provider(), clock=lambda: self.tick, cooldown=0,
                                  measure=self.measure, measure_interval=0)
    async def observe(self, sid, seq):
        path = self.store.root / "media" / f"f{seq}.jpg"
        path.write_bytes(b"jpeg")
        image = self.store.put(sid, "evidence", {"kind": "image", "path": str(path), "timestamp_seconds": seq + 1, "scene_epoch": 0})
        await self.co.observe(sid, {"key": f"o{seq}", "sequence": seq, "scene_epoch": 0, "evidence_ids": [image["id"]],
                                    "start_seconds": seq, "end_seconds": seq + 1})
    async def until(self, condition):
        for _ in range(300):
            if condition():
                return
            await asyncio.sleep(.002)
        self.fail("state not reached")

class MeasureLiveTests(_LiveBase):
    async def test_oblique_measurement_asks_for_top_view_and_stops_after_a_good_one(self):
        self.make([measured("oblique"), measured("good", 28.0), measured("good", 31.0), measured("good", 27.0)])
        sid = (await self.co.create({"consent": True, "source_kind": "replay", "teacher": "Mamá"}))["id"]
        await self.observe(sid, 0)
        await self.until(lambda: self.co.snapshot(sid)["question"] is not None)
        ask = self.co.snapshot(sid)["question"]
        self.assertEqual((ask["kind"], ask["gap_key"]), ("view_request", "measure:view"))
        self.assertIn("desde arriba", ask["text"])
        await self.co.control(sid, {"action": "spoken", "question_id": ask["id"]})
        await self.co.control(sid, {"action": "speaking", "channel": "agent", "speaking": False})
        self.assertIsNone(self.co.snapshot(sid)["question"])
        for seq in (1, 2, 3):
            await self.observe(sid, seq)
            await self.until(lambda: self.co.snapshot(sid)["tools"].get("measurement", {}).get("samples") == seq
                             and self.co.snapshot(sid)["tools"]["measurement"].get("view") == "good")
        tool = self.co.snapshot(sid)["tools"]["measurement"]
        self.assertEqual((tool["diameter_cm"], tool["samples"], tool["range_cm"]), (28.0, 3, [25.0, 33.0]))
        await self.observe(sid, 4)
        await self.until(lambda: self.co.snapshot(sid)["metrics"]["observations_analysed"] == 5)
        self.assertEqual(len(self.measure.calls), 4)  # stops after three top-view samples

    async def test_learner_vessel_is_compared_with_the_teachers(self):
        teacher = self.store.create_session({"title": "Croquetas", "mode": "record", "teacher": "Mamá"})
        self.store.put(teacher["id"], "measurement", measured("good", 28.0))
        self.store.put(teacher["id"], "knowledge", {"text": "x", "confirmed": True})
        self.make([measured("good", 24.0)])
        sid = (await self.co.create({"consent": True, "source_kind": "phone", "teacher": "David",
                                     "mode": "learn", "lesson_session_id": teacher["id"]}))["id"]
        await self.observe(sid, 0)
        await self.until(lambda: self.co.snapshot(sid)["question"] is not None)
        guide = self.co.snapshot(sid)["question"]
        self.assertEqual(guide["gap_key"], "measure:compare")
        self.assertIn("24", guide["text"]) ; self.assertIn("28", guide["text"]) ; self.assertIn("73%", guide["text"])
        self.assertAlmostEqual(self.store.list(sid, "comparison")[0]["area_ratio"], (24 / 28) ** 2, places=3)


class FakeGesture:
    def __init__(self, store):
        self.store, self.calls = store, []
    async def compare(self, sid, teacher, learner, teacher_said, teacher_name, learner_name):
        self.calls.append((teacher, learner, teacher_said))
        return self.store.put(sid, "gesture_comparison", {"same_gesture": False, "difference": "Tú dejas las varillas dentro.",
            "suggestion": "Levántalas alto.", "phase": "levantar", "split_video_evidence_id": "v", "still_evidence_id": "i"})


class GestureLiveTests(_LiveBase):
    async def test_invited_repeat_is_compared_with_the_teachers_demonstration(self):
        video = self.store.put("x", "evidence", {"kind": "video", "path": "/teacher.mp4"})
        teacher = self.store.create_session({"title": "Croquetas", "mode": "record", "teacher": "Papá",
            "live": {"config": {"source_kind": "replay", "source_evidence_id": video["id"]}}})
        card = self.store.put(teacher["id"], "knowledge", {"text": "Levanto las varillas y miro cómo cae.", "quote": "Levanto las varillas y miro cómo cae.",
            "confirmed": True, "decision_card": {"speaker": "Papá", "source_start_seconds": 700, "source_end_seconds": 704}})
        class GuideProvider(Provider):
            async def live_guide(self, context):
                return {"addressed": True, "known": True, "invite_repeat": True, "answer": "Pasa las varillas como te enseñó.", "card_ids": [card["id"]]}
        mine = self.store.put("y", "evidence", {"kind": "video", "path": "/learner.mp4"})
        self.gesture = FakeGesture(self.store)
        self.co = LiveCoordinator(self.store, GuideProvider(), clock=lambda: self.tick, cooldown=0, gesture=self.gesture, gesture_seconds=2)
        sid = (await self.co.create({"consent": True, "source_kind": "replay", "source_evidence_id": mine["id"], "teacher": "David",
                                     "mode": "learn", "lesson_session_id": teacher["id"]}))["id"]
        await self.co.transcript(sid, {"key": "ask", "text": "¿Y esto ya está?", "origin": "typed", "role": "cook", "speaker": "David",
                                       "start_seconds": 10, "end_seconds": 10, "scene_epoch": 0})
        await self.until(lambda: self.co.snapshot(sid)["question"] is not None)
        guide = self.co.snapshot(sid)["question"]
        await self.co.control(sid, {"action": "spoken", "question_id": guide["id"]})
        await self.co.control(sid, {"action": "speaking", "channel": "agent", "speaking": False})
        for seq in (10, 11, 12):
            await self.observe(sid, seq)
            await self.until(lambda: self.co.snapshot(sid)["metrics"]["observations_analysed"] == seq - 9)
        await self.until(lambda: self.co.snapshot(sid)["question"] is not None)
        teacher_window, learner_window, said = self.gesture.calls[0]
        self.assertEqual((teacher_window["video"], teacher_window["start"], teacher_window["end"]), ("/teacher.mp4", 698, 708))
        self.assertEqual((learner_window["video"], learner_window["start"], learner_window["end"]), ("/learner.mp4", 10, 12))
        self.assertIn("varillas", said)
        self.assertIn("Mira esta diferencia", self.co.snapshot(sid)["question"]["text"])


class TimelineTests(_LiveBase):
    async def test_heat_changes_and_phases_build_the_timeline(self):
        outputs = [{**perception(), "phase": "sofrito de harina", "heat_change": "up"},
                   {**perception(), "phase": "sofrito de harina"},
                   {**perception(), "phase": "añadir leche", "heat_change": "down"}]
        class Scripted(Provider):
            async def live_observe(self, context, media):
                self.context = context
                return outputs.pop(0)
        provider = Scripted()
        self.co = LiveCoordinator(self.store, provider, clock=lambda: self.tick, cooldown=0)
        sid = (await self.co.create({"consent": True, "source_kind": "replay", "teacher": "Papá"}))["id"]
        for seq in range(3):
            await self.observe(sid, seq)
            await self.until(lambda: self.co.snapshot(sid)["metrics"]["observations_analysed"] == seq + 1)
        timeline = self.co.timeline(sid)
        self.assertEqual([h["kind"] for h in timeline["heat"]], ["up", "down"])
        self.assertEqual([p["name"] for p in timeline["phases"]], ["sofrito de harina", "añadir leche"])
        self.assertEqual(timeline["phases"][0]["end"], 2)
        self.assertEqual([h["kind"] for h in provider.context["heat_timeline"]], ["up"])  # Spark can ask about it


class LearnerPriorityTests(_LiveBase):
    async def test_learner_question_is_answered_while_a_view_request_is_pending(self):
        teacher = self.store.create_session({"title": "Croquetas", "mode": "record", "teacher": "Papá"})
        card = self.store.put(teacher["id"], "knowledge", {"text": "Templada.", "quote": "Templada.", "confirmed": True, "decision_card": {"speaker": "Papá"}})
        class GuideProvider(Provider):
            async def live_guide(self, context):
                return {"addressed": True, "known": True, "invite_repeat": False, "answer": "Papá la echaba templada.", "card_ids": [card["id"]]}
        self.co = LiveCoordinator(self.store, GuideProvider(), clock=lambda: self.tick, cooldown=0)
        sid = (await self.co.create({"consent": True, "source_kind": "phone", "teacher": "David", "mode": "learn", "lesson_session_id": teacher["id"]}))["id"]
        self.co._offer_prompt(self.co.runs[sid], "view_request", "¿Me enseñas la sartén desde arriba?", "r", "measure:view",
                              {"start_seconds": 0, "end_seconds": 1}, [])
        await self.co.transcript(sid, {"key": "ask", "text": "¿La leche era fría?", "origin": "microphone", "role": "cook",
            "speaker": "David", "start_seconds": 2, "end_seconds": 2, "scene_epoch": 0, "answer_to": "anything"})
        await self.until(lambda: (self.co.snapshot(sid)["question"] or {}).get("trigger") == "answer")
        self.assertIn("templada", self.co.snapshot(sid)["question"]["text"])
