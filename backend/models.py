from typing import Literal
from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class CreateSession(StrictModel):
    title: str = Field(min_length=1, max_length=140)
    teacher: str = Field(default="", max_length=120)
    mode: Literal["record", "learn"] = "record"
    source_session_id: str | None = None


class TurnRequest(StrictModel):
    text: str = Field(min_length=1, max_length=5000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=4)
    answer_to: str | None = None
    idempotency_key: str | None = Field(default=None, max_length=100)


class NoteRequest(StrictModel):
    text: str = Field(min_length=1, max_length=5000)
    timestamp_seconds: float | None = Field(default=None, ge=0, le=86400)
    evidence_ids: list[str] = Field(default_factory=list, max_length=4)


class ConfirmRequest(StrictModel):
    text: str | None = Field(default=None, min_length=1, max_length=2000)


class SegmentRequest(StrictModel):
    evidence_id: str
    target: Literal["saucepan", "spoon", "food", "hand", "dough", "croquette"] = "saucepan"
    start_seconds: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)
    duration_seconds: float = Field(default=8, gt=0, le=8, allow_inf_nan=False)


class ClipRequest(StrictModel):
    evidence_id: str
    start_seconds: float = Field(default=0, ge=0, le=86400, allow_inf_nan=False)
    duration_seconds: float = Field(default=15, gt=0, le=120, allow_inf_nan=False)


class KnowledgeClaim(StrictModel):
    text: str = Field(min_length=1, max_length=1500)
    source: Literal["observed", "said", "inferred"]
    evidence_ids: list[str] = Field(min_length=1, max_length=6)
    # A quoted user statement for said claims. Visual observations use null.
    quote: str | None
    uncertainty: str = Field(min_length=1, max_length=600)


class AgentQuestion(StrictModel):
    text: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=500)
    evidence_ids: list[str] = Field(default_factory=list, max_length=6)


class AgentOutput(StrictModel):
    reply: str = Field(min_length=1, max_length=1600)
    evidence_ids: list[str] = Field(max_length=8)
    question: AgentQuestion | None
    knowledge: list[KnowledgeClaim] = Field(max_length=6)
    segmentation_targets: list[Literal["saucepan", "spoon", "food", "hand", "dough", "croquette"]] = Field(max_length=3)
    uncertainty: list[str] = Field(max_length=6)


class TranscriptSegment(StrictModel):
    start_seconds: float = Field(ge=0, allow_inf_nan=False)
    end_seconds: float = Field(ge=0, allow_inf_nan=False)
    text: str = Field(min_length=1, max_length=2000)
    uncertainty: str = Field(max_length=400)


class TranscriptOutput(StrictModel):
    recipe_title: str | None = Field(max_length=160)
    summary: str = Field(max_length=600)
    segments: list[TranscriptSegment] = Field(max_length=30)
