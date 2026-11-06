"""Pure, database/model-independent scoring stages."""

import math
from collections.abc import Iterable

from core.domain import JobPosting, ResumeProfile, ScoreBreakdown, ScoreConfig


def passes_gates(resume: ResumeProfile, job: JobPosting, config: ScoreConfig) -> bool:
    if config.require_open and job.status != "open":
        return False
    required_years = _required_years(job)
    return required_years - resume.exp_years <= config.max_years_shortfall


def fit_score(
    resume: ResumeProfile,
    job: JobPosting,
    config: ScoreConfig,
    semantic_similarity: float = 0.0,
) -> ScoreBreakdown:
    resume_skills = {skill.lower() for skill in resume.skills}
    required = {skill.lower(): float(weight) for skill, weight in job.requirements.items()}
    matched = tuple(skill for skill in required if skill in resume_skills)
    missing = tuple(skill for skill in required if skill not in resume_skills)
    total_weight = sum(required.values())
    skill_overlap = sum(required[skill] for skill in matched) / total_weight if total_weight else 1.0

    required_years = _required_years(job)
    experience_factor = (
        1.0
        if resume.exp_years >= required_years
        else math.exp(-config.decay_lambda * (required_years - resume.exp_years))
    )
    final_score = experience_factor * (
        config.alpha * semantic_similarity + (1.0 - config.alpha) * skill_overlap
    )
    return ScoreBreakdown(
        semantic_similarity=round(semantic_similarity, 6),
        skill_overlap=round(skill_overlap, 6),
        experience_factor=round(experience_factor, 6),
        final_score=round(final_score, 6),
        matched_skills=matched,
        missing_skills=missing,
        config_version=config.version,
    )


def rank(
    resume: ResumeProfile,
    jobs: Iterable[JobPosting],
    config: ScoreConfig,
    semantic_scores: dict[str, float] | None = None,
) -> list[tuple[JobPosting, ScoreBreakdown]]:
    semantic_scores = semantic_scores or {}
    ranked = []
    for job in jobs:
        if not passes_gates(resume, job, config):
            continue
        breakdown = fit_score(resume, job, config, semantic_scores.get(job.external_id, 0.0))
        ranked.append((job, breakdown))
    ranked.sort(key=lambda item: (-item[1].final_score, item[0].company.lower(), item[0].title.lower()))
    return ranked


def _required_years(job: JobPosting) -> float:
    value = job.metadata.get("req_years", 0.0)
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0

