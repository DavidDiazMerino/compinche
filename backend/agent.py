"""A bounded LangGraph workflow: retrieve → observe/reason → validate → remember.

Source material never grants permissions. SAM is a separate explicit action; the
model can suggest targets but cannot invoke arbitrary side effects.
"""
import json
import os
import re
import sqlite3
import threading
from typing import TypedDict
from pydantic import ValidationError
# Local conversations must not be exported through inherited tracing settings.
os.environ["LANGSMITH_TRACING"] = "false"
os.environ["LANGCHAIN_TRACING_V2"] = "false"
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.sqlite import SqliteSaver
from .models import AgentOutput
from .store import uid, now


class GuardrailFailure(RuntimeError):
    pass


class ConversationConflict(RuntimeError):
    pass


class AgentState(TypedDict, total=False):
    session_id: str
    request: dict
    user_message: dict
    context: dict
    media: list[dict]
    references: dict
    output: dict
    result: dict


MEASURE = re.compile(r"\b(?:\d+(?:[.,/]\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:kilograms?|grams?|kg|g|millilit(?:er|re)s?|ml|lit(?:er|re)s?|centimet(?:er|re)s?|millimet(?:er|re)s?|cm|mm|°\s*[cf]|degrees?(?:\s+celsius|\s+fahrenheit)?|minutes?|seconds?|hours?|tablespoons?|teaspoons?|cups?|ounces?|oz|pounds?|lbs?|inches|pinches)\b", re.I)


def normalise(text):
    return " ".join(text.casefold().replace(",", ".").split())


def validate_output(raw, references, active_media_ids, pending_question, answered):
    try:
        output = AgentOutput.model_validate(raw)
    except ValidationError as exc:
        raise GuardrailFailure("The model response did not match the required evidence schema.") from exc
    allowed = set(references)
    all_ids = output.evidence_ids + [eid for claim in output.knowledge for eid in claim.evidence_ids]
    if output.question:
        all_ids += output.question.evidence_ids
    if any(eid not in allowed for eid in all_ids):
        raise GuardrailFailure("The model cited evidence that does not belong to this conversation.")
    if "?" in output.reply or "¿" in output.reply:
        raise GuardrailFailure("The model placed a question outside the single-question channel.")
    if output.question and (output.question.text.count("?") > 1 or output.question.text.count("¿") > 1):
        raise GuardrailFailure("The model tried to ask multiple questions at once.")
    if pending_question and not answered and output.question:
        # Deterministic suppression preserves the one existing unanswered question.
        output.question = None
    # A measurement must be supported by the text actually cited, not merely
    # somewhere else in the session. Media notes are explicitly user supplied.
    texts_and_ids = [(output.reply, output.evidence_ids)] + [(claim.text, claim.evidence_ids) for claim in output.knowledge]
    if output.question:
        texts_and_ids += [(output.question.text, output.question.evidence_ids), (output.question.reason, output.question.evidence_ids)]
    for text, cited in texts_and_ids:
        source_text = "\n".join(references[eid].get("text", "") for eid in cited if references[eid].get("kind") in ("user", "note", "confirmed", "media"))
        source_quantities = {normalise(quantity) for quantity in MEASURE.findall(source_text)}
        for quantity in MEASURE.findall(text):
            if normalise(quantity) not in source_quantities:
                raise GuardrailFailure("The model introduced a physical quantity absent from the user's evidence.")
    if re.search(r"\b(?:safe to eat|definitely cooked|guaranteed safe|perfectly safe)\b", "\n".join([output.reply] + [claim.text for claim in output.knowledge]), re.I):
        raise GuardrailFailure("Visual evidence cannot certify food safety.")
    if not active_media_ids and re.search(r"\b(?:I see|I can see|I watched|I observed|the video shows)\b", output.reply, re.I):
        raise GuardrailFailure("The model claimed direct observation without supplied media.")
    for claim in output.knowledge:
        refs = [references[eid] for eid in claim.evidence_ids]
        if claim.source == "observed":
            if not any(eid in active_media_ids for eid in claim.evidence_ids):
                raise GuardrailFailure("A visual observation requires media actually analysed in this turn.")
            if MEASURE.search(claim.text):
                raise GuardrailFailure("A visual observation cannot assert a measured physical quantity.")
        if claim.source == "said":
            if not claim.quote or not any(normalise(claim.quote) in normalise(r.get("text", "")) for r in refs if r.get("kind") in ("user", "note", "confirmed", "media")):
                raise GuardrailFailure("A quoted explanation must match the original user's words.")
    return output


