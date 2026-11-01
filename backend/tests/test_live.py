"""Logical acceptance of the live loop; no provider, microphone or GPU claims."""
import asyncio
import copy
import json
from pathlib import Path
import tempfile
import unittest

from backend.live import LiveCoordinator, LiveError
from backend.store import Store


def perception(key="stop-cue", *, target=None):
    return {"observation": "La persona remueve la mezcla y levanta la cuchara.", "phase": "remover",
        "gap": {"key": key, "question": "¿Qué señal usas para decidir cuándo parar?", "reason": "El criterio no se ha explicado.",
            "action": "Remover y levantar la cuchara", "cue": "La mezcla cae de la cuchara", "decision": None,
            "target": target, "request_kind": "explain"}, "segmentation_targets": [target] if target else []}


class Provider:
    model = "fake-live-for-tests-only"
    def __init__(self, outputs=None, gate=None):
        self.calls, self.outputs, self.gate = [], outputs or [], gate
        self.called = asyncio.Event()
    async def live_observe(self, context, media):
        index = len(self.calls)
        self.calls.append((copy.deepcopy(context), copy.deepcopy(media)))
        self.called.set()
        if index == 0 and self.gate:
            await self.gate.wait()
        value = self.outputs[index] if index < len(self.outputs) else perception()
        if isinstance(value, Exception):
            raise value
        return copy.deepcopy(value)


class LiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name))
        self.tick, self.wall = 100.0, 1_700_000_000.0
        self.provider = Provider()
        self.co = self.make(self.provider)
    def make(self, provider, **settings):
        return LiveCoordinator(self.store, provider, clock=lambda: self.tick, wall_clock=lambda: self.wall,
                               cooldown=0, **settings)
    async def asyncTearDown(self):
        await self.co.close()
        self.store.close()
        self.tmp.cleanup()
    async def start(self, **settings):
        return (await self.co.create({"consent": True, "teacher": "Papá", "speaker_name": "David",
                                      "source_kind": "replay", **settings}))["id"]
    async def until(self, condition):
        for _ in range(300):
            if condition():
                return
            await asyncio.sleep(.002)
        self.fail("Live task did not reach the expected state")
    def media(self, sid, second, epoch=0, kind="image"):
        path = self.store.root / "media" / f"fixture-{sid}-{second}-{epoch}-{kind}"
        path.write_bytes(b"test-only-media-not-submitted-to-a-real-provider")
        return self.store.put(sid, "evidence", {"kind": kind, "path": str(path),
            "timestamp_seconds": second, "source_end_seconds": second, "scene_epoch": epoch})
    async def observe(self, sid, seq=0, second=1, epoch=0):
        image = self.media(sid, second, epoch)
        payload = {"key": f"obs-{seq}", "sequence": seq, "scene_epoch": epoch, "evidence_ids": [image["id"]],
                   "start_seconds": max(0, second-1), "end_seconds": second}
        return await self.co.observe(sid, payload), payload
    async def question(self, sid):
        await self.observe(sid)
        await self.until(lambda: self.co.snapshot(sid)["question"] is not None)
        return self.co.snapshot(sid)["question"]
    async def speak(self, sid, question):
        await self.co.control(sid, {"action": "spoken", "question_id": question["id"]})
        await self.co.control(sid, {"action": "speaking", "channel": "agent", "speaking": False})
    def answer(self, question, **changes):
        return {"key": "reply-1", "text": "Cuando el surco queda abierto y noto resistencia.", "speaker": "David",
                "role": "wearer", "origin": "microphone", "answer_to": question["id"],
                "start_seconds": 1, "end_seconds": 1, "started_wall": self.wall+.01, "scene_epoch": 0, **changes}

    async def test_fresh_teaching_context_never_inherits_preanalysed_future(self):
        teacher = self.store.create_session({"mode": "record", "title": "Original", "teacher": "Papá"})
        original = self.media(teacher["id"], 0, kind="video")
        self.store.put(teacher["id"], "note", {"text": "SECRET FUTURE ENDING", "timestamp_seconds": 700})
        self.store.put(teacher["id"], "knowledge", {"text": "SECRET FUTURE RULE", "confirmed": True})
        sid = await self.start(source_evidence_id=original["id"])
        await self.observe(sid)
        await self.until(lambda: len(self.provider.calls) == 1)
        context = self.provider.calls[0][0]
        self.assertNotIn("SECRET", json.dumps(context))
        self.assertIsNone(self.store.get_session(sid)["source_session_id"])
        self.assertEqual(context["source_cutoff_seconds"], 1)
        foreign = {"key": "foreign", "sequence": 1, "start_seconds": 0, "end_seconds": 2,
                   "evidence_ids": [original["id"]]}
        with self.assertRaisesRegex(LiveError, "belong"):
            await self.co.observe(sid, foreign)
        future = self.media(sid, 500)
        with self.assertRaisesRegex(LiveError, "future"):
            await self.co.observe(sid, {**foreign, "key": "future", "evidence_ids": [future["id"]]})

    async def test_busy_vision_retains_only_latest_queued_window_without_blocking_capture(self):
        gate = asyncio.Event()
        await self.co.close()
        self.provider = Provider(gate=gate)
        self.co = self.make(self.provider)
        sid = await self.start()
        await self.observe(sid, 0, 1)
        await self.provider.called.wait()
        await self.observe(sid, 1, 2)
        await self.observe(sid, 2, 3)
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.co.snapshot(sid)["queue_depth"], 1)
        self.assertEqual(self.co.snapshot(sid)["metrics"]["observations_superseded"], 1)
        gate.set()
        await self.until(lambda: self.co.snapshot(sid)["metrics"]["observations_analysed"] == 2)
        self.assertEqual([c[0]["source_cutoff_seconds"] for c in self.provider.calls], [1, 3])
        self.assertEqual(len(self.store.list(sid, "question")), 1)
        self.assertEqual(self.co.snapshot(sid)["question"]["source_end_seconds"], 1)

    async def test_question_anchors_each_visual_frame_to_its_actual_time(self):
        sid = await self.start()
        frames = [self.media(sid, second) for second in (1, 2, 3)]
        await self.co.observe(sid, {"key": "multi-frame", "sequence": 0, "scene_epoch": 0,
            "start_seconds": 1, "end_seconds": 3, "evidence_ids": [frame["id"] for frame in frames]})
        await self.until(lambda: self.co.snapshot(sid)["question"] is not None)
        self.assertEqual(self.co.snapshot(sid)["question"]["media_anchors"],
            [{"evidence_id": frame["id"], "original_start_seconds": second} for second, frame in zip((1, 2, 3), frames)])

    async def test_delayed_question_is_retrospective_with_same_id_and_original_proposal(self):
        original = "¿Qué indica que hay que mover la olla ahora en este paso?"
        delayed = perception()
        delayed["gap"]["question"] = original
        self.provider.outputs = [delayed] * 4
        # Either elapsed processing/waiting time or source movement is sufficient.
        for wait, source_advance in ((4, 0), (1, 4)):
            with self.subTest(wait=wait, source_advance=source_advance):
                sid = await self.start()
                await self.co.control(sid, {"action": "speaking", "channel": "source", "speaking": True})
                await self.observe(sid)
                await self.until(lambda: self.co.snapshot(sid)["metrics"]["observations_analysed"] == 1)
                candidate = self.store.list(sid, "question")[0]
                self.assertEqual(candidate["text"], original)
                self.assertIsNone(self.co.snapshot(sid)["question"])
                self.tick += wait
                self.wall += wait
                if source_advance:
                    await self.observe(sid, seq=1, second=1+source_advance)
                await self.co.control(sid, {"action": "speaking", "channel": "source", "speaking": False})
                question = self.co.snapshot(sid)["question"]
                self.assertEqual(question["id"], candidate["id"])
                self.assertEqual(question["text"], "Sobre el gesto que vi hace un momento: ¿Qué indica que hay que mover la olla en ese momento en ese paso?")
                self.assertEqual(question["proposed_text"], original)
                self.assertEqual(question["original_question"], original)
                self.assertEqual(question["temporal_reference"]["kind"], "retrospective")
                self.assertEqual(question["temporal_reference"]["source_lag_seconds"], source_advance)
                self.assertEqual(question["temporal_reference"]["observation_age_seconds"], wait)
                self.assertEqual(question["source_end_seconds"], 1)

    async def test_seek_invalidates_inflight_work_and_all_previous_epoch_context(self):
        gate = asyncio.Event()
        await self.co.close()
        self.provider = Provider([perception("old"), perception("new")], gate)
        self.co = self.make(self.provider)
        sid = await self.start()
        await self.co.transcript(sid, {"key": "old-note", "text": "OLD EPOCH WORDS", "origin": "recorded_source",
            "speaker": "Grabación sin verificar", "role": "observer", "start_seconds": 0, "end_seconds": 0, "scene_epoch": 0})
        await self.observe(sid)
        await self.provider.called.wait()
        await self.co.control(sid, {"action": "seek", "source_seconds": 0, "scene_epoch": 1})
        await self.observe(sid, 0, 1, 1)
        gate.set()
        await self.until(lambda: self.co.snapshot(sid)["question"] is not None)
        snapshot = self.co.snapshot(sid)
        self.assertEqual(snapshot["metrics"]["stale_results"], 1)
        self.assertEqual(snapshot["question"]["gap_key"], "new")
        self.assertNotIn("OLD EPOCH", json.dumps(self.provider.calls[-1][0]))
        stale = await self.co.transcript(sid, {"key": "late-asr", "text": "Late old explanation", "scene_epoch": 0})
        self.assertEqual(stale["ignored"], "stale_capture")
        with self.assertRaisesRegex(LiveError, "advance"):
            await self.co.control(sid, {"action": "seek", "source_seconds": 0, "scene_epoch": 1})

    async def test_question_is_not_exposed_until_person_stops_speaking(self):
        sid = await self.start()
        await self.co.control(sid, {"action": "speaking", "channel": "human", "speaking": True})
        await self.observe(sid)
        await self.until(lambda: self.co.snapshot(sid)["metrics"]["observations_analysed"] == 1)
        self.assertIsNone(self.co.snapshot(sid)["question"])
        self.assertTrue(any(e["type"] == "gap_candidate" for e in self.co.snapshot(sid)["events"]))
        await self.co.control(sid, {"action": "speaking", "channel": "human", "speaking": False})
        self.assertEqual(self.co.snapshot(sid)["question"]["status"], "pending")

    async def test_spoken_question_genuine_reply_and_actor_attribution_are_linked(self):
        sid = await self.start()
        q = await self.question(sid)
        premature = await self.co.transcript(sid, self.answer(q, key="early"))
        self.assertFalse(premature["answered"])
        self.assertEqual(premature["ignored"], "question_not_spoken_before_utterance")
        await self.speak(sid, q)
        original_words = await self.co.transcript(sid, self.answer(q, key="original-audio", origin="recorded_source"))
        self.assertFalse(original_words["answered"])
        echo = await self.co.transcript(sid, self.answer(q, key="echo", text=q["text"]))
        self.assertEqual(echo["ignored"], "agent_echo")
        audio = self.media(sid, 1, kind="audio")
        actual = self.answer(q, evidence_ids=[audio["id"]], automatic=True, asr_model="test-asr")
        answer = await self.co.transcript(sid, actual)
        self.assertTrue(answer["answered"])
        card = answer["card"]
        self.assertEqual(card["speaker"], "David")
        self.assertEqual(card["speaker_role"], "wearer")
        self.assertFalse(card["confirmed"])
        self.assertEqual(card["quote"], actual["text"])
        self.assertEqual(card["decision_card"]["answer_evidence_ids"], [audio["id"]])
        self.assertEqual(card["decision_card"]["speaker_verification"], "user_declared")
        self.assertIsNone(self.co.snapshot(sid)["question"])
        self.assertEqual(self.store.get(q["id"], "question")["status"], "answered")
        retry = await self.co.transcript(sid, actual)
        self.assertTrue(retry["cached"])
        self.assertEqual(len(self.co.snapshot(sid)["cards"]), 1)
        with self.assertRaisesRegex(LiveError, "another payload"):
            await self.co.transcript(sid, {**actual, "text": "Changed words"})

    async def test_delayed_asr_cannot_answer_a_new_question_or_pre_spoken_audio(self):
        self.provider.outputs = [perception("one"), perception("two")]
        sid = await self.start()
        first = await self.question(sid)
        await self.speak(sid, first)
        before_speech = await self.co.transcript(sid, self.answer(first, key="pre", started_wall=self.wall-1))
        self.assertFalse(before_speech["answered"])
        await self.co.control(sid, {"action": "postpone", "question_id": first["id"]})
        await self.observe(sid, 1, 2)
        await self.until(lambda: self.co.snapshot(sid)["question"] is not None)
        second = self.co.snapshot(sid)["question"]
        self.assertEqual(second["gap_key"], "two")
        await self.speak(sid, second)
        delayed = await self.co.transcript(sid, self.answer(first, key="late"))
        self.assertFalse(delayed["answered"])
        self.assertEqual(delayed["ignored"], "question_not_current")
        self.assertEqual(self.co.snapshot(sid)["question"]["id"], second["id"])
        self.assertEqual(self.store.get(first["id"], "question")["status"], "postponed")

    async def test_now_not_and_unknown_keep_gaps_unanswered(self):
        self.provider.outputs = [perception("one"), perception("two")]
        sid = await self.start()
        first = await self.question(sid)
        await self.speak(sid, first)
        postponed = await self.co.transcript(sid, self.answer(first, text="Ahora no, espera."))
        self.assertEqual(postponed["ignored"], "postponed")
        self.assertFalse(postponed["answered"])
        await self.observe(sid, 1, 2)
        await self.until(lambda: self.co.snapshot(sid)["question"] is not None)
        second = self.co.snapshot(sid)["question"]
        await self.speak(sid, second)
        unknown = await self.co.transcript(sid, self.answer(second, key="unknown", text="No lo sé."))
        self.assertEqual(unknown["ignored"], "unresolved")
        self.assertEqual(self.store.get(second["id"], "question")["status"], "unresolved")
        self.assertEqual(self.co.snapshot(sid)["cards"], [])

    async def test_unanswered_question_expires_without_becoming_answered(self):
        self.provider.outputs = [perception("old"), perception("new")]
        sid = await self.start()
        first = await self.question(sid)
        await self.speak(sid, first)
        self.tick += 80
        self.wall += 80
        await self.observe(sid, 1, 81)
        await self.until(lambda: self.co.snapshot(sid)["question"] is not None)
        self.assertEqual(self.store.get(first["id"], "question")["status"], "expired")
        self.assertEqual(self.co.snapshot(sid)["question"]["gap_key"], "new")
        self.assertEqual(self.co.snapshot(sid)["cards"], [])

    async def test_vision_failure_does_not_stop_future_capture_or_invent_questions(self):
        self.provider.outputs = [RuntimeError("provider down"), perception()]
        sid = await self.start()
        receipt, payload = await self.observe(sid)
        await self.until(lambda: self.co.snapshot(sid)["metrics"]["provider_failures"] == 1)
        self.assertIsNone(self.co.snapshot(sid)["question"])
        retry = await self.co.observe(sid, payload)
        self.assertTrue(retry["cached"])
        self.assertEqual(len(self.provider.calls), 1)
        await self.observe(sid, 1, 2)
        await self.until(lambda: self.co.snapshot(sid)["question"] is not None)
        self.assertEqual(len(self.provider.calls), 2)
        events = self.co.snapshot(sid)["events"]
        self.assertEqual([e["id"] for e in events], sorted(set(e["id"] for e in events)))
        last = events[-1]["id"]
        self.assertEqual(self.co.snapshot(sid, last)["events"], [])

    async def test_semantic_visual_quantities_need_an_actual_spoken_source(self):
        result = perception()
        result["observation"] = "Veo 20 gramos de mantequilla."
        self.provider.outputs = [result]
        sid = await self.start()
        await self.observe(sid)
        await self.until(lambda: self.co.snapshot(sid)["metrics"]["provider_failures"] == 1)
        self.assertIsNone(self.co.snapshot(sid)["question"])
        self.assertEqual(self.co.snapshot(sid)["cards"], [])

    async def test_segmentation_is_background_and_uses_the_matching_causal_window(self):
        gate, began = asyncio.Event(), asyncio.Event()
        captured = []
        async def segment(media, target, sid):
            captured.append((copy.deepcopy(media), target, sid))
            began.set()
            await gate.wait()
            return {"frames_with_detections": 2, "model": "test-masks-only"}
        await self.co.close()
        self.provider = Provider([perception(target="spoon"), perception(), perception()])
        self.co = self.make(self.provider, segment_observation=segment, segmentation_cooldown=0)
        sid = await self.start()
        await self.observe(sid)
        await began.wait()
        self.assertIsNotNone(self.co.snapshot(sid)["question"])
        self.assertEqual(self.co.snapshot(sid)["tools"]["segmentation"]["status"], "running")
        await self.observe(sid, 1, 2)
        await self.until(lambda: len(self.provider.calls) == 2)
        self.assertEqual(self.provider.calls[1][0]["sam_results"], [])
        gate.set()
        await self.until(lambda: self.co.snapshot(sid)["tools"]["segmentation"]["status"] == "completed")
        await self.observe(sid, 2, 3)
        await self.until(lambda: len(self.provider.calls) == 3)
        result = self.provider.calls[2][0]["sam_results"][0]
        self.assertEqual(result["end_seconds"], 1)
        self.assertEqual(result["evidence_ids"], [captured[0][0][0]["id"]])
        self.assertEqual(result["scene_epoch"], 0)

    async def test_pause_cancels_segmentation_and_seek_rejects_late_masks(self):
        began, cancelled = asyncio.Event(), asyncio.Event()
        async def segment(*args):
            began.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        await self.co.close()
        self.provider = Provider([perception(target="hand")])
        self.co = self.make(self.provider, segment_observation=segment)
        sid = await self.start()
        await self.observe(sid)
        await began.wait()
        await self.co.control(sid, {"action": "pause"})
        await cancelled.wait()
        self.assertEqual(self.co.snapshot(sid)["status"], "paused")
        self.assertEqual(self.co.snapshot(sid)["tools"]["segmentation"]["status"], "paused")

    async def test_segmentation_reports_actual_shortened_window(self):
        async def segment(*args):
            return {"original_start_seconds": 8, "original_end_seconds": 10, "frames_with_detections": 0}
        await self.co.close()
        self.provider = Provider([perception(target="spoon")])
        self.co = self.make(self.provider, segment_observation=segment)
        sid = await self.start()
        frames = [self.media(sid, second) for second in (4, 7, 10)]
        await self.co.observe(sid, {"key": "short-sam", "sequence": 0, "start_seconds": 4,
            "end_seconds": 10, "evidence_ids": [frame["id"] for frame in frames]})
        await self.until(lambda: self.co.snapshot(sid)["tools"]["segmentation"]["status"] == "completed")
        summary = self.co.snapshot(sid)["tools"]["segmentation"]
        self.assertEqual((summary["start_seconds"], summary["end_seconds"]), (8, 10))
        self.assertEqual(summary["result"]["frames_with_detections"], 0)

    async def test_segmentation_rejects_result_with_future_source_interval(self):
        async def segment(*args):
            return {"original_start_seconds": 1, "original_end_seconds": 11}
        await self.co.close()
        self.provider = Provider([perception(target="spoon")])
        self.co = self.make(self.provider, segment_observation=segment)
        sid = await self.start()
        await self.observe(sid, second=10)
        await self.until(lambda: self.co.snapshot(sid)["tools"]["segmentation"]["status"] == "unavailable")
        self.assertEqual(self.co.runs[sid].segmentation, [])
        self.assertTrue(any(e["type"] == "tool_error" for e in self.co.snapshot(sid)["events"]))

    async def test_learning_derivation_is_nonblocking_and_never_rewrites_literal_answer(self):
        gate, began = asyncio.Event(), asyncio.Event()
        class LearningProvider(Provider):
            async def live_learn(self, context):
                self.learning_context = copy.deepcopy(context)
                began.set()
                await gate.wait()
                return {"summary": "David explica su criterio", "signal": "El surco queda abierto",
                        "reported_sensation": "Noto resistencia", "decision": "Parar al reconocer la señal"}
        await self.co.close()
        self.provider = LearningProvider()
        self.co = self.make(self.provider)
        sid = await self.start()
        q = await self.question(sid)
        await self.speak(sid, q)
        payload = self.answer(q)
        answer = await self.co.transcript(sid, payload)
        await began.wait()
        self.assertTrue(answer["answered"])
        self.assertIsNone(self.co.snapshot(sid)["question"])
        self.assertIsNone(answer["card"]["decision_card"]["decision"])
        gate.set()
        await self.until(lambda: self.co.snapshot(sid)["cards"][0]["decision_card"].get("derived_by"))
        card = self.co.snapshot(sid)["cards"][0]
        self.assertEqual(card["quote"], payload["text"])
        self.assertEqual(card["text"], payload["text"])
        self.assertEqual(card["decision_card"]["explanation"], payload["text"])
        self.assertFalse(card["confirmed"])
        self.assertFalse(card["decision_card"]["derivation_confirmed"])
        self.assertEqual(card["decision_card"]["signal"], "El surco queda abierto")

    async def test_unrelated_reply_keeps_question_open_and_relevant_reply_closes_it(self):
        class CheckingProvider(Provider):
            async def live_answer_check(self, context):
                related = "surco" in context["human_reply"]
                return {"status": "explains" if related else "unrelated", "reason": "test"}
        await self.co.close()
        self.provider = CheckingProvider()
        self.co = self.make(self.provider)
        sid = await self.start()
        q = await self.question(sid)
        await self.speak(sid, q)
        off = await self.co.transcript(sid, self.answer(q, key="off", text="Mañana quiero ir al cine."))
        self.assertFalse(off["answered"])
        self.assertEqual(off["ignored"], "unrelated_to_question")
        self.assertEqual(self.co.snapshot(sid)["question"]["status"], "spoken")
        self.assertEqual(self.co.snapshot(sid)["cards"], [])
        on = await self.co.transcript(sid, self.answer(q, key="on"))
        self.assertTrue(on["answered"])
        self.assertEqual(on["card"]["relevance"]["status"], "explains")
        self.assertIsNone(self.co.snapshot(sid)["question"])

    async def test_review_wins_over_inflight_derived_fields(self):
        gate, began = asyncio.Event(), asyncio.Event()
        class LearningProvider(Provider):
            async def live_learn(self, context):
                began.set()
                await gate.wait()
                return {"summary": "Model paraphrase", "signal": None, "reported_sensation": None, "decision": "Model rule"}
        await self.co.close()
        self.provider = LearningProvider()
        self.co = self.make(self.provider)
        sid = await self.start()
        q = await self.question(sid)
        await self.speak(sid, q)
        answer = await self.co.transcript(sid, self.answer(q))
        await began.wait()
        await self.co.control(sid, {"action": "confirm", "card_id": answer["card"]["id"]})
        gate.set()
        await asyncio.sleep(.02)
        card = self.co.snapshot(sid)["cards"][0]
        self.assertTrue(card["confirmed"])
        self.assertIsNone(card["decision_card"]["decision"])

    async def test_live_learner_receives_only_confirmed_previous_teaching_rules(self):
        teacher = self.store.create_session({"title": "Past teaching", "mode": "record"})
        self.store.put(teacher["id"], "knowledge", {"text": "A confirmed decision", "confirmed": True})
        self.store.put(teacher["id"], "knowledge", {"text": "UNREVIEWED GUESS", "confirmed": False})
        self.store.put(teacher["id"], "note", {"text": "FUTURE RANDOM SOURCE SUMMARY", "timestamp_seconds": 900})
        sid = await self.start(mode="learn", lesson_session_id=teacher["id"])
        await self.observe(sid)
        await self.until(lambda: len(self.provider.calls) == 1)
        context = self.provider.calls[0][0]
        self.assertEqual(context["confirmed_lesson"][0]["text"], "A confirmed decision")
        self.assertNotIn("UNREVIEWED", json.dumps(context))
        self.assertNotIn("RANDOM SOURCE", json.dumps(context))

    async def test_asr_pending_fences_candidate_until_new_explanation_is_available(self):
        sid = await self.start()
        await self.co.speech_processing(sid, "turn-1", True, 0)
        await self.co.control(sid, {"action": "speaking", "channel": "human", "speaking": False})
        await self.observe(sid)
        await self.until(lambda: self.co.snapshot(sid)["metrics"]["observations_analysed"] == 1)
        self.assertIsNone(self.co.snapshot(sid)["question"])
        self.assertEqual(self.co.snapshot(sid)["speech_pending"], 1)
        await self.co.transcript(sid, {"key": "explanation", "text": "Paro cuando el surco se mantiene.",
            "origin": "microphone", "role": "cook", "speaker": "Papá", "start_seconds": 1, "end_seconds": 1,
            "scene_epoch": 0, "started_wall": self.wall})
        await self.co.speech_processing(sid, "turn-1", False, 0)
        self.assertIsNone(self.co.snapshot(sid)["question"])
        self.assertEqual(self.co.snapshot(sid)["speech_pending"], 0)

    async def test_spoken_start_keeps_echo_gate_closed_until_actual_audio_end(self):
        sid = await self.start()
        q = await self.question(sid)
        await self.co.control(sid, {"action": "speaking", "channel": "agent", "speaking": True})
        await self.co.control(sid, {"action": "spoken", "question_id": q["id"]})
        self.assertTrue(self.co.snapshot(sid)["speaking"]["agent"])
        stale_frame = self.media(sid, 2)
        await self.co.observe(sid, {"key": "frame-started-before-tts", "sequence": 1,
            "scene_epoch": 0, "start_seconds": 1, "end_seconds": 2,
            "evidence_ids": [stale_frame["id"]], "agent_speaking": False})
        self.assertTrue(self.co.snapshot(sid)["speaking"]["agent"])
        mixed_audio = await self.co.transcript(sid, self.answer(q, key="while-tts", text="An unrelated ASR result during output"))
        self.assertEqual(mixed_audio["ignored"], "agent_echo")
        await self.co.control(sid, {"action": "speaking", "channel": "agent", "speaking": False})
        late_frame = self.media(sid, 3)
        result = await self.co.observe(sid, {"key": "frame-started-during-tts", "sequence": 2,
            "scene_epoch": 0, "start_seconds": 2, "end_seconds": 3,
            "evidence_ids": [late_frame["id"]], "agent_speaking": True})
        self.assertFalse(self.co.snapshot(sid)["speaking"]["agent"])
        self.assertTrue(self.store.get(result["observation_id"], "live_observation")["agent_speaking_at_capture"])
        await self.co.control(sid, {"action": "speaking", "channel": "agent", "speaking": True})
        await self.co.control(sid, {"action": "barge_in"})
        self.assertFalse(self.co.snapshot(sid)["speaking"]["agent"])
        actual = await self.co.transcript(sid, self.answer(q, key="actual-barge-in"))
        self.assertTrue(actual["answered"])

    async def test_interrupted_session_restores_unique_event_cursor(self):
        sid = await self.start()
        q = await self.question(sid)
        await self.speak(sid, q)
        last = self.co.snapshot(sid)["last_event_id"]
        await self.co.close()
        self.co = self.make(self.provider)
        self.assertEqual(self.co.snapshot(sid)["status"], "interrupted")
        await self.co.control(sid, {"action": "resume"})
        events = self.co.snapshot(sid, last)["events"]
        self.assertTrue(events)
        self.assertGreater(events[0]["id"], last)
        self.assertEqual(len({e["id"] for e in self.co.snapshot(sid)["events"]}), len(self.co.snapshot(sid)["events"]))

    async def test_segmentation_result_from_old_seek_is_not_reused(self):
        gate, began = asyncio.Event(), asyncio.Event()
        async def segment(*args):
            began.set()
            await gate.wait()
            return {"frames_with_detections": 1, "model": "old-mask"}
        await self.co.close()
        self.provider = Provider([perception(target="spoon"), perception()])
        self.co = self.make(self.provider, segment_observation=segment)
        sid = await self.start()
        await self.observe(sid)
        await began.wait()
        await self.co.control(sid, {"action": "seek", "source_seconds": 0, "scene_epoch": 1})
        gate.set()
        await self.until(lambda: any(e["type"] == "segmentation_stale" for e in self.co.snapshot(sid)["events"]))
        await self.observe(sid, 0, 1, 1)
        await self.until(lambda: len(self.provider.calls) == 2)
        self.assertEqual(self.provider.calls[1][0]["sam_results"], [])
