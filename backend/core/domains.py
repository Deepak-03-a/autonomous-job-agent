"""Predict which role families a candidate fits, deterministically.

Each family is defined by the taxonomy skills that characterise it and the job-title
words people in it use. The score blends two signals the candidate can point to:

  skills  how much of the family's skill weight the candidate holds (saturating,
          so a family with a big skill list is not penalised for its size)
  titles  how much of the candidate's working time was spent in roles whose
          title matches the family

A family is only predicted when there is evidence for it: at least two of its skills,
or a matching job title or headline. No model is involved, so the same profile always
gives the same answer and every score comes with what produced it. The family's title words
double as the search roles used to focus job fetching.
"""

import re
from dataclasses import dataclass, field

from app.agents.parser import SKILL_TAXONOMY_WEIGHTS

SKILL_SHARE = 0.6
TITLE_SHARE = 0.4
SATURATION = 0.5          # holding half of a family's skill weight counts as full coverage
MIN_SCORE = 0.25
MIN_SKILLS = 2             # a single generic skill (say, excel) is not evidence of a field


@dataclass(frozen=True)
class Family:
    name: str
    skills: frozenset[str]
    titles: tuple[str, ...]


FAMILIES: tuple[Family, ...] = (
    Family("DevOps, SRE and Cloud",
           frozenset({"docker", "kubernetes", "terraform", "ansible", "jenkins", "ci/cd", "aws", "azure",
                      "linux", "prometheus", "grafana", "git"}),
           ("devops", "dev ops", "sre", "site reliability", "cloud engineer", "cloud architect",
            "platform engineer", "infrastructure engineer", "release engineer", "systems engineer")),
    Family("Backend Engineering",
           frozenset({"python", "java", "go", "golang", "ruby", "php", "c#", ".net", "c++", "rust", "spring",
                      "django", "flask", "fastapi", "node.js", "express", "rest apis", "graphql", "sql"}),
           ("backend", "back-end", "back end", "software engineer", "software developer", "api developer",
            "python developer", "java developer", "golang developer", ".net developer")),
    Family("Frontend Engineering",
           frozenset({"javascript", "typescript", "react", "angular", "vue", "next.js", "html", "css"}),
           ("frontend", "front-end", "front end", "ui developer", "web developer", "react developer")),
    Family("Full-Stack Engineering",
           frozenset({"javascript", "typescript", "react", "node.js", "python", "sql", "html", "css", "rest apis"}),
           ("full stack", "full-stack", "fullstack")),
    Family("Mobile Engineering",
           frozenset({"android", "ios", "kotlin", "swift", "flutter", "react native"}),
           ("android", "ios developer", "ios engineer", "mobile", "flutter")),
    Family("Data Engineering",
           frozenset({"sql", "spark", "airflow", "snowflake", "postgresql", "mysql", "mongodb", "redis",
                      "oracle", "python"}),
           ("data engineer", "etl", "analytics engineer", "big data", "database engineer", "dba")),
    Family("Data Science and ML",
           frozenset({"python", "machine learning", "pandas", "numpy", "scikit-learn", "tensorflow", "pytorch",
                      "nlp", "computer vision", "sql"}),
           ("data scientist", "machine learning", "ml engineer", "ai engineer", "nlp engineer",
            "research scientist", "applied scientist")),
    Family("Data Analytics and BI",
           frozenset({"sql", "excel", "tableau", "power bi", "data analysis", "python", "pandas"}),
           ("data analyst", "business analyst", "bi developer", "business intelligence", "analytics")),
    Family("Product and Project Management",
           frozenset({"product management", "project management", "agile", "scrum", "jira"}),
           ("product manager", "project manager", "program manager", "scrum master", "product owner")),
    Family("Design",
           frozenset({"figma", "ui/ux", "css", "html"}),
           ("designer", "ux", "ui/ux", "product design")),
    Family("Marketing and Content",
           frozenset({"seo", "content marketing", "excel", "salesforce"}),
           ("marketing", "seo", "content", "growth", "brand")),
    Family("Enterprise Applications",
           frozenset({"sap", "salesforce", "workday", "oracle", "accounting", "excel"}),
           ("sap", "salesforce", "workday", "erp", "crm", "finance analyst", "accountant")),
)

_UNKNOWN = [s for fam in FAMILIES for s in fam.skills if s not in SKILL_TAXONOMY_WEIGHTS]
assert not _UNKNOWN, f"families reference skills outside the taxonomy: {_UNKNOWN}"


@dataclass(frozen=True)
class RoleSpan:
    """One job: its title and how many months it lasted (used to weight titles)."""
    title: str
    months: float = 12.0


@dataclass(frozen=True)
class DomainScore:
    family: str
    score: float
    skill_signal: float
    title_signal: float | None
    matched_skills: tuple[str, ...]
    matched_titles: tuple[str, ...]
    role_keywords: tuple[str, ...] = field(default=())


def _has(text: str, term: str) -> bool:
    return bool(re.search(r"(?<![\w-])" + re.escape(term) + r"(?![\w-])", text))


def predict_domains(skills, roles: list[RoleSpan] | None = None, headline: str = "", top: int = 3) -> list[DomainScore]:
    """Return up to `top` families scoring at least MIN_SCORE, best first."""
    held = {str(s).casefold() for s in skills}
    roles = [r for r in (roles or []) if r.title.strip()]
    total_months = sum(max(r.months, 1.0) for r in roles)
    headline_l = (headline or "").casefold()
    have_titles = bool(roles) or bool(headline_l.strip())

    scored: list[DomainScore] = []
    for family in FAMILIES:
        weights = {s: SKILL_TAXONOMY_WEIGHTS[s] for s in family.skills}
        matched = sorted(s for s in family.skills if s in held)
        skill_signal = min(1.0, sum(weights[s] for s in matched) / (SATURATION * sum(weights.values())))

        title_signal: float | None = None
        headline_hit = False
        matched_titles: list[str] = []
        if have_titles:
            weighted = 0.0
            for role in roles:
                title_l = role.title.casefold()
                if any(_has(title_l, t) for t in family.titles):
                    weighted += max(role.months, 1.0)
                    matched_titles.append(role.title)
            if roles:
                title_signal = weighted / total_months
            if any(_has(headline_l, t) for t in family.titles):
                headline_hit = True
                title_signal = max(title_signal or 0.0, 0.6)  # a headline match is a real but weaker signal
        score = skill_signal if title_signal is None else SKILL_SHARE * skill_signal + TITLE_SHARE * title_signal
        has_evidence = len(matched) >= MIN_SKILLS or matched_titles or headline_hit
        if score >= MIN_SCORE and has_evidence:
            scored.append(DomainScore(family.name, round(score, 3), round(skill_signal, 3),
                                      None if title_signal is None else round(title_signal, 3),
                                      tuple(matched), tuple(dict.fromkeys(matched_titles)), family.titles))
    scored.sort(key=lambda d: (-d.score, d.family))
    return scored[:top]


def target_roles(domains: list[DomainScore], limit: int = 12) -> list[str]:
    """Job-title words to search for, drawn from the predicted families."""
    roles: list[str] = []
    for domain in domains:
        for keyword in domain.role_keywords:
            if keyword not in roles:
                roles.append(keyword)
    return roles[:limit]


def domain_dicts(domains: list[DomainScore]) -> list[dict]:
    """JSON-friendly form of predicted domains (used by the API and stored profiles)."""
    return [{"family": d.family, "score": d.score, "skill_signal": d.skill_signal,
             "title_signal": d.title_signal, "matched_skills": list(d.matched_skills),
             "matched_titles": list(d.matched_titles)} for d in domains]
