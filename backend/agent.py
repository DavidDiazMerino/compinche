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
from .segmentation_context import summarise_job


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


MEASURE = re.compile(r"\b(?:\d+(?:[.,/]\d+)?|one|two|three|four|five|six|seven|eight|nine|ten)\s*(?:kilograms?|kilogramos?|gramos?|grams?|kg|g|millilit(?:er|re)s?|mililitros?|ml|lit(?:er|re)s?|litros?|centimet(?:er|re)s?|millimet(?:er|re)s?|cm|mm|[°º]\s*[cf]|grados?|degrees?(?:\s+celsius|\s+fahrenheit)?|minutos?|minutes?|segundos?|seconds?|horas?|hours?|tablespoons?|teaspoons?|cucharadas?|cucharaditas?|tazas?|cups?|ounces?|oz|pounds?|lbs?|inches|pinches)\b", re.I)
DIRECT_OBSERVATION = re.compile(
    r"\b(?:I (?:can )?see|I (?:watched|observed)|the (?:video|image|clip) shows|"
    r"veo|he (?:visto|observado)|(?:el|en el) v[ií]deo (?:muestra|se ve)|"
    r"(?:en la imagen|en el clip) se ve|se (?:ve|observa))\b", re.I)


def normalise(text):
    return " ".join(text.casefold().replace(",", ".").split())


def quantity_key(text):
    return re.sub(r"\s+", "", normalise(text).replace("º", "°"))


def without_supported_quotes(text, cited, references):
    source = "\n".join(references[eid].get("text", "") for eid in cited
                       if references[eid].get("kind") in ("user", "note", "confirmed", "media", "transcript"))
    def replace(match):
        return "" if normalise(match.group(1)) in normalise(source) else match.group(0)
    return re.sub(r'["“«]([^"”»]+)["”»]', replace, text)


def asserts_direct_observation(text):
    return any(not re.search(r"\b(?:no|nunca|tampoco)\s+$", text[:match.start()], re.I)
               for match in DIRECT_OBSERVATION.finditer(text))


