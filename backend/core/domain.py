"""I/O-free domain objects shared by the API and Linux workers."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class JobPosting:
    source: str
    external_id: str
    company: str
    title: str
    location: str = ""
    work_mode: str = ""
    description: str = ""
    posted_at: datetime | None = None
    status: str = "open"
    content_hash: str = ""
    requirements: dict[str, float] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ResumeProfile:
    resume_id: int | str
    raw_text: str
    exp_years: float = 0.0
    skills: tuple[str, ...] = ()
    embedding: tuple[float, ...] = ()


@dataclass(frozen=True)
class ScoreConfig:
    version: str = "1"
    alpha: float = 0.60
    decay_lambda: float = 0.35
    max_years_shortfall: float = 3.0
    require_open: bool = True
    honour_work_mode: bool = True


@dataclass(frozen=True)
class ScoreBreakdown:
    semantic_similarity: float
    skill_overlap: float
    experience_factor: float
    final_score: float
    matched_skills: tuple[str, ...] = ()
    missing_skills: tuple[str, ...] = ()
    config_version: str = "1"

