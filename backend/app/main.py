import os
import json
import secrets
import threading
from contextlib import asynccontextmanager
from datetime import date
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.database import init_db, get_db
from adapters.imports.linkedin_zip import ExportError, read_export
from core.domains import domain_dicts, predict_domains, target_roles
from app.services.agent import run_agent
from app.services.ranking import NoProfile
from core.linkedin import combine, parse_tables
from app.agents.parser import ResumeParserAgent, SKILL_TAXONOMY_WEIGHTS, extract_skills_from_text
from app.agents.crawler import EthicalCrawlerAgent, MarketDiscoveryAgent
from app.agents.matcher import HybridJobMatcherAgent, build_market_brief
from app.agents.advisor import SkillGapAdvisorAgent
from core.config import settings
from adapters.db.app_store import PostgresApplicationStore
from adapters.llm.embeddings import MiniLMEmbedder
from adapters.llm.ollama import OllamaClient
from app.services.context import AgentContext
from app.services.grounding import evidence_grounded
from app.services.ranking import (
    job_payload, load_profile, rank_market as rank_market_for, retrieval_profile_text,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    if not settings.api_token:
        raise RuntimeError("Set TALENTALIGN_API_TOKEN before starting the API.")
    if len(settings.api_token) < 32:
        raise RuntimeError("TALENTALIGN_API_TOKEN must contain at least 32 characters.")
    if settings.api_token == "replace-with-a-long-random-token":
        raise RuntimeError("Replace the example TALENTALIGN_API_TOKEN with a private random value.")
    if (settings.environment not in {"development", "test"}
            and not settings.database_url.startswith("postgresql")):
        raise RuntimeError("Deployments must use PostgreSQL; SQLite is reserved for tests/development.")
    if "*" in settings.cors_origins:
        raise RuntimeError("CORS_ORIGINS must list explicit origins; wildcard CORS is disabled.")
    if POSTGRES_STORE is not None:
        for migration in POSTGRES_MIGRATIONS:
            POSTGRES_STORE.migrate(migration)
    else:
        init_db()
    warmer = threading.Thread(target=matcher_agent.warm_embedder, daemon=True)
    warmer.start()
    yield


app = FastAPI(
    title="Autonomous Multi-Agent Job Alignment & Career Portal",
    description="SASTRA Deemed University MCA Capstone Project (CAPOL601R01) - Master Production Edition",
    version="2.5.0",
    lifespan=lifespan,
)

if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type"],
    )

APP_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = APP_ROOT.parent
if not (PROJECT_ROOT / "frontend").exists():
    PROJECT_ROOT = APP_ROOT.parents[1]
FRONTEND_ROOT = PROJECT_ROOT / "frontend"
STATIC_ROOT = APP_ROOT / "static"
POSTGRES_STORE = (
    PostgresApplicationStore(settings.database_url)
    if settings.database_url.startswith("postgresql")
    else None
)
RESUME_EMBEDDER = MiniLMEmbedder(settings.embedding_model, settings.embedding_dimensions) if POSTGRES_STORE else None
POSTGRES_MIGRATION = APP_ROOT.parent / "adapters" / "db" / "migrations" / "001_initial.sql"
if not POSTGRES_MIGRATION.exists():
    POSTGRES_MIGRATION = PROJECT_ROOT / "backend" / "adapters" / "db" / "migrations" / "001_initial.sql"
POSTGRES_MIGRATIONS = sorted(POSTGRES_MIGRATION.parent.glob("*.sql"))
MAX_EXPORT_BYTES = int(os.getenv("MAX_EXPORT_BYTES", str(25 * 1024 * 1024)))

if STATIC_ROOT.exists():
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
if FRONTEND_ROOT.exists():
    # Serve only the page source, not package metadata, tests, or dependencies.
    app.mount("/frontend/src", StaticFiles(directory=FRONTEND_ROOT / "src"), name="frontend")