def session_window(items, session_id, source_session_id=None, limit=100, required_ids=()):
    """Reserve context for each person, then reuse unused space.

    A source with a long history must not evict a learner's new correction.
    Confirmation changes relevance, never the provenance or confirmation flag.
    """
    owners = [session_id] + ([source_session_id] if source_session_id else [])
    recency = lambda item: (item.get("confirmed_at") or item.get("updated_at") or item.get("created_at", ""), item["id"])
    groups = [sorted((item for item in items if item["session_id"] == owner), key=recency)
              for owner in owners]
    quota = limit // len(groups)
    required = set(required_ids)
    selected = []
    for group in groups:
        pinned = [item for item in group if item["id"] in required]
        ordinary = [item for item in group if item["id"] not in required]
        space = max(0, quota - len(pinned))
        selected.extend(pinned + (ordinary[-space:] if space else []))
    selected_ids = {item["id"] for item in selected}
    remaining = sorted((item for group in groups for item in group if item["id"] not in selected_ids), key=recency)
    spare = limit - len(selected)
    if spare:
        selected.extend(remaining[-spare:])
    return sorted(selected, key=recency)


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
    if output.question and re.search(r"\b(?:y|e|and)\s+(?:cómo|cuándo|cuánt[oa]s?|qué|cuál(?:es)?|how|when|what|which|why)\b", output.question.text, re.I):
        raise GuardrailFailure("The model combined different questions into one sentence.")
    if pending_question and not answered and output.question:
        # Deterministic suppression preserves the one existing unanswered question.
        output.question = None
    # A measurement must be supported by the text actually cited, not merely
    # somewhere else in the session. Media notes are explicitly user supplied.
    texts_and_ids = [(output.reply, output.evidence_ids)] + [(claim.text, claim.evidence_ids) for claim in output.knowledge]
    if output.question:
        texts_and_ids += [(output.question.text, output.question.evidence_ids), (output.question.reason, output.question.evidence_ids)]
    for text, cited in texts_and_ids:
        source_text = "\n".join(references[eid].get("text", "") for eid in cited if references[eid].get("kind") in ("user", "note", "confirmed", "media", "transcript"))
        source_quantities = {quantity_key(quantity) for quantity in MEASURE.findall(source_text)}
        for quantity in MEASURE.findall(text):
            if quantity_key(quantity) not in source_quantities:
                raise GuardrailFailure("The model introduced a physical quantity absent from the user's evidence.")
    if re.search(r"\b(?:safe to eat|definitely cooked|guaranteed safe|perfectly safe)\b", "\n".join([output.reply] + [claim.text for claim in output.knowledge]), re.I):
        raise GuardrailFailure("Visual evidence cannot certify food safety.")
    if not active_media_ids:
        observation_texts = [(output.reply, output.evidence_ids)] + [
            (claim.text, claim.evidence_ids) for claim in output.knowledge
            if not (claim.source == "said" and claim.quote and normalise(claim.text) == normalise(claim.quote))]
        if output.question:
            observation_texts += [(output.question.text, output.question.evidence_ids),
                                  (output.question.reason, output.question.evidence_ids)]
        if any(asserts_direct_observation(without_supported_quotes(text, cited, references))
               for text, cited in observation_texts):
            raise GuardrailFailure("The model claimed direct observation without supplied media.")
    for claim in output.knowledge:
        refs = [references[eid] for eid in claim.evidence_ids]
        if claim.source == "observed":
            if not any(eid in active_media_ids for eid in claim.evidence_ids):
                raise GuardrailFailure("A visual observation requires media actually analysed in this turn.")
            if MEASURE.search(claim.text):
                raise GuardrailFailure("A visual observation cannot assert a measured physical quantity.")
        if claim.source == "said":
            if not claim.quote or not any(normalise(claim.quote) in normalise(r.get("text", "")) for r in refs if r.get("kind") in ("user", "note", "confirmed", "media", "transcript")):
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
        source_id = data["session"].get("source_session_id")
        notes = session_window(data["notes"], state["session_id"], source_id)
        knowledge = session_window(data["knowledge"], state["session_id"], source_id)
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
        for note in notes:
            references[note["id"]] = {"kind": "transcript" if note.get("source") == "transcript" else "note", "text": note["text"], "session_id": note["session_id"], "provenance": note.get("provenance"), "timestamp_seconds": note.get("timestamp_seconds")}
        for claim in knowledge:
            references[claim["id"]] = {"kind": "confirmed" if claim.get("confirmed") else "inferred", "text": claim["text"], "session_id": claim["session_id"]}
        by_id = {e["id"]: e for e in data["evidence"]}
        selected = state["request"].get("evidence_ids", [])
        if len(set(selected)) != len(selected):
            raise GuardrailFailure("Attach each evidence item only once.")
        if any(eid not in by_id for eid in selected):
            raise GuardrailFailure("One of the media references does not belong to this session or its source.")
        media = [by_id[eid] for eid in selected]
        manifest = session_window(data["evidence"], state["session_id"], source_id, required_ids=selected)
        for evidence in data["evidence"]:
            references[evidence["id"]] = {"kind": "media", "text": evidence.get("note", ""), "session_id": evidence["session_id"], "analysed_this_turn": evidence["id"] in selected}
        sam_results = [summarise_job(j, by_id[j["evidence_id"]]) for j in data["jobs"]
                       if j["status"] == "completed" and j.get("evidence_id") in by_id][-8:]
        for result in sam_results:
            references[result["id"]] = {"kind": "segmentation", "text": "Pixel predictions only.", "evidence_id": result["evidence_id"]}
        context = {
            "session": data["session"],
            "original_teacher_messages": original,
            "conversation": messages,
            "conversation_earlier_turns_omitted": max(0, len(data["messages"]) - 40),
            "knowledge": knowledge,
            "user_notes": notes,
            "context_omitted": {
                "knowledge": {owner: sum(item["session_id"] == owner for item in data["knowledge"]) - sum(item["session_id"] == owner for item in knowledge)
                              for owner in self.store.related_ids(state["session_id"])},
                "notes": {owner: sum(item["session_id"] == owner for item in data["notes"]) - sum(item["session_id"] == owner for item in notes)
                          for owner in self.store.related_ids(state["session_id"])},
            },
            "evidence_manifest": [{k: v for k, v in e.items() if k != "path"} for e in manifest],
            "available_references": references,
            "pending_question": data["pending_question"],
            "question_history": data["questions"][-40:],
            "answering_pending_question": bool(state["request"].get("answer_to")),
            "sam_results": sam_results,
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
        if output.question:
            key = lambda text: re.sub(r"[^\w]+", " ", text.casefold()).strip()
            if any(key(q["text"]) == key(output.question.text) for q in state["context"]["question_history"]):
                output.question = None
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
            anchors = [{"evidence_id": e["id"], "original_start_seconds": e.get("timestamp_seconds", 0)}
                       for e in state["media"] if e["id"] in output["question"]["evidence_ids"]]
            pending = self.store.put(sid, "question", {**output["question"], "status": "pending", "media_anchors": anchors})
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