class CookingAgent:
    def __init__(self, store, provider):
        self.store = store
        self.provider = provider
        self._locks = {}
        self._locks_lock = threading.Lock()
        self.checkpoint_connection = sqlite3.connect(store.root / "agent-checkpoints.sqlite3", check_same_thread=False)
        self.checkpointer = SqliteSaver(self.checkpoint_connection)
        builder = StateGraph(AgentState)
        builder.add_node("retrieve_context", self.retrieve)
        builder.add_node("meta_observe_and_reason", self.reason)
        builder.add_node("validate_evidence_and_turn", self.validate)
        builder.add_node("remember", self.remember)
        builder.add_edge(START, "retrieve_context")
        builder.add_edge("retrieve_context", "meta_observe_and_reason")
        builder.add_edge("meta_observe_and_reason", "validate_evidence_and_turn")
        builder.add_edge("validate_evidence_and_turn", "remember")
        builder.add_edge("remember", END)
        self.graph = builder.compile(checkpointer=self.checkpointer)

    def invoke(self, session_id, request):
        with self._locks_lock:
            lock = self._locks.setdefault(session_id, threading.Lock())
        if not lock.acquire(blocking=False):
            raise ConversationConflict("Another turn is still processing for this session.")
        try:
            request_json = json.dumps(request, sort_keys=True)
            cached = self.store.cached_turn(session_id, request.get("idempotency_key"), request_json)
            if cached:
                return cached
            session = self.store.get_session(session_id)
            if not session:
                raise KeyError(session_id)
            pending = session.get("pending_question")
            if request.get("answer_to") and (not pending or request["answer_to"] != pending["id"]):
                raise ConversationConflict("That question is no longer the active question. Refresh the session.")
            result = self.graph.invoke(
                {"session_id": session_id, "request": request, "user_message": {"id": uid(), "role": "user", "text": request["text"], "evidence_ids": request.get("evidence_ids", []), "answer_to": request.get("answer_to"), "created_at": now()}},
                config={"configurable": {"thread_id": session_id}, "recursion_limit": 6},
            )["result"]
            return result
        finally:
            lock.release()

    def retrieve(self, state):
        data = self.store.context(state["session_id"])
        references = {}
        messages = data["messages"][-40:] + [state["user_message"]]
        if data["session"].get("source_session_id"):
            # Preserve the original teacher's history separately from the learner.
            original = self.store.conversation(data["session"]["source_session_id"])[-30:]
        else:
            original = []
        for message in original + messages:
            if message["role"] == "user":
                references[message["id"]] = {"kind": "user", "text": message["text"], "session_id": message.get("session_id", state["session_id"])}
        for note in data["notes"][-100:]:
            references[note["id"]] = {"kind": "note", "text": note["text"], "session_id": note["session_id"]}
        for claim in data["knowledge"][-100:]:
            references[claim["id"]] = {"kind": "confirmed" if claim.get("confirmed") else "inferred", "text": claim["text"], "session_id": claim["session_id"]}
        by_id = {e["id"]: e for e in data["evidence"]}
        selected = state["request"].get("evidence_ids", [])
        if len(set(selected)) != len(selected):
            raise GuardrailFailure("Attach each evidence item only once.")
        if any(eid not in by_id for eid in selected):
            raise GuardrailFailure("One of the media references does not belong to this session or its source.")
        media = [by_id[eid] for eid in selected]
        for evidence in data["evidence"]:
            references[evidence["id"]] = {"kind": "media", "text": evidence.get("note", ""), "session_id": evidence["session_id"], "analysed_this_turn": evidence["id"] in selected}
        context = {
            "session": data["session"],
            "original_teacher_messages": original,
            "conversation": messages,
            "conversation_earlier_turns_omitted": max(0, len(data["messages"]) - 40),
            "knowledge": data["knowledge"][-100:],
            "user_notes": data["notes"][-100:],
            "evidence_manifest": [{k: v for k, v in e.items() if k != "path"} for e in data["evidence"][-100:]],
            "available_references": references,
            "pending_question": data["pending_question"],
            "question_history": data["questions"][-40:],
            "answering_pending_question": bool(state["request"].get("answer_to")),
            "sam_results": [{"id": j["id"], "target": j["target"], "status": j["status"], "note": "Segmentation locates object pixels only; it does not measure physical size or determine recipe meaning.", "summary": {k: v for k, v in j.get("result", {}).items() if k in ("model", "frame_count", "frames_with_detections", "source_sha256", "provenance")}} for j in data["jobs"] if j["status"] == "completed"][-8:],
        }
        return {"context": context, "references": references, "media": media}

    def reason(self, state):
        # Track disclosure independently from acceptance of generated claims.
        # If the network fails mid-request, do not falsely report "never sent".
        for evidence in state["media"]:
            self.store.put(evidence["session_id"], "evidence", {**evidence, "meta_transmission_requested_at": now(), "meta_transfer_status": "requested", "uploaded_to_meta": None})
        output = self.provider.generate(state["context"], state["media"])
        for evidence in state["media"]:
            current = self.store.get(evidence["id"], "evidence")
            self.store.put(evidence["session_id"], "evidence", {**current, "meta_transfer_status": "sent", "uploaded_to_meta": True})
        return {"output": output}

    def validate(self, state):
        output = validate_output(state["output"], state["references"], {e["id"] for e in state["media"]}, state["context"]["pending_question"], state["context"]["answering_pending_question"])
        return {"output": output.model_dump()}

    def remember(self, state):
        with self.store.atomic():
            result = self._remember(state)
            self.store.remember_turn(state["session_id"], state["request"].get("idempotency_key"), json.dumps(state["request"], sort_keys=True), result["result"])
            return result

    def _remember(self, state):
        sid, output = state["session_id"], state["output"]
        self.store.put(sid, "message", state["user_message"])
        pending = state["context"]["pending_question"]
        if state["context"]["answering_pending_question"]:
            self.store.put(sid, "question", {**pending, "answered_at": now(), "answer_message_id": state["user_message"]["id"], "status": "answered"})
            pending = None
        asked_question = None
        if output["question"]:
            pending = self.store.put(sid, "question", {**output["question"], "status": "pending"})
            asked_question = pending
        message = self.store.put(sid, "message", {"role": "assistant", "text": output["reply"], "evidence_ids": output["evidence_ids"], "question_id": asked_question["id"] if asked_question else None, "question": asked_question, "provider": "meta", "model": self.provider.model, "uncertainty": output["uncertainty"]})
        knowledge = [self.store.put(sid, "knowledge", {**claim, "confirmed": False, "model": self.provider.model, "created_by": "meta"}) for claim in output["knowledge"]]
        for evidence in state["media"]:
            current = self.store.get(evidence["id"], "evidence")
            self.store.put(evidence["session_id"], "evidence", {**current, "analysed_at": now(), "analysed_by": self.provider.model})
        session = self.store.update_session(sid, pending_question=pending)
        return {"result": {"message": message, "question": pending, "knowledge": knowledge, "segmentation_targets": output["segmentation_targets"], "guardrails": {"passed": True, "one_question": True, "evidence_checked": True, "human_confirmation_required": True}, "session": session}}

    def close(self):
        self.checkpoint_connection.close()