@app.middleware("http")
async def required_api_token(request: Request, call_next):
    """Fail closed when the API secret is missing; accept bearer or DB session."""
    if request.url.path.startswith("/api/"):
        if not settings.api_token:
            return JSONResponse({"detail": "The server has no TALENTALIGN_API_TOKEN configured."}, status_code=503)
        bearer = request.headers.get("authorization", "")
        cookie = request.cookies.get("talentalign_session", "")
        authorized = (
            secrets.compare_digest(bearer, f"Bearer {settings.api_token}")
            or (POSTGRES_STORE is not None and POSTGRES_STORE.is_valid_web_session(cookie))
        )
        if not authorized:
            return JSONResponse({"detail": "A valid bearer token is required."}, status_code=401)
    return await call_next(request)

parser_agent = ResumeParserAgent()
crawler_agent = EthicalCrawlerAgent()
market_agent = MarketDiscoveryAgent()
matcher_agent = HybridJobMatcherAgent(alpha=0.60, decay_lambda=0.35)
advisor_agent = SkillGapAdvisorAgent()
ollama_client = OllamaClient(settings.ollama_url, settings.ollama_model, keep_alive=settings.ollama_keep_alive)


def build_agent_context() -> AgentContext:
    """Build a fresh context so agent caches are isolated to one request."""
    short = lambda seconds: OllamaClient(
        settings.ollama_url, settings.ollama_model,
        timeout=seconds, keep_alive=settings.ollama_keep_alive,
    )
    return AgentContext(
        postgres=POSTGRES_STORE, get_db=get_db, matcher=matcher_agent,
        advisor=advisor_agent, embedder=RESUME_EMBEDDER,
        planner_llm=short(30.0), narrator_llm=short(45.0),
    )

@app.get("/", response_class=HTMLResponse)
def read_dashboard(request: Request):
    react_index_path = FRONTEND_ROOT / "index.html"
    if react_index_path.exists():
        page = react_index_path.read_text(encoding="utf-8")
    else:
        template_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
        if os.path.exists(template_path):
            with open(template_path, "r", encoding="utf-8") as f:
                page = f.read()
        else:
            page = "<h1>Dashboard template not found.</h1>"
    response = HTMLResponse(page)
    if settings.api_token:
        # Give the browser a per-session opaque token. PostgreSQL stores only
        # its hash; JavaScript cannot read the HttpOnly cookie.
        forwarded_proto = request.headers.get("x-forwarded-proto", "")
        session = request.cookies.get("talentalign_session", "")
        if POSTGRES_STORE is not None and not POSTGRES_STORE.is_valid_web_session(session):
            session = POSTGRES_STORE.issue_web_session()
        elif POSTGRES_STORE is None:
            # SQLite development mode retains compatibility with the configured
            # bearer token; production PostgreSQL deployments use DB sessions.
            session = settings.api_token
        response.set_cookie(
            "talentalign_session",
            session,
            httponly=True,
            secure=request.url.scheme == "https" or forwarded_proto == "https",
            samesite="strict",
            path="/api/",
            max_age=60 * 60 * 24 * 30,
        )
    return response

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "course_code": "CAPOL601R01",
        "system": "Autonomous Multi-Agent Career & Job Discovery Engine",
        "candidate": "Ram Deepak Kasavajhala"
    }


