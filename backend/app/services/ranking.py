"""Rank the stored job market for one profile (shared by the API and the agent)."""

import json
from dataclasses import dataclass
from typing import Any

from app.agents.matcher import build_market_brief
from app.services.context import AgentContext


class NoProfile(LookupError):
    """No resume or LinkedIn profile has been uploaded yet."""


def job_payload(row) -> dict:
    keys = set(row.keys())
    try:
        weights = json.loads(row["weights_json"] or "{}")
    except json.JSONDecodeError:
        weights = {}
    if not isinstance(weights, dict):
        weights = {}
    return {
        "id": row["id"],
        "title": row["title"] or "",
        "company": row["company"] or "",
        "url": row["url"] or "",
        "description": row["description"] or "",
        "req_years": row["req_years"] or 0.0,
        "weights": weights,
        "location": row["location"] or "",
        "country": row["country"] or "",
        "source": row["source"] or "",
        "work_mode": (row["work_mode"] or "") if "work_mode" in keys else "",
        "is_startup": bool(row["is_startup"]),
        "posted_at": row["posted_at"] or "",
    }


def load_profile(ctx: AgentContext, resume_id: int | None = None, fallback_latest: bool = False) -> dict | None:
    """The profile as {id, filename, raw_text, exp_years, skills, extras}.

    An explicit resume_id that does not exist returns None unless fallback_latest is
    set (kept for the older endpoints). No id means the newest profile.
    """
    if ctx.postgres is not None:
        row = ctx.postgres.get_resume(resume_id) if resume_id is not None else ctx.postgres.latest_resume()
        if row is None and fallback_latest:
            row = ctx.postgres.latest_resume()
        if row is None:
            return None
        return {"id": row["id"], "filename": row.get("filename") or "", "raw_text": row.get("raw_text") or "",
                "exp_years": row.get("exp_years") or 0.0, "skills": list(row.get("skills") or []),
                "extras": row.get("extras") or {}}
    db = ctx.get_db()
    try:
        cursor = db.cursor()
        row = (cursor.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()
               if resume_id is not None else None)
        if row is None and (resume_id is None or fallback_latest):
            row = cursor.execute("SELECT * FROM resumes ORDER BY id DESC LIMIT 1").fetchone()
    finally:
        db.close()
    if row is None:
        return None
    return {"id": row["id"], "filename": row["filename"] or "", "raw_text": row["raw_text"] or "",
            "exp_years": row["exp_years"] or 0.0, "skills": json.loads(row["skills_json"] or "[]"), "extras": {}}


@dataclass
class Ranking:
    profile: dict
    semantic_mode: str
    ranked: list[dict]
    matches: list[dict]
    market_brief: dict


def _fetch_rows(ctx: AgentContext, profile: dict) -> list[Any]:
    if ctx.postgres is not None:
        if not profile["raw_text"].strip() or ctx.embedder is None:
            return []
        return ctx.postgres.search_jobs_by_embedding(ctx.embedder.encode([profile["raw_text"]])[0], limit=100)
    db = ctx.get_db()
    try:
        return db.cursor().execute("SELECT * FROM jobs ORDER BY id ASC").fetchall()
    finally:
        db.close()


def rank_market(ctx: AgentContext, profile: dict) -> Ranking:
    """Score every retrieved opening and attach the skill that would move each match."""
    rows = _fetch_rows(ctx, profile)
    if not rows:
        return Ranking(profile, "no_jobs", [], [], {})
    jobs = [job_payload(row) for row in rows]
    ranked, semantic_mode = ctx.matcher.rank_jobs(profile["raw_text"], profile["skills"], profile["exp_years"], jobs)

    matches = []
    for item in ranked:
        job, evaluation = item["job"], item["evaluation"]
        advice = ctx.advisor.generate_advice(profile["skills"], job["weights"], job_title=job["title"],
                                             score_lifts=evaluation["skill_lifts"])
        matches.append({
            "job_id": job["id"],
            "company_details": {
                "company_name": job["company"], "role_title": job["title"], "portal_url": job["url"],
                "required_experience_years": job["req_years"], "location": job["location"],
                "source": job["source"], "is_startup": job["is_startup"], "work_mode": job["work_mode"],
            },
            "candidate_profile": {
                "detected_experience_years": profile["exp_years"],
                "matched_skills": advice["matched_skills"], "missing_skills": advice["missing_skills"],
            },
            "evaluation": evaluation,
            "advisory": advice,
        })
    return Ranking(profile, semantic_mode, ranked, matches, build_market_brief(ranked))
