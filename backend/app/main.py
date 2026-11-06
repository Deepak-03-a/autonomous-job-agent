import os
import json
import secrets
import threading
from fastapi import FastAPI, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pathlib import Path

from app.database import init_db, get_db
from app.agents.parser import ResumeParserAgent, SKILL_TAXONOMY_WEIGHTS, extract_skills_from_text
from app.agents.crawler import EthicalCrawlerAgent, MarketDiscoveryAgent
from app.agents.matcher import HybridJobMatcherAgent, build_market_brief
from app.agents.advisor import SkillGapAdvisorAgent
from core.config import settings
from adapters.db.app_store import PostgresApplicationStore
from adapters.llm.embeddings import MiniLMEmbedder
from adapters.llm.ollama import OllamaClient

app = FastAPI(
    title="Autonomous Multi-Agent Job Alignment & Career Portal",
    description="SASTRA Deemed University MCA Capstone Project (CAPOL601R01) - Master Production Edition",
    version="2.5.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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

if STATIC_ROOT.exists():
    app.mount("/static", StaticFiles(directory=STATIC_ROOT), name="static")
if FRONTEND_ROOT.exists():
    app.mount("/frontend", StaticFiles(directory=FRONTEND_ROOT), name="frontend")


@app.middleware("http")
async def optional_api_token(request: Request, call_next):
    """Require a server-side bearer token or a valid hashed browser session."""
    if settings.api_token and request.url.path.startswith("/api/"):
        bearer = request.headers.get("authorization", "")
        cookie = request.cookies.get("talentalign_session", "")
        authorized = (
            secrets.compare_digest(bearer, f"Bearer {settings.api_token}")
            or (POSTGRES_STORE is not None and POSTGRES_STORE.is_valid_web_session(cookie))
        )
        if not authorized:
            return JSONResponse({"detail": "A valid bearer token is required."}, status_code=401)
    return await call_next(request)

@app.on_event("startup")
def startup_event():
    if POSTGRES_STORE is not None:
        POSTGRES_STORE.migrate(POSTGRES_MIGRATION)
    else:
        init_db()
    # The embedding model is optional. Warm it in the background so the API
    # answers immediately and falls back to word overlap if the download fails.
    threading.Thread(target=matcher_agent.warm_embedder, daemon=True).start()

parser_agent = ResumeParserAgent()
crawler_agent = EthicalCrawlerAgent()
market_agent = MarketDiscoveryAgent()
matcher_agent = HybridJobMatcherAgent(alpha=0.60, decay_lambda=0.35)
advisor_agent = SkillGapAdvisorAgent()
ollama_client = OllamaClient(settings.ollama_url, settings.ollama_model, keep_alive=settings.ollama_keep_alive)

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
            context_text = json.dumps(context, ensure_ascii=False)
            # Only display generated text when every claimed evidence snippet
            # is present in the stored context.
            if not evidence or any(not isinstance(item, str) or item not in context_text for item in evidence):
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
    
    if POSTGRES_STORE is not None:
        resume_id = POSTGRES_STORE.insert_resume(file.filename, parsed_data, user_id)
    else:
        db = get_db()
        cursor = db.cursor()
        cursor.execute(
            "INSERT INTO resumes (user_id, filename, raw_text, exp_years, skills_json) VALUES (?, ?, ?, ?, ?)",
            (user_id, file.filename, parsed_data["raw_text"], parsed_data["exp_years"], json.dumps(parsed_data["skills"]))
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
        "preview": parsed_data["raw_text"][:500]
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
            query_embedding = RESUME_EMBEDDER.encode([resume["raw_text"]])[0]
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


def _job_payload(row) -> dict:
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
        "is_startup": bool(row["is_startup"]),
        "posted_at": row["posted_at"] or "",
    }


@app.post("/api/v1/rank-market")
def rank_market(resume_id: int = Form(1)):
    """Score every stored opening and attach the skill that would move each match."""
    db = None
    try:
        if POSTGRES_STORE is not None:
            resume = POSTGRES_STORE.get_resume(resume_id) or POSTGRES_STORE.latest_resume()
            rows = (
                POSTGRES_STORE.search_jobs_by_embedding(
                    RESUME_EMBEDDER.encode([resume["raw_text"]])[0],
                    limit=100,
                )
                if resume else []
            )
        else:
            db = get_db()
            cursor = db.cursor()
            resume = cursor.execute("SELECT * FROM resumes WHERE id = ?", (resume_id,)).fetchone()
            if not resume:
                resume = cursor.execute("SELECT * FROM resumes ORDER BY id DESC LIMIT 1").fetchone()
            rows = cursor.execute("SELECT * FROM jobs ORDER BY id ASC").fetchall()
        if not resume:
            raise HTTPException(status_code=404, detail="Upload a resume before ranking the market.")

        if not rows:
            return {
                "status": "success",
                "resume_id": resume["id"],
                "semantic_mode": "no_jobs",
                "total_ranked": 0,
                "market_brief": {},
                "matches": [],
            }

        resume_skills = resume["skills"] if POSTGRES_STORE is not None else json.loads(resume["skills_json"] or "[]")
        jobs = [_job_payload(row) for row in rows]
        ranked, semantic_mode = matcher_agent.rank_jobs(
            resume["raw_text"] or "",
            resume_skills,
            resume["exp_years"] or 0.0,
            jobs,
        )

        matches = []
        for item in ranked:
            job = item["job"]
            evaluation = item["evaluation"]
            advice = advisor_agent.generate_advice(
                resume_skills,
                job["weights"],
                job_title=job["title"],
                score_lifts=evaluation["skill_lifts"],
            )
            matches.append({
                "job_id": job["id"],
                "company_details": {
                    "company_name": job["company"],
                    "role_title": job["title"],
                    "portal_url": job["url"],
                    "required_experience_years": job["req_years"],
                    "location": job["location"],
                    "source": job["source"],
                    "is_startup": job["is_startup"],
                },
                "candidate_profile": {
                    "detected_experience_years": resume["exp_years"],
                    "matched_skills": advice["matched_skills"],
                    "missing_skills": advice["missing_skills"],
                },
                "evaluation": evaluation,
                "advisory": advice,
            })

        return {
            "status": "success",
            "resume_id": resume["id"],
            "semantic_mode": semantic_mode,
            "total_ranked": len(matches),
            "market_brief": build_market_brief(ranked),
            "matches": matches,
        }
    finally:
        if db is not None:
            db.close()
