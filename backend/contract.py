"""Контракт результата оценки. Все части системы работают от этих моделей."""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

Answer = Literal["yes", "no", "scale", "na"]
Symbol = Literal["!!", "!", "?!", "?", "??"]
AppealStatus = Literal["open", "confirmed", "changed"]


class Evidence(BaseModel):
    message_index: int
    timestamp: str = ""          # как в исходных данных: MM:SS или ISO 8601
    quote: str
    verified: bool = False       # цитата дословно найдена в реплике message_index


class CheckedRange(BaseModel):
    from_index: int
    to_index: int


class CriterionResult(BaseModel):
    criterion_id: str
    name: str = ""
    answer: Answer
    value: Optional[int] = None  # выбранная ступень шкалы
    score: float = 0
    max_score: float = 0
    weight: float = 0
    effective_weight: float = 0
    explanation: str = ""
    evidence: list[Evidence] = Field(default_factory=list)
    checked_range: Optional[CheckedRange] = None   # если действие пропущено
    na_reason: str = ""
    confidence: float = 0        # 0..1
    low_confidence: bool = False # цитаты не прошли проверку после повторов
    votes: list[str] = Field(default_factory=list)  # метки независимых оценок, если было голосование


class GrossError(BaseModel):
    flag: bool = False
    criterion_id: Optional[str] = None
    reason: str = ""


class SectionResult(BaseModel):
    id: str
    name: str
    score: Optional[float] = None
    max_score: float = 0


class Annotation(BaseModel):
    message_index: int
    symbol: Symbol
    comment: str = ""
    best_move: str = ""
    criterion_ids: list[str] = Field(default_factory=list)


class SentimentPoint(BaseModel):
    message_index: int
    value: float  # -1..+1


class AppealDecision(BaseModel):
    new_answer: Optional[Answer] = None
    new_value: Optional[int] = None
    new_score: Optional[float] = None
    reason: str = ""


class Appeal(BaseModel):
    appeal_id: str
    criterion_id: str
    comment: str
    status: AppealStatus = "open"
    created_at: str = ""
    original: dict = Field(default_factory=dict)   # CriterionResult + total до решения
    decision: Optional[AppealDecision] = None
    final: dict = Field(default_factory=dict)      # CriterionResult + total после решения


class Metrics(BaseModel):
    """Детерминированные метрики (считаются кодом, без модели)."""
    first_response_sec: Optional[float] = None
    avg_response_sec: Optional[float] = None
    max_pause_sec: Optional[float] = None
    operator_talk_share: Optional[float] = None
    operator_messages: int = 0
    client_messages: int = 0


class EvaluationResult(BaseModel):
    evaluation_id: str = ""
    conversation_id: str
    checklist_id: str
    checklist_version: int
    model: str = ""
    cost_usd: float = 0
    duration_ms: int = 0
    created_at: str = ""
    total_score: Optional[float] = None
    gross_error: GrossError = Field(default_factory=GrossError)
    sections: list[SectionResult] = Field(default_factory=list)
    criteria: list[CriterionResult] = Field(default_factory=list)
    annotations: list[Annotation] = Field(default_factory=list)
    sentiment_curve: list[SentimentPoint] = Field(default_factory=list)
    metrics: Metrics = Field(default_factory=Metrics)
    appeals: list[Appeal] = Field(default_factory=list)