@app.post("/api/v1/chat")
def chat_about_match(payload: dict):
    """Explain a selected job match using stored data and Ollama when available."""
    question = str(payload.get("question", "")).strip()
    if not question or len(question) > 1000:
        raise HTTPException(status_code=400, detail="Question must contain 1 to 1000 characters.")
    try:
        job_id = int(payload.get("job_id"))
        resume_id = int(payload.get("resume_id", 1))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="A valid job_id and resume_id are required.")

    db = None
    try:
        if POSTGRES_STORE is not None:
            job = POSTGRES_STORE.get_chat_job(job_id)
            resume = POSTGRES_STORE.get_resume(resume_id)
        else:
            db = get_db()
            cursor = db.cursor()
            job_row = cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
            resume_row = cursor.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()
            job = dict(job_row) if job_row else None
            resume = dict(resume_row) if resume_row else None
            if job:
                job["weights"] = json.loads(job.get("weights_json") or "{}")
            if resume:
                resume["skills"] = json.loads(resume.get("skills_json") or "[]")
        if not job or not resume:
            raise HTTPException(status_code=404, detail="Select a saved job and uploaded resume first.")

        skills = resume.get("skills") or []
        weights = job.get("weights") or {}
        context = {
            "job_title": job.get("title") or "",
            "company": job.get("company") or job.get("company_id") or "",
            "location": job.get("location") or "",
            "job_description": (job.get("description") or "")[:5000],
            "required_experience_years": job.get("req_years") or 0,
            "job_skills": list(weights.keys()) if isinstance(weights, dict) else [],
            "candidate_skills": skills,
            "candidate_experience_years": resume.get("exp_years") or 0,
        }
        fallback = (
            f"{context['job_title']} at {context['company']} is listed in "
            f"{context['location'] or 'an unspecified location'}. Your resume lists "
            f"{', '.join(skills[:8]) if skills else 'no extracted skills'}. "
            "I could not reach the local Ollama model, so this is a data-only summary."
        )
        try:
            result = ollama_client.explain(question, context)
            evidence = result["evidence"]
            # Only display generated text when every claimed evidence snippet
            # is present in the stored context.
            if not evidence_grounded(evidence, context):
                raise ValueError("Explanation evidence did not match stored data")
            return {"answer": result["answer"], "source": "ollama", "evidence": evidence}
        except Exception:
            return {"answer": fallback, "source": "fallback", "evidence": []}
    finally:
        if db is not None:
            db.close()

@app.post("/api/v1/parse-resume-pdf")
async def parse_resume_pdf(file: UploadFile = File(...), user_id: int = Form(1)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    pdf_bytes = await file.read()
    parsed_data = parser_agent.parse_pdf_bytes(pdf_bytes)
    domains = predict_domains(parsed_data["skills"])
    roles = target_roles(domains)

    if POSTGRES_STORE is not None:
        resume_id = POSTGRES_STORE.insert_resume(
            file.filename,
            parsed_data,
            user_id,
            extras={"domains": _domain_dicts(domains), "target_roles": roles},
        )
    else:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO resumes (user_id, filename, raw_text, exp_years, skills_json, extras_json) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, file.filename, parsed_data["raw_text"], parsed_data["exp_years"],
             json.dumps(parsed_data["skills"]),
             json.dumps({"domains": _domain_dicts(domains), "target_roles": roles}))
        )
        db.commit()
        resume_id = cursor.lastrowid
        db.close()

    return {
        "status": "success",
        "resume_id": resume_id,
        "filename": file.filename,
        "detected_experience_years": parsed_data["exp_years"],
        "detected_skills": parsed_data["skills"],
        "predicted_domains": _domain_dicts(domains),
        "target_roles": roles,
        "preview": parsed_data["raw_text"][:500]
    }


_domain_dicts = domain_dicts


