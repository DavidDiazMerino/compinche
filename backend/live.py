"""Causal capture-session coordinator.

Capture, speech and perception have independent lanes.  Provider calls never block
input acknowledgement; only the newest *queued* visual window is analysed.  A
completed older window can still yield an explicitly anchored retrospective
question, unless speech, a seek, a pause, or age made that context obsolete.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
import difflib
import hashlib
import inspect
import json
import numpy as np
import math
from pathlib import Path
import re
import time
from typing import Any, Callable
from uuid import NAMESPACE_URL, uuid5

from .agent import MEASURE, quantity_key
from .store import now, uid

TARGETS = {"saucepan", "spoon", "food", "hand", "dough", "croquette"}
ROLES = {"cook", "wearer", "observer"}
TERMINAL = {"stopped", "interrupted"}


class LiveError(ValueError):
    def __init__(self, message: str, code: str = "invalid_live_input", status: int = 400):
        super().__init__(message)
        self.code, self.status = code, status


def _seconds(value, name="time"):
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise LiveError(f"{name} must be a finite number.") from None
    if not math.isfinite(number) or not 0 <= number <= 86400:
        raise LiveError(f"{name} must be between zero and 86400 seconds.")
    return number


def _text(value, limit=5000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise LiveError("A nonempty, bounded text value is required.")
    return value.strip()


def _normal(text):
    return re.sub(r"[^\w]+", " ", text.casefold()).strip()


def _echo(text, question):
    a, b = _normal(text), _normal(question)
    return a == b or (min(len(a), len(b)) > 15 and difflib.SequenceMatcher(None, a, b).ratio() >= .9)


def _decline(text):
    return bool(re.match(r"^(?:no (?:lo )?s[eé]|no me acuerdo|no estoy segur[oa]|no quiero responder|no sabr[ií]a|I don.t know|I don.t want to answer)(?:[,\s.!]|$)", text.strip(), re.I))


def _postpone(text):
    return bool(re.match(r"^(?:ahora no(?:[,\s.!]|$)|ahora mismo no(?:[,\s.!]|$)|no ahora(?:[,\s.!]|$)|"
                         r"m[aá]s tarde[.!]?$|luego[.!]?$|espera[.!]?$|not now(?:[,\s.!]|$)|later[.!]?$|wait[.!]?$)", text.strip(), re.I))


@dataclass
class _Run:
    sid: str
    config: dict
    status: str = "running"
    cursor: float = 0
    epoch: int = 0
    generation: int = 0
    notes_revision: int = 0
    sequence: int = -1
    pending: dict | None = None
    candidate: dict | None = None
    active: dict | None = None
    signal: asyncio.Event = field(default_factory=asyncio.Event)
    worker: asyncio.Task | None = None
    segment_task: asyncio.Task | None = None
    human_speaking: bool = False
    source_speaking: bool = False
    agent_speaking: bool = False
    last_asked: float = float("-inf")
    last_segment: float = float("-inf")
    metrics: dict = field(default_factory=lambda: {
        "observations_received": 0, "observations_analysed": 0, "observations_superseded": 0,
        "stale_results": 0, "provider_failures": 0, "questions_ready": 0, "questions_spoken": 0,
        "utterances_received": 0, "echoes_ignored": 0, "answers_received": 0,
    })
    tools: dict = field(default_factory=lambda: {"vision": {"status": "idle"}, "segmentation": {"status": "idle"}})
    segmentation: list[dict] = field(default_factory=list)
    learning_tasks: set = field(default_factory=set)
    event_seq: int = 0
    question_ready_mono: float | None = None
    question_timing_restored: bool = False
    processing_speech: set = field(default_factory=set)
    measure_task: asyncio.Task | None = None
    last_measure: float = float("-inf")
    best_measurement: dict | None = None
    measurements: list = field(default_factory=list)
    pending_gesture: dict | None = None
    flame: list = field(default_factory=list)
    heat_events: list = field(default_factory=list)
    gesture_task: asyncio.Task | None = None


class LiveCoordinator:
    def __init__(self, store, provider, sam=None, *, segment_observation=None,
                 max_result_age=45.0, max_source_lag=45.0, cooldown=12.0,
                 segmentation_cooldown=30.0, question_ttl=75.0, clock=time.monotonic, wall_clock=time.time,
                 max_active=2, measure=None, measure_interval=15.0, gesture=None, gesture_seconds=6.0):
        self.store, self.provider, self.sam = store, provider, sam
        self.measure, self.measure_interval, self.measure_samples = measure, measure_interval, 3
        self.gesture, self.gesture_seconds = gesture, gesture_seconds
        self.segment_observation = segment_observation
        self.max_result_age, self.max_source_lag = max_result_age, max_source_lag
        self.cooldown, self.segmentation_cooldown, self.question_ttl = cooldown, segmentation_cooldown, question_ttl
        self.clock, self.wall_clock, self.max_active = clock, wall_clock, max_active
        self.runs: dict[str, _Run] = {}
        self._closed = False
        # A process restart cannot leave a capture advertised as running.
        for session in store.list_sessions():
            live = session.get("live")
            if live and live.get("status") in {"running", "paused"}:
                store.update_session(session["id"], live={**live, "status": "interrupted"})

    async def create(self, config: dict):
        if self._closed:
            raise LiveError("The capture coordinator is stopped.", "live_closed", 503)
        if sum(run.status == "running" for run in self.runs.values()) >= self.max_active:
            raise LiveError("Stop another live session before opening one more.", "live_busy", 409)
        config = dict(config)
        config.setdefault("input_mode", config.get("source_kind", config.get("input", "replay")))
        config.setdefault("cook_name", config.get("teacher", "Persona que cocina"))
        config.setdefault("wearer_name", config.get("speaker_name", "Persona que graba"))
        mode = config.get("mode", "record")
        if mode not in {"record", "learn"}:
            raise LiveError("Choose record or learn mode.")
        lesson = config.get("lesson_session_id")
        if mode == "learn":
            source = self.store.get_session(lesson) if lesson else None
            if not source or source.get("mode") != "record":
                raise LiveError("Choose an existing teaching session.", "lesson_not_found", 404)
        elif config.get("source_session_id"):
            raise LiveError("A live teaching session cannot inherit analysed source context.")
        live = {"status": "running", "scene_epoch": 0, "cursor_seconds": 0, "config": config}
        session = self.store.create_session({
            "title": config.get("title", "Aprender mientras cocinamos")[:140],
            "teacher": config.get("cook_name", config.get("teacher", "Persona que cocina"))[:120],
            "mode": mode, "source_session_id": lesson if mode == "learn" else None, "live": live,
        })
        run = _Run(session["id"], config)
        if config.get("segmentation") is False:
            run.tools["segmentation"] = {"status": "disabled"}
        self.runs[run.sid] = run
        self._event(run, "session_started", {"input_mode": config.get("input_mode", config.get("input", "replay")),
                                              "mode": mode, "source_context_inherited": mode == "learn"})
        run.worker = asyncio.create_task(self._visual_loop(run), name=f"live-vision-{run.sid}")
        return self.snapshot(run.sid)

    def _get(self, sid):
        run = self.runs.get(sid)
        if run:
            return run
        session = self.store.get_session(sid)
        if not session or not session.get("live"):
            raise LiveError("This live session does not exist.", "live_not_found", 404)
        saved = session["live"]
        run = _Run(sid, saved.get("config", {}), status=saved.get("status", "interrupted"),
                   cursor=saved.get("cursor_seconds", 0), epoch=saved.get("scene_epoch", 0))
        run.metrics.update(saved.get("metrics", {}))
        run.event_seq = max(saved.get("event_seq", 0), max((e.get("seq", 0) for e in self.store.list(sid, "live_event")), default=0))
        question = session.get("pending_question")
        if question and question.get("status") in {"pending", "spoken"}:
            run.active = question
            run.question_ready_mono = self.clock() - max(0, self.wall_clock() - (question.get("ready_wall") or self.wall_clock()))
            run.question_timing_restored = True
        self.runs[sid] = run
        return run

    def _save(self, run):
        self.store.update_session(run.sid, pending_question=run.active,
            live={"status": run.status, "scene_epoch": run.epoch, "cursor_seconds": run.cursor,
                  "config": run.config, "metrics": run.metrics, "event_seq": run.event_seq})

    def _event(self, run, kind, data=None):
        if not run.event_seq:
            run.event_seq = max((item.get("seq", 0) for item in self.store.list(run.sid, "live_event")), default=0)
        run.event_seq += 1
        seq = run.event_seq
        item = self.store.put(run.sid, "live_event", {"type": kind, "seq": seq,
            "event_id": seq, "scene_epoch": run.epoch, "source_seconds": run.cursor,
            "wall_time": self.wall_clock(), "data": data or {}})
        return {**item, "storage_id": item["id"], "id": seq}

    def _receipt_id(self, sid, lane, key):
        return str(uuid5(NAMESPACE_URL, f"compinche/live/{sid}/{lane}/{key}"))

    def receipt(self, sid, lane, key):
        record = self.store.get(self._receipt_id(sid, lane, key), "live_receipt")
        return record.get("result") if record else None

    def _cached(self, run, lane, key, payload):
        key = _text(key, 160)
        identity = self._receipt_id(run.sid, lane, key)
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        previous = self.store.get(identity, "live_receipt")
        if previous:
            if previous["fingerprint"] != digest:
                raise LiveError("This input key already belongs to another payload.", "live_key_conflict", 409)
            return {**previous["result"], "cached": True}, (identity, digest)
        return None, (identity, digest)

    def _remember_receipt(self, run, lane, key, receipt, result):
        self.store.put(run.sid, "live_receipt", {"id": receipt[0], "fingerprint": receipt[1],
            "lane": lane, "key": key, "result": result})
        return result

    def _owned_media(self, run, ids, cutoff):
        if not isinstance(ids, list) or not 1 <= len(ids) <= 8 or len(ids) != len(set(ids)):
            raise LiveError("Provide one to eight different captured media IDs.")
        media = []
        for eid in ids:
            item = self.store.get(eid, "evidence")
            if not item or item["session_id"] != run.sid:
                raise LiveError("Visual input must belong to this capture session.", "live_foreign_evidence", 404)
            epoch = item.get("scene_epoch", run.epoch)
            if epoch != run.epoch:
                raise LiveError("This evidence belongs to an earlier capture epoch.", "live_stale_epoch", 409)
            start = _seconds(item.get("timestamp_seconds", 0), "media timestamp")
            end = _seconds(item.get("source_end_seconds", start + (item.get("duration_seconds") or 0)), "media end")
            if end > cutoff + .05 or start > cutoff + .05:
                raise LiveError("A causal window cannot include future media.", "live_future_media")
            media.append(item)
        return sorted(media, key=lambda item: item.get("timestamp_seconds", 0))

    async def observe(self, sid, payload):
        run = self._get(sid)
        if run.status != "running":
            raise LiveError("Resume capture before sending observations.", "live_not_running", 409)
        payload = dict(payload)
        self._expire_active(run)
        epoch = payload.get("scene_epoch", run.epoch)
        if epoch != run.epoch:
            raise LiveError("This window belongs to an earlier capture epoch.", "live_stale_epoch", 409)
        key = str(epoch) + ":" + _text(payload.get("key"), 140)
        previous, receipt = self._cached(run, "observe", key, payload)
        if previous:
            return previous
        start, end = _seconds(payload.get("start_seconds", 0)), _seconds(payload.get("end_seconds"))
        if start > end or end < run.cursor - .05:
            raise LiveError("Capture windows must be ordered and cannot run backwards.", "live_clock_reversed")
        sequence = payload.get("sequence", run.sequence + 1)
        if type(sequence) is not int or sequence <= run.sequence:
            raise LiveError("Capture sequence must increase.", "live_sequence_conflict", 409)
        media = self._owned_media(run, payload.get("evidence_ids", []), end)
        run.cursor, run.sequence = end, sequence
        run.source_speaking = bool(payload.get("source_speaking", run.source_speaking))
        # Frame metadata can arrive after either audio START or END.  Only the
        # explicit playback controls own the speaking gate; a late frame cannot
        # reopen the microphone mid-question or leave it blocked after output.
        observation = self.store.put(run.sid, "live_observation", {"key": key,
            "sequence": sequence, "scene_epoch": epoch, "start_seconds": start, "end_seconds": end,
            "evidence_ids": [item["id"] for item in media], "status": "queued",
            "received_at": now(), "received_wall": self.wall_clock(), "source_kind": payload.get("source_kind", "replay"),
            "http_received_wall": payload.get("http_received_wall"), "admission_ms": payload.get("admission_ms"),
            "agent_speaking_at_capture": bool(payload.get("agent_speaking", False))})
        if type(payload.get("admission_ms")) in {int, float}:
            run.metrics["last_admission_ms"] = payload["admission_ms"]
        if run.pending:
            old = run.pending["observation"]
            self.store.put(run.sid, "live_observation", {**old, "status": "superseded"})
            run.metrics["observations_superseded"] += 1
        run.pending = {"observation": observation, "media": media, "received_mono": self.clock()}
        run.metrics["observations_received"] += 1
        self._event(run, "observation_received", {"observation_id": observation["id"], "evidence_ids": observation["evidence_ids"],
                                                 "start_seconds": start, "end_seconds": end})
        self._maybe_emit(run)
        self._save(run)
        run.signal.set()
        return self._remember_receipt(run, "observe", key, receipt,
            {"accepted": True, "cached": False, "observation_id": observation["id"], "sequence": sequence,
             "scene_epoch": epoch, "status": "queued"})

    def _context(self, run, observation):
        cutoff = observation["end_seconds"]
        notes = [n for n in self.store.list(run.sid, "note")
                 if n.get("scene_epoch", 0) == run.epoch and n.get("end_seconds", n.get("timestamp_seconds", 0)) <= cutoff + .05]
        questions = [q for q in self.store.list(run.sid, "question") if q.get("scene_epoch", 0) == run.epoch][-20:]
        media = [self.store.get(eid, "evidence") for eid in observation["evidence_ids"]]
        # Only selected current media, own received words, and explicit past lesson rules.
        # In particular do not use Store.context() here: it may expose preanalysed future clips.
        confirmed_lesson = []
        session = self.store.get_session(run.sid)
        if session.get("mode") == "learn" and session.get("source_session_id"):
            confirmed_lesson = self._lesson_cards(session["source_session_id"])
        return {"session_id": run.sid, "mode": session.get("mode", "record"),
            "source_cutoff_seconds": cutoff, "source_start_seconds": observation["start_seconds"],
            "scene_epoch": run.epoch, "speaker_roles": {"cook": run.config.get("cook_name", "Persona que cocina"),
                "wearer": run.config.get("wearer_name", "Persona que graba")},
            "transcripts": [{k: n.get(k) for k in ("id", "text", "speaker", "role", "origin", "automatic", "timestamp_seconds", "end_seconds", "evidence_ids", "provenance")} for n in notes[-20:]],
            "question_history": questions, "pending_question": run.active,
            "evidence": [{k: item.get(k) for k in ("id", "kind", "timestamp_seconds", "source_end_seconds", "scene_epoch")} for item in media],
            "sam_results": [s for s in run.segmentation if s.get("scene_epoch") == run.epoch and s.get("end_seconds", 0) <= cutoff][-3:],
            "confirmed_lesson": confirmed_lesson,
            "already_reminded": sorted(self._reminded(run)),
            "heat_timeline": run.heat_events[-6:],
        }

    def _lesson_cards(self, lesson_sid):
        """Only human-confirmed teaching, trimmed to what a learner prompt needs."""
        cards = []
        for k in [k for k in self.store.list(lesson_sid, "knowledge") if k.get("confirmed")][-30:]:
            d = k.get("decision_card") or {}
            cards.append({"id": k["id"], "teacher_said": (k.get("correction") or {}).get("text") or k.get("quote") or k.get("text"), "speaker": d.get("speaker") or k.get("speaker"),
                "question": d.get("question"), "action": d.get("action"), "observed_cue": d.get("observed_cue"),
                "signal": d.get("signal"), "reported_sensation": d.get("reported_sensation"), "decision": d.get("decision"),
                "demonstration_evidence_ids": d.get("demonstration_evidence_ids", [])[:4],
                "demonstration_interval": [d["source_start_seconds"], d["source_end_seconds"]]
                    if d.get("source_start_seconds") is not None and d.get("source_end_seconds") is not None else None})
        return cards

    def _reminded(self, run):
        # A card already spoken as an answer is not repeated as a reminder.
        return {cid for q in self.store.list(run.sid, "question") if q.get("kind") == "guide"
                for cid in q.get("card_ids", [])}

    async def _invoke(self, method, *args):
        if inspect.iscoroutinefunction(method):
            return await method(*args)
        result = await asyncio.to_thread(method, *args)
        return await result if inspect.isawaitable(result) else result

    def _validate_result(self, result, context):
        if not isinstance(result, dict):
            raise LiveError("The visual tool returned no structured observation.", "live_invalid_model_output")
        observation = result.get("observation", result.get("reply", ""))
        if not isinstance(observation, str) or len(observation) > 2000:
            raise LiveError("The observation is invalid.", "live_invalid_model_output")
        gap = result.get("gap", result.get("question"))
        if gap is not None:
            if not isinstance(gap, dict):
                raise LiveError("The proposed question is invalid.", "live_invalid_model_output")
            question = _text(gap.get("question", gap.get("text")), 350)
            gap = {**gap, "question": question, "reason": _text(gap.get("reason"), 700),
                   "key": _text(gap.get("key", _normal(question)), 160)}
            if gap.get("target") not in TARGETS | {None}:
                raise LiveError("The model requested an unknown vision tool target.", "live_invalid_model_output")
            if gap.get("request_kind", "explain") not in {"explain", "view", "repeat"}:
                raise LiveError("The model requested an unsupported interaction.", "live_invalid_model_output")
        source = " ".join(note["text"] for note in context["transcripts"])
        allowed = {quantity_key(match.group()) for match in MEASURE.finditer(source)}
        texts = [observation] + ([gap.get(k) or "" for k in ("question", "action", "cue", "decision")] if gap else [])
        if any(quantity_key(match.group()) not in allowed for text in texts for match in MEASURE.finditer(text)):
            raise LiveError("Visual predictions cannot invent a physical measurement.", "live_unsupported_measurement")
        targets = result.get("segmentation_targets", [])
        if not isinstance(targets, list) or any(t not in TARGETS for t in targets):
            raise LiveError("The model requested an unsupported segmentation tool.", "live_invalid_model_output")
        reminder = result.get("reminder")
        lesson_ids = {c["id"] for c in context.get("confirmed_lesson", [])}
        if not isinstance(reminder, dict) or reminder.get("card_id") not in lesson_ids or not isinstance(reminder.get("text"), str):
            reminder = None
        if context.get("mode") == "learn":
            gap = None  # The learner is not interviewed; teaching gaps belong to record sessions.
        heat = result.get("heat_change") if result.get("heat_change") in {"on", "up", "down", "off"} else None
        return {"observation": observation, "phase": result.get("phase"), "gap": gap, "reminder": reminder, "heat_change": heat,
                "segmentation_targets": targets[:3]}

    async def _visual_loop(self, run):
        try:
            while run.status != "stopped":
                try:
                    await asyncio.wait_for(run.signal.wait(), timeout=1)
                except asyncio.TimeoutError:
                    self._expire_active(run)
                    self._maybe_emit(run)
                    continue
                run.signal.clear()
                if run.status != "running" or not run.pending:
                    continue
                work, run.pending = run.pending, None
                obs, media = work["observation"], work["media"]
                generation, notes_revision, epoch = run.generation, run.notes_revision, run.epoch
                started, wall_started = self.clock(), self.wall_clock()
                obs = {**obs, "queue_ms": round((started-work["received_mono"])*1000, 1)}
                context = self._context(run, obs)
                run.tools["vision"] = {"status": "running", "observation_id": obs["id"]}
                self.store.put(run.sid, "live_observation", {**obs, "status": "analysing", "model_started_wall": wall_started})
                self._event(run, "vision_started", {"observation_id": obs["id"], "queued_ms": round((started-work["received_mono"])*1000)})
                try:
                    method = getattr(self.provider, "live_observe", None)
                    if method is None:
                        raise LiveError("The live visual provider is unavailable.", "live_provider_unavailable", 503)
                    raw = await self._invoke(method, context, media)
                    result = self._validate_result(raw, context)
                    finished = self.clock()
                    latency = round((finished-started)*1000, 1)
                    stale = run.status != "running" or epoch != run.epoch or generation != run.generation or notes_revision != run.notes_revision
                    stale = stale or finished-work["received_mono"] > self.max_result_age or run.cursor-obs["end_seconds"] > self.max_source_lag
                    run.metrics["last_vision_ms"] = latency
                    if stale:
                        run.metrics["stale_results"] += 1
                        self.store.put(run.sid, "live_observation", {**obs, "status": "stale", "model_started_wall": wall_started,
                            "model_finished_wall": self.wall_clock(), "model_ms": latency})
                        self._event(run, "vision_stale", {"observation_id": obs["id"], "model_ms": latency})
                    else:
                        run.metrics["observations_analysed"] += 1
                        self.store.put(run.sid, "live_observation", {**obs, "status": "completed", "result": result,
                            "model_started_wall": wall_started, "model_finished_wall": self.wall_clock(), "model_ms": latency})
                        self._event(run, "observation", {**result, "observation_id": obs["id"], "evidence_ids": obs["evidence_ids"],
                            "start_seconds": obs["start_seconds"], "end_seconds": obs["end_seconds"], "model_ms": latency})
                        self._accept_gap(run, result["gap"], obs, work["received_mono"])
                        self._track_heat(run, obs, result)
                        self._maybe_measure(run, obs)
                        self._maybe_compare_gesture(run)
                        if result.get("reminder") and result["reminder"]["card_id"] not in self._reminded(run):
                            self._offer_guide(run, result["reminder"]["text"], [result["reminder"]["card_id"]], "reminder",
                                              obs["start_seconds"], obs["end_seconds"], obs["evidence_ids"])
                        targets = result["segmentation_targets"] + ([result["gap"]["target"]] if result["gap"] and result["gap"].get("target") else [])
                        if targets:
                            self._start_segmentation(run, obs, media, targets[0])
                except asyncio.CancelledError:
                    raise
                except Exception as error:
                    run.metrics["provider_failures"] += 1
                    self.store.put(run.sid, "live_observation", {**obs, "status": "failed", "error": "The visual tool did not complete this window."})
                    self._event(run, "tool_error", {"tool": "vision", "code": getattr(error, "code", "live_provider_failed"),
                        "message": "No se pudo analizar esta ventana. La captura continúa."})
                finally:
                    run.tools["vision"] = {"status": "idle"}
                    self._save(run)
        except asyncio.CancelledError:
            pass

    def _accept_gap(self, run, gap, obs, received_mono):
        if not gap or run.active:
            return
        history = self.store.list(run.sid, "question")
        if any(q.get("gap_key") == gap["key"] and (q.get("status") in {"answered", "postponed", "cancelled"}
                    or (q.get("status") == "unresolved" and q.get("request_kind", "explain") == gap.get("request_kind", "explain")))
               for q in history):
            return
        if run.candidate:
            old = run.candidate["question"]
            self.store.put(run.sid, "question", {**old, "status": "expired", "closed_reason": "newer_candidate"})
        question = self.store.put(run.sid, "question", {
            "text": gap["question"], "reason": gap["reason"], "gap_key": gap["key"], "status": "candidate",
            "request_kind": gap.get("request_kind", "explain"), "action": gap.get("action"), "cue": gap.get("cue"),
            "inferred_decision": gap.get("decision"), "evidence_ids": obs["evidence_ids"],
            "source_start_seconds": obs["start_seconds"], "source_end_seconds": obs["end_seconds"],
            "scene_epoch": run.epoch, "ready_wall": None, "spoken_at": None,
            "observation_id": obs["id"],
            "media_anchors": [{"evidence_id": eid, "original_start_seconds": self.store.get(eid, "evidence").get("timestamp_seconds", obs["start_seconds"])}
                              for eid in obs["evidence_ids"]],
        })
        run.candidate = {"question": question, "received_mono": received_mono}
        self._event(run, "gap_candidate", {"question_id": question["id"], "text": question["text"]})
        self._maybe_emit(run)

    def _maybe_emit(self, run):
        candidate = run.candidate
        if not candidate or run.active or run.status != "running":
            return
        question = candidate["question"]
        age = self.clock()-candidate["received_mono"]
        if question["scene_epoch"] != run.epoch or age > self.max_result_age or run.cursor-question["source_end_seconds"] > self.max_source_lag:
            self.store.put(run.sid, "question", {**question, "status": "expired", "closed_reason": "context_expired"})
            run.candidate = None
            self._event(run, "question_expired", {"question_id": question["id"]})
            return
        if run.human_speaking or run.source_speaking or run.agent_speaking or run.processing_speech or self.clock()-run.last_asked < self.cooldown:
            return
        source_lag = max(0, run.cursor-question["source_end_seconds"])
        proposed_text = question["text"]
        retrospective = source_lag > 2.5 or age > 3.5
        question.update(proposed_text=proposed_text, original_question=proposed_text,
            temporal_reference={"kind": "retrospective" if retrospective else "current",
                "source_start_seconds": question["source_start_seconds"],
                "source_end_seconds": question["source_end_seconds"],
                "source_lag_seconds": round(source_lag, 3), "observation_age_seconds": round(age, 3)})
        if retrospective:
            anchored = re.sub(r"\bahora\b", "en ese momento", proposed_text, flags=re.I)
            anchored = re.sub(r"\beste\s+paso\b", "ese paso", anchored, flags=re.I)
            # Keep the retrospective anchor once; Spark may already have written one.
            already = re.match(r"^\s*(hace un momento|antes|hace poco|sobre (el|lo) que)", anchored, re.I)
            lowered = "¿" + anchored[1:2].lower() + anchored[2:] if anchored.startswith("¿") else anchored
            question["text"] = anchored if already else "Hace un momento, " + lowered
        question.update(status="pending", ready_wall=self.wall_clock(), question_ready_at=now(),
                        admission_to_ready_ms=round(age*1000, 1))
        run.active = self.store.put(run.sid, "question", question)
        run.question_ready_mono = self.clock()
        run.question_timing_restored = False
        run.candidate = None
        run.metrics["questions_ready"] += 1
        run.metrics["last_question_ready_ms"] = round(age*1000, 1)
        run.metrics["last_question_source_lag_seconds"] = round(run.cursor-question["source_end_seconds"], 3)
        self._event(run, "question", {"question": run.active, "latency_ms": run.metrics["last_question_ready_ms"]})
        self.store.put(run.sid, "message", {"role": "assistant", "text": "Quiero comprender una decisión de este momento.",
            "question_id": question["id"], "question": run.active, "evidence_ids": question["evidence_ids"],
            "provider": "meta", "model": getattr(self.provider, "model", "unknown"), "created_by": "live_agent"})
        self._save(run)

    def _start_segmentation(self, run, obs, media, target):
        if run.config.get("segmentation") is False or (run.segment_task and not run.segment_task.done()):
            return
        if self.clock()-run.last_segment < self.segmentation_cooldown:
            return
        if not self.segment_observation and not self.sam:
            run.tools["segmentation"] = {"status": "unavailable"}
            return
        run.last_segment = self.clock()
        run.segment_task = asyncio.create_task(self._segment(run, obs, media, target, run.epoch, run.generation), name=f"live-sam-{run.sid}")

    async def _segment(self, run, obs, media, target, epoch, generation):
        if epoch != run.epoch or generation != run.generation or run.status != "running":
            return
        started = self.clock()
        run.tools["segmentation"] = {"status": "running", "target": target, "observation_id": obs["id"]}
        self._event(run, "segmentation_started", run.tools["segmentation"])
        try:
            if self.segment_observation:
                result = await self._invoke(self.segment_observation, media, target, run.sid)
            else:
                video = next((item for item in media if item.get("kind") == "video"), None)
                if not video:
                    raise LiveError("This SAM adapter requires a temporal visual window.", "sam_window_required")
                result = await self.sam.segment(Path(video["path"]), target, uid(), duration_seconds=min(video.get("duration_seconds", 2), 8))
            if epoch != run.epoch or generation != run.generation or run.status != "running":
                run.tools["segmentation"] = {"status": "idle"}
                self._event(run, "segmentation_stale", {"observation_id": obs["id"]})
                return
            actual_start, actual_end = obs["start_seconds"], obs["end_seconds"]
            if isinstance(result, dict):
                actual_start = result.get("original_start_seconds", actual_start)
                actual_end = result.get("original_end_seconds", actual_end)
                if (type(actual_start) not in {int, float} or type(actual_end) not in {int, float}
                        or not math.isfinite(actual_start) or not math.isfinite(actual_end)
                        or not 0 <= actual_start <= actual_end <= obs["end_seconds"] + .05):
                    raise LiveError("The segmentation interval must stay inside the observed source cutoff.")
            summary = {"target": target, "observation_id": obs["id"], "evidence_ids": obs["evidence_ids"],
                "start_seconds": actual_start, "end_seconds": actual_end, "scene_epoch": epoch,
                "result": result, "elapsed_ms": round((self.clock()-started)*1000, 1),
                "interpretation_limits": "Object localisation and motion predictions only; not physical measurement or cooking readiness."}
            run.segmentation = (run.segmentation+[summary])[-4:]
            run.tools["segmentation"] = {"status": "completed", **summary}
            self._event(run, "segmentation", summary)
        except asyncio.CancelledError:
            run.tools["segmentation"] = {"status": "paused" if run.status == "paused" else "idle"}
            raise
        except Exception as error:
            run.tools["segmentation"] = {"status": "unavailable", "target": target, "code": getattr(error, "code", type(error).__name__)}
            self._event(run, "tool_error", {"tool": "segmentation", "message": "La segmentación no está disponible para esta ventana; la observación continúa."})

    async def speech_processing(self, sid, key, pending=True, scene_epoch=None):
        """Keep the speech lane fenced between VAD endpoint and final ASR text."""
        run = self._get(sid)
        epoch = run.epoch if scene_epoch is None else scene_epoch
        identity = (epoch, str(key))
        if pending:
            if epoch != run.epoch or run.status != "running":
                return
            run.processing_speech.add(identity)
        else:
            run.processing_speech.discard(identity)
            if epoch == run.epoch:
                self._maybe_emit(run)
        self._save(run)

    async def tool_error(self, sid, tool, message, *, scene_epoch=None):
        """Publish a safe tool failure without manufacturing a human explanation."""
        run = self._get(sid)
        if scene_epoch is not None and scene_epoch != run.epoch:
            return False
        self._event(run, "tool_error", {"tool": _text(tool, 80), "message": _text(message, 600)})
        self._save(run)
        return True

    async def voice_prepared(self, sid, question_id, preparation_ms):
        run = self._get(sid)
        if not run.active or run.active["id"] != question_id or run.status != "running":
            return False
        run.active = self.store.put(sid, "question", {**run.active,
            "voice_preparation_ms": preparation_ms, "voice_prepared_wall": self.wall_clock()})
        self._event(run, "voice_prepared", {"question_id": question_id, "preparation_ms": preparation_ms})
        self._save(run)
        return True

    async def transcript(self, sid, payload):
        run = self._get(sid)
        payload = dict(payload)
        key = _text(payload.get("key"), 160)
        previous, receipt = self._cached(run, "transcript", key, payload)
        if previous:
            return previous
        def finish(result):
            return self._remember_receipt(run, "transcript", key, receipt, {"cached": False, **result})
        if payload.get("scene_epoch", run.epoch) != run.epoch or run.status != "running":
            return finish({"accepted": False, "ignored": "stale_capture"})
        text = _text(payload.get("text"))
        origin = payload.get("origin", "microphone")
        role = payload.get("role", "observer")
        if origin not in {"microphone", "recorded_source", "typed"} or role not in ROLES:
            raise LiveError("Declare the real speech source and speaker role.")
        speaker = _text(payload.get("speaker", "Persona sin identificar"), 120)
        start, end = _seconds(payload.get("start_seconds", run.cursor)), _seconds(payload.get("end_seconds", run.cursor))
        if start > end:
            raise LiveError("Speech source interval cannot run backwards.")
        self._expire_active(run)
        question = run.active
        if payload.get("agent_speaking") or run.agent_speaking or (question and _echo(text, question["text"])):
            run.metrics["echoes_ignored"] += 1
            self._event(run, "echo_ignored", {"reason": "agent_output_or_matching_question"})
            self._save(run)
            return finish({"accepted": False, "ignored": "agent_echo"})
        audio_ids = payload.get("evidence_ids", [])
        if not isinstance(audio_ids, list) or len(audio_ids) > 4:
            raise LiveError("A speech event supports up to four audio references.")
        for eid in audio_ids:
            audio = self.store.get(eid, "evidence")
            if not audio or audio["session_id"] != sid or audio.get("kind") != "audio":
                raise LiveError("Speech evidence must be audio from this session.", "live_foreign_evidence", 404)
        run.cursor = max(run.cursor, end)
        run.notes_revision += 1
        # A newly heard explanation must be considered before a queued question is spoken.
        self._expire_candidate(run, "new_speech")
        note = self.store.put(sid, "note", {"text": text, "source": "transcript" if origin != "typed" else "user",
            "confirmed": False, "timestamp_seconds": start, "end_seconds": end, "scene_epoch": run.epoch,
            "speaker": speaker, "role": role, "speaker_verification": "user_declared", "origin": origin,
            "automatic": bool(payload.get("automatic", origin != "typed")), "evidence_ids": audio_ids,
            "provenance": {"capture": origin, "model": payload.get("asr_model"), "human_verified": False}})
        run.metrics["utterances_received"] += 1
        self._event(run, "transcript", {"note": note})
        answered, card, ignored = False, None, None
        # A learner is guided, never interviewed: their words are questions for Compinche, not answers.
        wanted = payload.get("answer_to") if run.config.get("mode") != "learn" else None
        if wanted and origin != "recorded_source":
            started_wall = payload.get("started_wall")
            actual_start = (float(started_wall) if type(started_wall) in {int, float} and math.isfinite(started_wall) else None)
            valid_start = origin == "typed" or (question and actual_start is not None and actual_start >= question.get("spoken_wall", float("inf")))
            if not question or wanted != question["id"] or question.get("scene_epoch") != run.epoch:
                ignored = "question_not_current"
            elif question.get("status") != "spoken" or not valid_start:
                ignored = "question_not_spoken_before_utterance"
            elif _postpone(text):
                self._close_question(run, "postponed", "person_requested_later")
                ignored = "postponed"
            elif _decline(text):
                self._close_question(run, "unresolved", "person_did_not_supply_explanation")
                ignored = "unresolved"
            else:
                epoch, check = run.epoch, await self._check_answer(run, question, text)
                if not run.active or run.active["id"] != question["id"] or run.epoch != epoch or run.status != "running":
                    ignored = "question_changed_during_check"
                elif check and check["status"] == "unrelated":
                    # Keep the words as conversation; the gap stays open for a relevant explanation.
                    ignored = "unrelated_to_question"
                    self._event(run, "answer_unrelated", {"question_id": question["id"], "note_id": note["id"],
                                                          "reason": check["reason"]})
                elif check and check["status"] == "declined":
                    self._close_question(run, "unresolved", "person_did_not_supply_explanation")
                    ignored = "unresolved"
                else:
                    question = run.active
            if ignored is None and wanted and question and question.get("status") == "spoken" and origin != "recorded_source":
                question = self.store.put(sid, "question", {**question, "status": "answered", "answered_at": now(),
                    "answer_note_id": note["id"], "answer_speaker": speaker, "answer_role": role})
                run.active = None
                run.generation += 1
                card = self.store.put(sid, "knowledge", {"text": text, "source": "said", "quote": text,
                    "relevance": check or {"status": "unchecked", "reason": "Sin verificación automática de pertinencia."},
                    "confirmed": False, "created_by": "live_conversation", "speaker": speaker, "speaker_role": role,
                    "question_id": question["id"], "evidence_ids": [note["id"], *question["evidence_ids"]][:6],
                    "uncertainty": "Explicación atribuida al hablante declarado; transcripción e interpretación pendientes de revisión humana.",
                    "decision_card": {"action": question.get("action"), "observed_cue": question.get("cue"),
                        "explanation": text, "reported_sensation": None, "decision": None,
                        "inferred_decision": question.get("inferred_decision"), "question": question["text"],
                        "speaker": speaker, "role": role, "speaker_verification": "user_declared",
                        "demonstration_evidence_ids": question["evidence_ids"], "answer_evidence_ids": audio_ids,
                        "answer_note_id": note["id"], "source_start_seconds": question["source_start_seconds"],
                        "source_end_seconds": question["source_end_seconds"], "answer_start_seconds": start,
                        "answer_end_seconds": end, "scene_epoch": run.epoch, "confirmed": False}})
                self.store.put(sid, "message", {"role": "user", "text": text, "answer_to": question["id"],
                    "evidence_ids": audio_ids, "speaker": speaker, "speaker_role": role, "origin": origin,
                    "note_id": note["id"], "created_by": "live_human_transcript"})
                run.metrics["answers_received"] += 1
                answered = True
                self._event(run, "decision", {"card": card, "question_id": question["id"]})
                if hasattr(self.provider, "live_learn"):
                    task = asyncio.create_task(self._consolidate(run, card, question), name=f"live-learn-{run.sid}")
                    run.learning_tasks.add(task)
                    task.add_done_callback(run.learning_tasks.discard)
        elif wanted:
            ignored = "recorded_source_is_not_a_live_answer"
        if (not wanted and origin != "recorded_source" and run.config.get("mode") == "learn"
                and (not question or question.get("kind") == "guide")):
            task = asyncio.create_task(self._answer_learner(run, note), name=f"live-guide-{run.sid}")
            run.learning_tasks.add(task)
            task.add_done_callback(run.learning_tasks.discard)
        self._save(run)
        return finish({"accepted": True, "note_id": note["id"], "answered": answered,
                       "card": card, "ignored": ignored})

    def _flame_pixels(self, run, obs):
        """Blue gas-flame pixels just below the measured vessel; None without a vessel box or image."""
        vessel = (run.best_measurement or {}).get("vessel_box")
        image = next((self.store.get(e, "evidence") for e in reversed(obs["evidence_ids"])
                      if (self.store.get(e, "evidence") or {}).get("kind") == "image"), None)
        if not vessel or not image:
            return None
        try:
            from PIL import Image
            left, top, right, bottom = vessel
            h = bottom - top
            rgb = np.asarray(Image.open(image["path"]).convert("RGB").crop(
                (int(left), int(bottom - .25 * h), int(right), int(bottom + .45 * h)))).astype(int)
        except Exception:
            return None
        blue = rgb[..., 2] - np.maximum(rgb[..., 0], rgb[..., 1])
        return int(((blue > 12) & (rgb[..., 2] > 25)).sum())

    def _track_heat(self, run, obs, result):
        """Heat timeline: changes Spark sees (hand on the knob, flame) plus a flame meter as supporting measure."""
        px = self._flame_pixels(run, obs)
        if px is not None:
            run.flame.append({"t": obs["end_seconds"], "px": px})
        event = None
        recent = [e for e in run.heat_events if obs["end_seconds"] - e["t"] < 20]
        if result.get("heat_change") and not any(e["kind"] == result["heat_change"] for e in recent):
            event = {"kind": result["heat_change"], "source": "vision"}
        elif len(run.flame) >= 4:
            before = float(np.median([f["px"] for f in run.flame[-4:-2]]))
            after = float(np.median([f["px"] for f in run.flame[-2:]]))
            last = run.heat_events[-1]["t"] if run.heat_events else -1e9
            if obs["end_seconds"] - last > 20 and max(before, after) > 60 and abs(after - before) > .5 * max(before, after):
                event = {"kind": "down" if after < before else "up", "source": "flame_meter"}
        if event:
            event.update(t=obs["end_seconds"], flame_px=px, evidence_ids=obs["evidence_ids"][-1:], phase=result.get("phase"))
            run.heat_events.append(event)
            self.store.put(run.sid, "heat_event", event)
            words = {"on": "Enciende el fuego", "up": "Sube el fuego", "down": "Baja el fuego", "off": "Apaga el fuego"}
            self._event(run, "heat", {**event, "text": words[event["kind"]] + (" · llama medida" if event["source"] == "flame_meter" else "")})
        if px is not None:
            run.tools["heat"] = {"status": "idle", "flame_px": px, "events": len(run.heat_events)}

    def timeline(self, sid):
        """Phases, heat changes, questions and confirmed cards on the source clock (scenes 06 and 09)."""
        run = self._get(sid)
        observations = [o for o in self.store.list(sid, "live_observation") if o.get("status") == "completed"]
        phases = []
        for o in sorted(observations, key=lambda o: o.get("end_seconds", 0)):
            name = ((o.get("result") or {}).get("phase") or "").strip()
            if not name:
                continue
            # Spark words the same phase slightly differently between windows; similar names are one phase.
            if phases and difflib.SequenceMatcher(None, _normal(phases[-1]["name"]), _normal(name)).ratio() >= .7:
                phases[-1]["end"] = o["end_seconds"]
            else:
                phases.append({"name": name, "start": o.get("start_seconds", o["end_seconds"]), "end": o["end_seconds"]})
        merged = []
        for i, phase in enumerate(phases):  # a closed blip shorter than eight seconds belongs to its neighbour
            blip = phase["end"] - phase["start"] < 8 and i < len(phases) - 1
            if merged and (blip or difflib.SequenceMatcher(None, _normal(merged[-1]["name"]), _normal(phase["name"])).ratio() >= .7):
                merged[-1]["end"] = phase["end"]
            else:
                merged.append(phase)
        phases = merged
        questions = [{"t": q.get("source_end_seconds"), "text": q["text"], "status": q.get("status"), "kind": q.get("kind", "question")}
                     for q in self.store.list(sid, "question") if q.get("status") not in {"candidate", "expired"}]
        cards = [{"t": (k.get("decision_card") or {}).get("source_end_seconds"), "text": k.get("quote") or k.get("text"),
                  "confirmed": bool(k.get("confirmed"))} for k in self.store.list(sid, "knowledge") if k.get("decision_card")]
        return {"duration": run.cursor, "phases": phases, "heat": self.store.list(sid, "heat_event"),
                "flame": run.flame[-200:], "questions": questions, "cards": cards}

    def _maybe_measure(self, run, obs):
        """Measure the vessel automatically until a good (top-down enough) view has been measured."""
        if not self.measure or run.status != "running" or (run.measure_task and not run.measure_task.done()):
            return
        good = [m for m in run.measurements if m["view"] == "good"]
        if len(good) >= self.measure_samples or len(run.measurements) >= 3 * self.measure_samples \
                or self.clock() - run.last_measure < self.measure_interval:
            return
        run.last_measure = self.clock()
        image = next((eid for eid in reversed(obs["evidence_ids"]) if (self.store.get(eid, "evidence") or {}).get("kind") == "image"), None)
        if image:
            run.measure_task = asyncio.create_task(self._measure(run, image, obs, run.epoch), name=f"live-measure-{run.sid}")

    async def _measure(self, run, evidence_id, obs, epoch):
        run.tools["measurement"] = {**run.tools.get("measurement", {}), "status": "running"}
        try:
            result = await self.measure.measure(run.sid, evidence_id, "pot")
            if result.get("status") == "not_found":
                result = await self.measure.measure(run.sid, evidence_id, "frying pan")
        except asyncio.CancelledError:
            raise
        except Exception:
            run.tools["measurement"] = {**run.tools.get("measurement", {}), "status": "error"}
            self._event(run, "tool_error", {"tool": "measurement", "message": "No se pudo medir el recipiente en esta ventana."})
            return
        if run.epoch != epoch or run.status == "stopped" or result.get("status") != "measured":
            run.tools["measurement"] = {**run.tools.get("measurement", {}), "status": "idle"}
            return
        run.measurements.append(result)
        # Monocular depth varies frame to frame: aggregate same-view samples with the median, keep the spread.
        view = "good" if any(m["view"] == "good" for m in run.measurements) else "oblique"
        samples = [m for m in run.measurements if m["view"] == view]
        diameters = sorted(m["diameter_cm"] for m in samples)
        median = float(np.median(diameters))
        representative = min(samples, key=lambda m: abs(m["diameter_cm"] - median))
        run.best_measurement = {**representative, "diameter_cm": round(median, 1),
            "range_cm": [round(min(m["range_cm"][0] for m in samples), 1), round(max(m["range_cm"][1] for m in samples), 1)],
            "samples": len(samples), "sample_diameters_cm": diameters, "measurement_ids": [m["id"] for m in samples]}
        summary = {k: run.best_measurement.get(k) for k in ("id", "vessel", "diameter_cm", "range_cm", "raw_diameter_cm",
            "tilt_degrees", "view", "reference", "overlay_evidence_id", "source_evidence_id", "timings", "models",
            "samples", "sample_diameters_cm")}
        self.store.put(run.sid, "vessel_estimate", {k: v for k, v in run.best_measurement.items() if k not in {"id", "created_at", "session_id"}})
        run.tools["measurement"] = {"status": "idle", **summary}
        self._event(run, "measurement", {**summary, "text": f"{'Sartén' if result['vessel'] == 'frying pan' else 'Recipiente'}: "
            f"≈{summary['diameter_cm']:.0f} cm ({summary['range_cm'][0]:.0f}–{summary['range_cm'][1]:.0f}) · "
            f"{summary['samples']} {'vista' if summary['samples'] == 1 else 'vistas'} "
            f"{'cenital' if summary['view'] == 'good' else 'inclinada'}{'' if summary['samples'] == 1 else ('es' if summary['view'] == 'good' else 's')}"})
        noun = "la sartén" if result["vessel"] == "frying pan" else "la olla"
        asked = any(q.get("gap_key") == "measure:view" for q in self.store.list(run.sid, "question"))
        if result["view"] == "oblique" and not asked and not run.active:
            self._offer_prompt(run, "view_request", f"¿Me enseñas un momento {noun} desde arriba?",
                f"La veo muy inclinada ({result['tilt_degrees']:.0f}°); desde arriba puedo medir mejor su diámetro.",
                "measure:view", obs, [result.get("overlay_evidence_id") or evidence_id])
        elif run.config.get("mode") == "learn" and not any(q.get("gap_key") == "measure:compare" for q in self.store.list(run.sid, "question")):
            self._compare_vessels(run, obs)
        self._save(run)

    def _compare_vessels(self, run, obs):
        """Scene 10: same-scale comparison against the teacher's measured vessel (geometry, not a cooking guarantee)."""
        session = self.store.get_session(run.sid)
        lesson = session.get("source_session_id") or ""
        theirs = self.store.list(lesson, "vessel_estimate") or [m for m in self.store.list(lesson, "measurement") if m.get("status") == "measured"]
        mine = run.best_measurement
        if not theirs or not mine or run.active:
            return
        theirs = sorted(theirs, key=lambda m: (m.get("view") == "good", m.get("created_at", "")))[-1]
        ratio = (mine["diameter_cm"] / theirs["diameter_cm"]) ** 2
        percent = round(ratio * 100)
        teacher = (self.store.get_session(session["source_session_id"]) or {}).get("teacher") or "quien enseñó"
        if ratio < .85:
            advice = f"Para la misma altura de masa, usa en torno al {percent}% de cada ingrediente, o haz la receta en dos tandas."
        elif ratio > 1.15:
            advice = f"Con la misma cantidad la capa quedará más fina; vigila antes el punto."
        else:
            advice = "Puedes usar la misma cantidad."
        text = (f"Tu recipiente mide unos {mine['diameter_cm']:.0f} cm y el de {teacher} unos {theirs['diameter_cm']:.0f}: "
                f"tiene el {percent}% de su superficie. {advice}")
        self.store.put(run.sid, "comparison", {"mine": mine["id"], "theirs": theirs["id"], "area_ratio": round(ratio, 3),
            "advice": advice, "method": "Área de círculos con los diámetros medidos; misma altura de masa supuesta."})
        self._offer_prompt(run, "guide", text, "Comparación geométrica a la misma escala; no valida la receta.",
                           "measure:compare", obs, [e for e in (theirs.get("overlay_evidence_id"), mine.get("overlay_evidence_id")) if e])

    def _video_window(self, sid, start, end):
        """The session video covering [start, end] in source seconds: replay original or a phone motion clip."""
        session = self.store.get_session(sid) or {}
        config = (session.get("live") or {}).get("config", {})
        if config.get("source_kind") == "replay" and config.get("source_evidence_id"):
            video = self.store.get(config["source_evidence_id"], "evidence")
            if video:
                return {"video": video["path"], "start": max(0, start), "end": end}
        clips = [e for e in self.store.list(sid, "evidence") if e.get("kind") == "video" and e.get("source_end_seconds")]
        overlap = lambda e: min(end, e["source_end_seconds"]) - max(start, float(e.get("timestamp_seconds", 0)))
        best = max(clips, key=overlap, default=None)
        if best and overlap(best) > 2:
            offset = float(best.get("timestamp_seconds", 0))
            return {"video": best["path"], "start": max(0, start - offset), "end": min(end, best["source_end_seconds"]) - offset}
        return None

    def _maybe_compare_gesture(self, run):
        pending = run.pending_gesture
        if not pending or not self.gesture or (run.gesture_task and not run.gesture_task.done()):
            return
        if run.cursor < pending["after"] + self.gesture_seconds:
            return
        run.pending_gesture = None
        run.gesture_task = asyncio.create_task(self._compare_gesture(run, pending, run.epoch), name=f"live-gesture-{run.sid}")

    async def _compare_gesture(self, run, pending, epoch):
        card = pending["card"]
        session = self.store.get_session(run.sid)
        lesson = session.get("source_session_id")
        a, b = card["demonstration_interval"]
        teacher = self._video_window(lesson, a - 2, b + 4)
        learner = self._video_window(run.sid, pending["after"], pending["after"] + self.gesture_seconds)
        if not teacher or not learner:
            self._event(run, "tool_error", {"tool": "gesture", "message": "No hay vídeo suficiente para comparar el gesto."})
            return
        run.tools["gesture"] = {"status": "running"}
        teacher_name = (self.store.get_session(lesson) or {}).get("teacher") or "Quien enseñó"
        try:
            result = await self.gesture.compare(run.sid, teacher, learner, card.get("teacher_said"),
                                                f"{teacher_name} · demostración", "Tu intento")
        except asyncio.CancelledError:
            raise
        except Exception:
            run.tools["gesture"] = {"status": "error"}
            self._event(run, "tool_error", {"tool": "gesture", "message": "No se pudo comparar el gesto esta vez."})
            return
        summary = {k: result.get(k) for k in ("id", "same_gesture", "difference", "suggestion", "phase",
                                              "split_video_evidence_id", "still_evidence_id", "models")}
        run.tools["gesture"] = {"status": "idle", **summary, "card_id": card["id"]}
        self._event(run, "gesture_comparison", {**summary, "text": result["difference"]})
        if run.active and run.active.get("kind") in {"guide", "view_request"}:
            self._close_question(run, "superseded", "gesture_comparison_ready")
        if run.epoch == epoch and run.status == "running" and not run.active:
            obs = {"start_seconds": pending["after"], "end_seconds": run.cursor}
            self._offer_prompt(run, "guide", f"Mira esta diferencia respecto a su demostración: {result['difference']} {result['suggestion']}",
                               "Comparación alineada de su demostración y tu intento.", f"gesture:{result['id']}", obs,
                               [result["still_evidence_id"], result["split_video_evidence_id"]])
        self._save(run)

    def _offer_prompt(self, run, kind, text, reason, key, obs, evidence_ids):
        prompt = self.store.put(run.sid, "question", {"kind": kind, "trigger": key, "text": text, "reason": reason,
            "gap_key": key, "status": "pending", "request_kind": "view" if kind == "view_request" else "guide",
            "card_ids": [], "evidence_ids": evidence_ids, "demonstration_evidence_ids": evidence_ids,
            "source_start_seconds": obs["start_seconds"], "source_end_seconds": obs["end_seconds"], "scene_epoch": run.epoch,
            "ready_wall": self.wall_clock(), "question_ready_at": now(), "spoken_at": None})
        run.active, run.candidate = prompt, None
        run.question_ready_mono, run.question_timing_restored = self.clock(), False
        self._event(run, "guide" if kind == "guide" else "question", {"question": prompt})
        return prompt

    def _offer_guide(self, run, text, card_ids, trigger, start, end, evidence_ids=(), reply_to=None):
        """Speak confirmed teaching back to a learner through the same voice turn as a question."""
        if run.active or run.status != "running":
            return None
        session = self.store.get_session(run.sid)
        lesson = {c["id"]: c for c in self._lesson_cards(session.get("source_session_id"))} if session.get("source_session_id") else {}
        cards = [lesson[c] for c in card_ids if c in lesson]
        guide = self.store.put(run.sid, "question", {"kind": "guide", "trigger": trigger, "text": _text(text, 400),
            "reason": "Lo enseñó en la sesión original." if cards else "No forma parte de lo que se enseñó.",
            "gap_key": f"guide:{trigger}:{','.join(card_ids) or reply_to}", "status": "pending", "request_kind": "guide",
            "card_ids": [c["id"] for c in cards], "lesson_cards": cards, "reply_to_note_id": reply_to,
            "evidence_ids": list(evidence_ids)[:4], "demonstration_evidence_ids": [e for c in cards for e in c["demonstration_evidence_ids"]][:6],
            "source_start_seconds": start, "source_end_seconds": end, "scene_epoch": run.epoch,
            "ready_wall": self.wall_clock(), "question_ready_at": now(), "spoken_at": None})
        run.active, run.candidate = guide, None
        run.question_ready_mono, run.question_timing_restored = self.clock(), False
        self._event(run, "guide", {"question": guide})
        self.store.put(run.sid, "message", {"role": "assistant", "text": guide["text"], "question_id": guide["id"],
            "evidence_ids": guide["demonstration_evidence_ids"], "created_by": "live_guide",
            "model": getattr(self.provider, "model", "unknown")})
        self._save(run)
        return guide

    async def _answer_learner(self, run, note):
        if not hasattr(self.provider, "live_guide"):
            return
        session = self.store.get_session(run.sid)
        cards = self._lesson_cards(session["source_session_id"])
        epoch = run.epoch
        try:
            reply = await self._invoke(self.provider.live_guide, {"learner_said": note["text"], "lesson_cards": cards,
                "teacher": run.config.get("teacher"), "recent_observations": [o.get("result", {}).get("observation")
                    for o in self.store.list(run.sid, "live_observation") if o.get("status") == "completed"][-3:]})
        except asyncio.CancelledError:
            raise
        except Exception:
            self._event(run, "tool_error", {"tool": "guide", "message": "No se pudo consultar la lección; la pregunta queda guardada."})
            return
        if not reply.get("addressed") or run.epoch != epoch or run.status != "running":
            return
        allowed = {quantity_key(m.group()) for c in cards for m in MEASURE.finditer(json.dumps(c, ensure_ascii=False))}
        if any(quantity_key(m.group()) not in allowed for m in MEASURE.finditer(reply.get("answer", ""))):
            reply = {**reply, "known": False, "card_ids": [],
                     "answer": "Esa cantidad no la dio en la sesión; prefiero no inventarla."}
        if run.active and run.active.get("kind") in {"guide", "view_request"}:
            # The learner's own question comes before any automatic reminder or request.
            self._close_question(run, "superseded", "learner_asked")
        guide = self._offer_guide(run, reply["answer"], reply.get("card_ids", []) if reply.get("known") else [], "answer",
                                  note["timestamp_seconds"], note["end_seconds"], reply_to=note["id"])
        demonstrated = [c for c in (guide or {}).get("lesson_cards", []) if c.get("demonstration_interval")]
        if self.gesture and reply.get("known") and reply.get("invite_repeat") and demonstrated:
            # Watch the learner's next seconds and compare them with the teacher's demonstration.
            run.pending_gesture = {"card": demonstrated[0], "after": max(run.cursor, note["end_seconds"]), "note_id": note["id"]}

    async def _check_answer(self, run, question, text):
        """Classify relevance before a reply may close a gap; failures fall back to an unconfirmed card."""
        if not hasattr(self.provider, "live_answer_check"):
            return None
        try:
            return await self._invoke(self.provider.live_answer_check, {"question": question["text"],
                "action": question.get("action"), "observed_cue": question.get("cue"), "human_reply": text})
        except asyncio.CancelledError:
            raise
        except Exception:
            self._event(run, "tool_error", {"tool": "answer_check", "message": "No se pudo comprobar si la respuesta era pertinente; queda pendiente de revisión."})
            return None

    async def _consolidate(self, run, card, question):
        """Derive a tentative decision separately from the literal spoken evidence."""
        if run.status == "stopped":
            return
        quote, card_id = card["quote"], card["id"]
        context = {"question": question["text"], "action": question.get("action"),
            "observed_cue": question.get("cue"), "human_explanation": quote,
            "speaker": card["speaker"], "speaker_role": card["speaker_role"],
            "source_interval": [question["source_start_seconds"], question["source_end_seconds"]],
            "evidence_ids": card["evidence_ids"], "confirmed": False}
        try:
            raw = await self._invoke(self.provider.live_learn, context)
            from .live_models import LiveLearning
            derived = LiveLearning.model_validate(raw).model_dump()
            allowed = {quantity_key(m.group()) for m in MEASURE.finditer(quote)}
            if any(quantity_key(m.group()) not in allowed for value in derived.values() if value
                   for m in MEASURE.finditer(value)):
                raise LiveError("A derived decision cannot invent a measurement.")
            current = self.store.get(card_id, "knowledge")
            # Never replace a person's review, a changed quote, or a different card.
            if not current or current.get("confirmed") or current.get("quote") != quote or current.get("text") != card["text"]:
                return
            current["decision_card"] = {**current["decision_card"],
                "reported_sensation": derived.get("reported_sensation"), "decision": derived.get("decision"),
                "signal": derived.get("signal"), "summary": derived.get("summary"),
                "category": derived.get("category"), "title": derived.get("title"),
                "derived_by": getattr(self.provider, "model", "unknown"), "derivation_confirmed": False,
                "derivation_source_note_id": current["decision_card"]["answer_note_id"]}
            self.store.put(run.sid, "knowledge", current)
            self._event(run, "decision_updated", {"card": current})
        except asyncio.CancelledError:
            raise
        except Exception:
            self._event(run, "tool_error", {"tool": "learning", "message": "La explicación literal está guardada; no se pudo derivar su ficha todavía."})

    def _expire_active(self, run):
        question = run.active
        if not question or run.status != "running":
            return
        age = self.clock() - (run.question_ready_mono if run.question_ready_mono is not None else self.clock())
        if age > self.question_ttl or run.cursor-question["source_end_seconds"] > self.question_ttl:
            self._close_question(run, "expired", "relevance_window_elapsed")
            self._event(run, "speech_cancel", {"reason": "question_expired"})
            self._save(run)

    def _expire_candidate(self, run, reason):
        if run.candidate:
            q = run.candidate["question"]
            self.store.put(run.sid, "question", {**q, "status": "expired", "closed_reason": reason})
            self._event(run, "question_expired", {"question_id": q["id"], "reason": reason})
            run.candidate = None

    def _close_question(self, run, status, reason):
        question = run.active
        if question:
            self.store.put(run.sid, "question", {**question, "status": status, "closed_at": now(), "closed_reason": reason})
            self._event(run, "question_"+status, {"question_id": question["id"]})
            run.active = None
        self._expire_candidate(run, reason)
        run.generation += 1
        run.agent_speaking = False

    async def control(self, sid, payload):
        run = self._get(sid)
        self._expire_active(run)
        action = payload.get("action")
        question_id = payload.get("question_id")
        if action in {"postpone", "cancel", "spoken"} and question_id and (not run.active or run.active["id"] != question_id):
            raise LiveError("This question is no longer current.", "live_question_changed", 409)
        if action == "pause":
            run.status = "paused"
            run.generation += 1
            run.pending = None
            if run.segment_task:
                run.segment_task.cancel()
            self._expire_candidate(run, "capture_paused")
            run.agent_speaking = False
            self._event(run, "speech_cancel", {"reason": "capture_paused"})
        elif action == "resume":
            if run.status == "stopped":
                raise LiveError("Start a new session after stopping.", "live_stopped", 409)
            run.status = "running"
            run.generation += 1
            if not run.worker or run.worker.done():
                run.worker = asyncio.create_task(self._visual_loop(run))
            run.signal.set()
        elif action == "stop":
            run.status = "stopped"
            run.pending = None
            self._close_question(run, "cancelled", "capture_stopped")
            if run.worker:
                run.worker.cancel()
            if run.segment_task:
                run.segment_task.cancel()
            for task in list(run.learning_tasks):
                task.cancel()
            self._event(run, "speech_cancel", {"reason": "capture_stopped"})
        elif action in {"postpone", "cancel"}:
            self._close_question(run, "postponed" if action == "postpone" else "cancelled", "person_requested_"+action)
            self._event(run, "speech_cancel", {"reason": action})
        elif action == "spoken":
            if not run.active:
                raise LiveError("There is no question to acknowledge.", "live_question_changed", 409)
            if run.active["status"] != "spoken":
                ready_to_ack = max(0, (self.clock()-run.question_ready_mono)*1000) if run.question_ready_mono is not None and not run.question_timing_restored else None
                run.active = self.store.put(sid, "question", {**run.active, "status": "spoken",
                    "spoken_at": now(), "spoken_wall": self.wall_clock(), "spoken_epoch": run.epoch,
                    "ready_to_playback_ack_ms": round(ready_to_ack, 1) if ready_to_ack is not None else None,
                    "admission_to_playback_ack_ms": round(run.active["admission_to_ready_ms"]+ready_to_ack, 1)
                        if ready_to_ack is not None and run.active.get("admission_to_ready_ms") is not None else None})
                run.last_asked = self.clock()
                run.metrics["questions_spoken"] += 1
                self._event(run, "question_spoken", {"question_id": run.active["id"]})
            run.agent_speaking = True
            if run.active.get("kind") in {"guide", "view_request"}:
                # Guidance expects no answer: once audible it is delivered and the floor returns to the learner.
                run.active = self.store.put(sid, "question", {**run.active, "status": "delivered", "delivered_at": now()})
                self._event(run, "question_delivered", {"question_id": run.active["id"]})
                run.active = None
                run.generation += 1
        elif action == "speaking":
            channel, speaking = (payload.get("channel") or "human"), bool(payload.get("speaking"))
            if channel == "agent":
                run.agent_speaking = speaking
            elif channel == "source":
                run.source_speaking = speaking
            elif channel == "human":
                run.human_speaking = speaking
                if speaking and run.agent_speaking:
                    run.agent_speaking = False
                    self._event(run, "speech_cancel", {"reason": "human_barge_in"})
            else:
                raise LiveError("Choose human, agent or source speech.")
        elif action == "barge_in":
            run.agent_speaking = False
            run.human_speaking = True
            self._event(run, "speech_cancel", {"reason": "human_barge_in"})
        elif action == "seek":
            cursor = _seconds(payload.get("source_seconds") or 0)
            epoch = payload.get("scene_epoch")
            epoch = run.epoch + 1 if epoch is None else epoch
            if type(epoch) is not int or epoch <= run.epoch:
                raise LiveError("A seek must advance the capture epoch.")
            run.cursor, run.epoch = cursor, epoch
            run.sequence = -1
            run.pending = None
            run.human_speaking = run.source_speaking = run.agent_speaking = False
            run.processing_speech.clear()
            self._close_question(run, "expired", "source_seek")
            run.segmentation = []
            self._event(run, "speech_cancel", {"reason": "source_seek"})
        elif action in {"confirm", "correct"}:
            card_id = payload.get("card_id")
            card = self.store.get(card_id, "knowledge") if card_id else next((k for k in reversed(self.store.list(sid, "knowledge")) if k.get("question_id") == question_id), None)
            if not card or card["session_id"] != sid or not card.get("decision_card"):
                raise LiveError("Choose a decision from this capture.", "live_card_not_found", 404)
            reviewer = _text(payload["reviewer"], 120) if payload.get("reviewer") else "user"
            if action == "correct":
                # The literal quote stays as evidence; the person's corrected wording is what a learner is taught.
                card["correction"] = {"text": _text(payload.get("text"), 3000), "by": reviewer, "at": now()}
                card["decision_card"]["corrected_explanation"] = card["correction"]["text"]
            card.update(confirmed=True, confirmed_by=reviewer, confirmed_at=now(), reviewer_verification="user_declared")
            card["decision_card"]["confirmed"] = True
            self.store.put(sid, "knowledge", card)
            self._event(run, "decision_corrected" if action == "correct" else "decision_confirmed", {"card_id": card["id"], "by": reviewer})
        else:
            raise LiveError("Unknown live control action.")
        self._maybe_emit(run)
        self._save(run)
        self._event(run, "session_state", {"status": run.status, "action": action})
        return self.snapshot(sid)

    def snapshot(self, sid, after=0):
        run = self._get(sid)
        if hasattr(self.store, "live_events"):
            pending = self.store.live_events(sid, after, 201)
        else:
            events = self.store.list(sid, "live_event")
            pending = sorted((e for e in events if e.get("seq", 0) > after), key=lambda e: e["seq"])
        selected = [{**e, "storage_id": e["id"], "id": e["seq"]} for e in pending[:200]]
        cards = [item for item in self.store.list(sid, "knowledge") if item.get("decision_card")]
        return {"id": sid, "session_id": sid, "session": self.store.get_session(sid), "status": run.status,
            "scene_epoch": run.epoch, "source_seconds": run.cursor, "question": run.active, "cards": cards,
            "events": selected, "last_event_id": selected[-1]["id"] if selected else after,
            "more_events": len(pending) > 200, "metrics": dict(run.metrics), "tools": dict(run.tools),
            "speaking": {"human": run.human_speaking, "source": run.source_speaking, "agent": run.agent_speaking},
            "queue_depth": int(run.pending is not None), "speech_pending": len(run.processing_speech)}

    async def close(self):
        self._closed = True
        tasks = []
        for run in self.runs.values():
            if run.status != "stopped":
                run.status = "interrupted"
                run.generation += 1
                self._expire_candidate(run, "server_stopped")
                self._save(run)
            for task in (run.worker, run.segment_task, run.measure_task, run.gesture_task, *list(run.learning_tasks)):
                if task and not task.done():
                    task.cancel()
                    tasks.append(task)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
