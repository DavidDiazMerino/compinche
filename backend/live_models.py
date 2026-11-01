"""Bounded public contracts for continuous sessions (not prerecorded model answers)."""
from typing import Literal
from pydantic import Field
from .models import StrictModel

Source = Literal['replay', 'phone', 'meta_dat_mock', 'meta_dat_device']
Role = Literal['cook', 'wearer', 'observer']

class LiveCreate(StrictModel):
    consent: bool
    source_kind: Source
    teacher: str = Field(default='Persona que cocina', min_length=1, max_length=120)
    speaker_name: str = Field(default='David', min_length=1, max_length=120)
    speaker_role: Role = 'observer'
    source_evidence_id: str | None = None
    mode: Literal['record', 'learn'] = 'record'
    lesson_session_id: str | None = None
    replay_pause_on_question: bool = False
    monitor_original_audio: bool = False

class LiveFrame(StrictModel):
    jpeg_base64: str = Field(min_length=20, max_length=550_000)
    source_seconds: float = Field(ge=0, le=86_400, allow_inf_nan=False)
    sdk_presentation_time_us: int | None = None

class LiveObservation(StrictModel):
    video_evidence_id: str | None = None
    sequence: int = Field(ge=0, le=10_000_000)
    scene_epoch: int = Field(default=0, ge=0, le=100_000)
    frames: list[LiveFrame] = Field(min_length=1, max_length=4)
    source_speaking: bool = False
    agent_speaking: bool = False
    start_seconds: float | None = Field(default=None, ge=0, le=86_400, allow_inf_nan=False)
    end_seconds: float | None = Field(default=None, ge=0, le=86_400, allow_inf_nan=False)

class LiveControl(StrictModel):
    action: Literal['pause', 'resume', 'stop', 'postpone', 'cancel', 'spoken', 'speaking', 'barge_in', 'confirm', 'seek']
    question_id: str | None = None
    card_id: str | None = None
    speaking: bool | None = None
    channel: Literal['human', 'agent', 'source'] | None = None
    source_seconds: float | None = Field(default=None, ge=0, le=86_400, allow_inf_nan=False)
    scene_epoch: int | None = Field(default=None, ge=0, le=100_000)

class LiveTypedAnswer(StrictModel):
    text: str = Field(min_length=1, max_length=3000)
    speaker_name: str = Field(min_length=1, max_length=120)
    speaker_role: Role = 'observer'
    answer_to: str | None = None
    utterance_id: str = Field(min_length=1, max_length=100)
    source_seconds: float = Field(ge=0, le=86_400, allow_inf_nan=False)
    scene_epoch: int = Field(default=0, ge=0)

class LiveGap(StrictModel):
    key: str = Field(min_length=1, max_length=120)
    question: str = Field(min_length=1, max_length=300)
    reason: str = Field(min_length=1, max_length=400)
    action: str | None = Field(max_length=300)
    cue: str | None = Field(max_length=300)
    decision: str | None = Field(max_length=300)
    target: Literal['saucepan', 'spoon', 'food', 'hand', 'dough', 'croquette'] | None
    request_kind: Literal['explain', 'view', 'repeat']

class LiveReminder(StrictModel):
    card_id: str = Field(min_length=1, max_length=100)
    text: str = Field(min_length=1, max_length=300)

class LivePerception(StrictModel):
    observation: str = Field(max_length=600)
    phase: str | None = Field(max_length=100)
    gap: LiveGap | None
    reminder: LiveReminder | None = None
    heat_change: Literal['on', 'up', 'down', 'off'] | None = None
    segmentation_targets: list[Literal['saucepan', 'spoon', 'food', 'hand', 'dough', 'croquette']] = Field(max_length=1)

class LiveLearning(StrictModel):
    summary: str = Field(max_length=600)
    signal: str | None = Field(max_length=500)
    reported_sensation: str | None = Field(max_length=500)
    decision: str | None = Field(max_length=500)

class LiveAnswerCheck(StrictModel):
    status: Literal['explains', 'partial', 'unrelated', 'declined']
    reason: str = Field(max_length=300)

class LiveGuide(StrictModel):
    addressed: bool
    known: bool
    invite_repeat: bool = False
    answer: str = Field(max_length=400)
    card_ids: list[str] = Field(max_length=3)