@app.post("/api/v1/profile/linkedin-export")
async def import_linkedin_export(file: UploadFile = File(...), resume_id: int | None = Form(None)):
    """Merge a LinkedIn account data export into the candidate's saved profile."""
    if not (file.filename or "").lower().endswith(".zip"):
        raise HTTPException(status_code=400, detail="Upload the .zip file LinkedIn emailed you.")
    data = await file.read(MAX_EXPORT_BYTES + 1)
    if len(data) > MAX_EXPORT_BYTES:
        raise HTTPException(status_code=413, detail="The LinkedIn export exceeds the upload size limit.")
    try:
        tables, warnings, files_read = read_export(data)
    except ExportError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    if not any(tables.get(name) for name in ("positions", "skills", "profile")):
        raise HTTPException(status_code=400, detail="No LinkedIn profile, positions, or skills files were found.")

    today = date.today()
    profile = parse_tables(tables)
    if POSTGRES_STORE is not None:
        base = POSTGRES_STORE.get_resume(resume_id) if resume_id is not None else POSTGRES_STORE.latest_resume()
        base_skills = list(base["skills"]) if base else []
    else:
        db = get_db()
        try:
            row = (
                db.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()
                if resume_id is not None
                else db.execute("SELECT * FROM resumes ORDER BY id DESC LIMIT 1").fetchone()
            )
            base = dict(row) if row else None
        finally:
            db.close()
        base_skills = json.loads(base.get("skills_json") or "[]") if base else []
    if resume_id is not None and base is None:
        raise HTTPException(status_code=404, detail="Resume record not found.")

    combined = combine(
        base["raw_text"] if base else "",
        base_skills,
        base["exp_years"] if base else None,
        profile,
        today,
    )
    domains = predict_domains(combined.skills, profile.role_spans(today), profile.headline)
    hints = [value.casefold() for value in (profile.preferred_titles + profile.alert_phrases)][:10]
    roles = list(dict.fromkeys(hints + target_roles(domains)))[:15]
    filename = f"linkedin-export ({base['filename']})" if base and base.get("filename") else "linkedin-export"
    parsed = {"raw_text": combined.raw_text, "exp_years": combined.experience_years, "skills": combined.skills}
    extras = {
        "source": "linkedin_export",
        "domains": _domain_dicts(domains),
        "target_roles": roles,
        "extra_skills": combined.extra_skills,
        "alert_phrases": profile.alert_phrases[:20],
        "saved_jobs": profile.saved_jobs[:200],
        "applications": profile.applications[:200],
        "experience_source": combined.experience_source,
        "headline": profile.headline,
        "positions": [{"title": p.title, "company": p.company} for p in profile.positions[:25]],
    }
    if POSTGRES_STORE is not None:
        new_id = POSTGRES_STORE.insert_resume(filename, parsed, 1, extras=extras)
    else:
        db = get_db()
        try:
            cursor = db.execute(
                "INSERT INTO resumes (user_id, filename, raw_text, exp_years, skills_json, extras_json) VALUES (?, ?, ?, ?, ?, ?)",
                (1, filename, parsed["raw_text"], parsed["exp_years"], json.dumps(parsed["skills"]), json.dumps(extras)),
            )
            new_id = cursor.lastrowid
            db.commit()
        finally:
            db.close()

    return {
        "status": "success",
        "resume_id": new_id,
        "filename": filename,
        "files_read": files_read,
        "warnings": warnings,
        "headline": profile.headline,
        "experience": {"years": combined.experience_years, "source": combined.experience_source},
        "positions": [{
            "title": position.title,
            "company": position.company,
            "start": position.start.isoformat() if position.start else None,
            "end": position.end.isoformat() if position.end else None,
        } for position in profile.positions],
        "skills": combined.skills,
        "extra_skills": combined.extra_skills,
        "predicted_domains": _domain_dicts(domains),
        "target_roles": roles,
        "saved_jobs": len(profile.saved_jobs),
        "applications": len(profile.applications),
        "alert_phrases": profile.alert_phrases[:20],
    }

