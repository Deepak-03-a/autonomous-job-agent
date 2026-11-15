"""Turn the tables in a LinkedIn data export into a candidate profile.

Pure logic, no file or network access: callers pass in already-read CSV rows. The
reader (adapters/imports/linkedin_zip.py) decides which files may be opened.

Only fields useful for matching are kept. Contact emails, phone numbers, free-text
application answers and everything else in the export are never read into the
profile, so they cannot leak into the database or the logs.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from app.agents.parser import SKILL_TAXONOMY_WEIGHTS, extract_skills_from_text
from core.domains import RoleSpan

MAX_EXTRA_SKILLS = 50
MAX_TEXT_CHARS = 20_000

_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], start=1)}


def _key(value: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (value or "").casefold())


def pick(row: dict, *aliases: str) -> str:
    """Value of the first column whose normalised header matches an alias."""
    lookup = {_key(k): v for k, v in row.items() if k}
    for alias in aliases:
        value = lookup.get(_key(alias))
        if value and str(value).strip():
            return str(value).strip()
    return ""


def parse_month(text: str) -> date | None:
    """Accepts 'Jan 2022', 'January 2022', '2022', '01/2022', '2022-01', '2022-01-15'."""
    text = (text or "").strip()
    if not text:
        return None
    match = (re.fullmatch(r"([A-Za-z]{3,9})\.?,?\s+(\d{4})", text)
             or re.fullmatch(r"(\d{1,2})[/-](\d{4})", text)
             or re.fullmatch(r"(\d{4})[/-](\d{1,2})(?:[/-]\d{1,2})?", text)
             or re.fullmatch(r"(\d{4})", text))
    if not match:
        return None
    parts = match.groups()
    year, month = None, 1
    if len(parts) == 1:
        year = int(parts[0])
    elif parts[0].isalpha():
        month, year = _MONTHS.get(parts[0][:3].casefold()), int(parts[1])
    elif len(parts[0]) == 4:
        year, month = int(parts[0]), int(parts[1])
    else:
        month, year = int(parts[0]), int(parts[1])
    if not month or not 1 <= month <= 12 or not 1970 <= year <= 2100:
        return None
    return date(year, month, 1)


@dataclass(frozen=True)
class Position:
    title: str
    company: str
    start: date | None
    end: date | None          # None = still in the role
    description: str = ""

    def months(self, today: date) -> float:
        if not self.start:
            return 0.0
        end = self.end or today
        return max(0.0, (end.year - self.start.year) * 12 + (end.month - self.start.month))


@dataclass
class LinkedInProfile:
    headline: str = ""
    summary: str = ""
    positions: list[Position] = field(default_factory=list)
    skills: list[str] = field(default_factory=list)
    education: list[str] = field(default_factory=list)
    certifications: list[str] = field(default_factory=list)
    saved_jobs: list[dict] = field(default_factory=list)
    applications: list[dict] = field(default_factory=list)
    alert_phrases: list[str] = field(default_factory=list)
    preferred_titles: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def role_spans(self, today: date) -> list[RoleSpan]:
        return [RoleSpan(p.title, p.months(today) or 12.0) for p in self.positions]

    def experience_years(self, today: date) -> float:
        """Years covered by the positions, counting overlapping roles once."""
        spans = sorted((p.start, p.end or today) for p in self.positions if p.start)
        total_months, current_end = 0, None
        for start, end in spans:
            s = start.year * 12 + start.month
            e = max(end.year * 12 + end.month, s)
            if current_end is None or s > current_end:
                total_months += e - s
                current_end = e
            elif e > current_end:
                total_months += e - current_end
                current_end = e
        return round(total_months / 12, 1)

    def text(self) -> str:
        parts = [self.headline, self.summary]
        parts += [f"{p.title} at {p.company}. {p.description}".strip() for p in self.positions]
        parts += self.certifications
        return "\n".join(p for p in parts if p)[:MAX_TEXT_CHARS]


def _split_list(value: str) -> list[str]:
    return [p.strip() for p in re.split(r"[;|\n]", value or "") if p.strip()]


def parse_tables(tables: dict[str, list[dict]]) -> LinkedInProfile:
    """`tables` maps a canonical name (profile, positions, skills, ...) to CSV rows."""
    profile = LinkedInProfile()

    for row in tables.get("profile", [])[:1]:
        profile.headline = pick(row, "Headline")
        profile.summary = pick(row, "Summary")

    for row in tables.get("positions", []):
        title, company = pick(row, "Title"), pick(row, "Company Name", "Company")
        if not title and not company:
            continue
        profile.positions.append(Position(
            title=title, company=company,
            start=parse_month(pick(row, "Started On", "Start Date")),
            end=parse_month(pick(row, "Finished On", "End Date")),
            description=pick(row, "Description"),
        ))

    seen = set()
    for row in tables.get("skills", []):
        name = pick(row, "Name", "Skill")
        if name and name.casefold() not in seen and len(name) <= 60:
            seen.add(name.casefold())
            profile.skills.append(name)

    profile.education = [x for x in (pick(r, "School Name", "School") for r in tables.get("education", [])) if x]
    profile.certifications = [x for x in (pick(r, "Name") for r in tables.get("certifications", [])) if x]

    for row in tables.get("saved_jobs", []):
        title = pick(row, "Job Title", "Title")
        if title:
            profile.saved_jobs.append({"title": title, "company": pick(row, "Company Name", "Company"),
                                       "url": pick(row, "Job Url", "Url", "Link"),
                                       "date": pick(row, "Saved Date", "Save Date", "Date")})

    # Applications: company, title, date and link only. Contact details, the resume
    # name and screening answers in this file are deliberately never read.
    for row in tables.get("applications", []):
        title = pick(row, "Job Title", "Title")
        if title:
            profile.applications.append({"title": title, "company": pick(row, "Company Name", "Company"),
                                         "url": pick(row, "Job Url", "Url"),
                                         "date": pick(row, "Application Date", "Date")})

    for row in tables.get("alerts", []):
        phrase = pick(row, "Search Text", "Keywords", "Query", "Search Query", "Job Title", "Title")
        if phrase and phrase not in profile.alert_phrases:
            profile.alert_phrases.append(phrase)

    for row in tables.get("preferences", []):
        for header, value in row.items():
            if header and "title" in header.casefold():
                profile.preferred_titles.extend(t for t in _split_list(str(value or "")) if t not in profile.preferred_titles)
    return profile


@dataclass(frozen=True)
class CombinedProfile:
    skills: list[str]
    extra_skills: list[str]
    experience_years: float
    experience_source: str
    raw_text: str


def combine(resume_text: str, resume_skills: list[str], resume_years: float | None,
            profile: LinkedInProfile, today: date) -> CombinedProfile:
    """Merge a PDF resume (optional) with the LinkedIn profile for scoring.

    Skills are the union of what the resume names, what LinkedIn lists that is in the
    taxonomy, and taxonomy skills found in the LinkedIn text. LinkedIn skills outside
    the taxonomy are kept separately: they are shown to the candidate but cannot be
    matched against jobs yet.
    """
    linkedin_text = profile.text()
    skills = list(dict.fromkeys(resume_skills))
    extra: list[str] = []
    for name in profile.skills:
        if name.casefold() in SKILL_TAXONOMY_WEIGHTS:
            skills.append(name.casefold())
        else:
            extra.append(name)
    skills += extract_skills_from_text(linkedin_text)

    years = profile.experience_years(today)
    if years > 0:
        source = "linkedin positions"
    else:
        years, source = float(resume_years or 0.0), ("resume text" if resume_years else "unknown")

    text = resume_text.strip()
    if linkedin_text:
        text = f"{text}\n\nLinkedIn profile\n{linkedin_text}".strip()
    return CombinedProfile(list(dict.fromkeys(skills)), extra[:MAX_EXTRA_SKILLS], years, source,
                           text[: MAX_TEXT_CHARS * 2])