@app.post("/api/v1/market/discover")
def discover_live_market(
    resume_id: int = Form(1),
    country: str = Form("India"),
    max_pages: int | None = Form(None),
):
    if POSTGRES_STORE is not None:
        resume = POSTGRES_STORE.get_resume(resume_id) or POSTGRES_STORE.latest_resume()
        if not resume:
            market_jobs = []
        else:
            query_embedding = RESUME_EMBEDDER.encode([retrieval_profile_text(resume)])[0]
            market_jobs = POSTGRES_STORE.search_jobs_by_embedding(query_embedding, country, limit=100)
        return {
            "status": "success",
            "country": country,
            "cache_hit": False,
            "total_discovered": len(market_jobs),
            "result_limit": None,
            "source_errors": [],
            "jobs": market_jobs,
        }
    else:
        db = get_db()
        cursor = db.cursor()
        resume = cursor.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()
        if not resume:
            resume = cursor.execute("SELECT * FROM resumes ORDER BY id DESC LIMIT 1").fetchone()
    
    cand_years = resume["exp_years"] if resume else 0.0
    stored_skills = resume.get("skills") if POSTGRES_STORE is not None and resume else None
    resume_skills = (
        stored_skills if isinstance(stored_skills, list)
        else json.loads(resume["skills_json"])
        if resume else []
    )
    resume_text = resume["raw_text"] if resume else ""
    
    market_jobs = market_agent.discover_market_jobs(
        resume_skills,
        cand_years,
        resume_text=resume_text,
        country=country,
        max_pages=max_pages,
    )
    
    if POSTGRES_STORE is not None:
        POSTGRES_STORE.upsert_jobs(market_jobs, country)
    else:
        # Automatically ingest all market jobs into SQLite database.
        for job in market_jobs:
            cursor.execute(
                """INSERT OR REPLACE INTO jobs
                   (job_code, title, company, url, description, req_years, weights_json,
                    location, country, source, posted_at, is_startup)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    job["job_code"], job["title"], job["company"], job["portal_url"],
                    job["description"], job["req_years"], json.dumps(job["weights"]),
                    job["location"], job["country"] or country, job["source"],
                    job["posted_at"], int(job["is_startup"]),
                )
            )
        db.commit()
        db.close()
    
    return {
        "status": "success",
        "country": country,
        "cache_hit": market_agent.last_cache_hit,
        "total_discovered": len(market_jobs),
        "result_limit": None,
        "source_errors": market_agent.last_errors,
        "jobs": market_jobs
    }

@app.post("/api/v1/jobs/crawl")
async def crawl_job_portal(
    url: str = Form(...),
    job_code: str = Form(...),
    title: str = Form(...),
    company: str = Form(...),
    req_years: float = Form(3.0)
):
    try:
        crawl_res = await crawler_agent.crawl_and_prune(url)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
        
    default_weights = {
        skill: SKILL_TAXONOMY_WEIGHTS[skill]
        for skill in extract_skills_from_text(crawl_res["clean_text"])
    }
    if not default_weights:
        default_weights = {"general": 1.0}
    
    if POSTGRES_STORE is not None:
        postgres_ids = POSTGRES_STORE.upsert_jobs(
            [{
                "job_code": job_code,
                "title": title,
                "company": company,
                "portal_url": url,
                "description": crawl_res["clean_text"],
                "req_years": req_years,
                "weights": default_weights,
                "location": "Location not specified",
                "source": "manual",
                "careers_url": url,
                "company_website": url,
                "application_url": url,
                "provider": "direct",
            }],
            "",
        )
        job_id = postgres_ids[0] if postgres_ids else None
    else:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            """INSERT OR REPLACE INTO jobs
               (job_code, title, company, url, description, req_years, weights_json,
                location, country, source, posted_at, is_startup)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                job_code, title, company, url, crawl_res["clean_text"], req_years,
                json.dumps(default_weights), "Location not specified", "", "Manual crawl", "", 0,
            )
        )
        db.commit()
        job_id = cursor.lastrowid
        db.close()
    
    return {
        "status": "success",
        "job_id": job_id,
        "company": company,
        "title": title,
        "compression_metrics": {
            "raw_bytes": crawl_res["raw_bytes"],
            "clean_bytes": crawl_res["clean_bytes"],
            "payload_reduction_pct": crawl_res["compression_pct"]
        },
        "message": f"Successfully crawled {company} portal with {crawl_res['compression_pct']}% DOM bloat reduction."
    }

@app.post("/api/v1/evaluate-matches")
def evaluate_matches(resume_id: int = Form(1), job_id: int = Form(1)):
    if POSTGRES_STORE is not None:
        resume = POSTGRES_STORE.get_resume(resume_id) or POSTGRES_STORE.latest_resume()
        job = POSTGRES_STORE.get_job(job_id) or POSTGRES_STORE.latest_job()
        cursor = db = None
    else:
        db = get_db()
        cursor = db.cursor()
        resume = cursor.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()
        job = cursor.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if not resume:
            resume = cursor.execute("SELECT * FROM resumes ORDER BY id DESC LIMIT 1").fetchone()
        if not job:
            job = cursor.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 1").fetchone()
    if not resume or not job:
        if db is not None:
            db.close()
        raise HTTPException(status_code=404, detail="Resume or Job record not found in SQLite database.")
        
    resume_skills = resume["skills"] if POSTGRES_STORE is not None else json.loads(resume["skills_json"])
    job_weights = job["weights"] if POSTGRES_STORE is not None else json.loads(job["weights_json"])
    
    eval_res = matcher_agent.evaluate_job_match(
        resume_text=resume["raw_text"],
        job_text=job["description"],
        resume_skills=resume_skills,
        job_weights=job_weights,
        cand_years=resume["exp_years"],
        req_years=job["req_years"]
    )
    advice = advisor_agent.generate_advice(
        resume_skills,
        job_weights,
        job_title=job["title"] or "",
        score_lifts=eval_res["skill_lifts"],
    )
    
    if POSTGRES_STORE is not None:
        POSTGRES_STORE.insert_match(resume["id"], job, eval_res)
    else:
        cursor.execute(
            """INSERT INTO match_results
               (candidate_name, job_title, company, s_sem_pct, s_skill_pct, phi_exp, s_total_pct, match_tier, missing_json, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
            ("Candidate", job["title"], job["company"], eval_res["s_sem_pct"], eval_res["s_skill_pct"], eval_res["phi_exp"], eval_res["final_match_score_pct"], eval_res["match_tier"], json.dumps(advice["missing_skills"]))
        )
        db.commit()
        db.close()
    
    return {
        "status": "success",
        "company_details": {
            "company_name": job["company"],
            "role_title": job["title"],
            "portal_url": job["url"],
            "required_experience_years": job["req_years"]
        },
        "candidate_profile": {
            "detected_experience_years": resume["exp_years"],
            "matched_skills": advice["matched_skills"],
            "missing_skills": advice["missing_skills"]
        },
        "evaluation": eval_res,
        "advisory": advice
    }


_job_payload = job_payload  # kept for the other endpoints in this module


@app.post("/api/v1/rank-market")
def rank_market(resume_id: int = Form(1)):
    """Score every stored opening and attach the skill that would move each match."""
    ctx = build_agent_context()
    profile = load_profile(ctx, resume_id, fallback_latest=True)
    if profile is None:
        raise HTTPException(status_code=404, detail="Upload a resume before ranking the market.")
    ranking = rank_market_for(ctx, profile)
    if not ranking.matches:
        return {"status": "success", "resume_id": profile["id"], "semantic_mode": "no_jobs",
                "total_ranked": 0, "market_brief": {}, "matches": []}
    return {"status": "success", "resume_id": profile["id"], "semantic_mode": ranking.semantic_mode,
            "total_ranked": len(ranking.matches), "market_brief": ranking.market_brief, "matches": ranking.matches}


# ---------------------------------------------------------------------------
# Job-search agent: plan, run deterministic tools, report. See app/services/agent.py.
# ---------------------------------------------------------------------------
@app.post("/api/v1/agent/run")
def agent_run(goal: str = Form(""), resume_id: int | None = Form(None),
              planner: str = Form("auto"), narrative: str = Form("auto")):
    """Run the job-search agent for your newest profile (or the given resume_id)."""
    if len(goal) > 500:
        raise HTTPException(status_code=400, detail="The goal must be 500 characters or fewer.")
    if planner not in ("auto", "fixed") or narrative not in ("auto", "off"):
        raise HTTPException(status_code=400, detail="planner is auto|fixed and narrative is auto|off.")
    try:
        result = run_agent(build_agent_context(), goal, resume_id, planner, narrative)
    except NoProfile as error:
        raise HTTPException(status_code=404, detail=str(error))
    run_id = None
    if POSTGRES_STORE is not None:
        run_id = POSTGRES_STORE.insert_agent_run(result["goal"], result["planner"]["mode"], result["status"],
                                                 result["trace"], result["report"])
    return {"run_id": run_id, **result}


@app.get("/api/v1/agent/runs")
def agent_runs(limit: int = 20):
    return {"runs": POSTGRES_STORE.list_agent_runs(limit) if POSTGRES_STORE is not None else []}


@app.get("/api/v1/agent/runs/{run_id}")
def agent_run_detail(run_id: int):
    row = POSTGRES_STORE.get_agent_run(run_id) if POSTGRES_STORE is not None else None
    if row is None:
        raise HTTPException(status_code=404, detail="That run was not found (runs are saved only with PostgreSQL).")
    return row
