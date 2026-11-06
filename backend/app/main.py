import os
import json
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
    """Require a bearer token only when Linux deployment configures one."""
    if settings.api_token and request.url.path.startswith("/api/"):
        if request.headers.get("authorization") != f"Bearer {settings.api_token}":
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

@app.get("/", response_class=HTMLResponse)
def read_dashboard():
    react_index_path = FRONTEND_ROOT / "index.html"
    if react_index_path.exists():
        return react_index_path.read_text(encoding="utf-8")

    template_path = os.path.join(os.path.dirname(__file__), "templates", "dashboard.html")
    if os.path.exists(template_path):
        with open(template_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>Dashboard template not found.</h1>"

@app.get("/health")
def health_check():
    return {
        "status": "healthy",
        "course_code": "CAPOL601R01",
        "system": "Autonomous Multi-Agent Career & Job Discovery Engine",
        "candidate": "Ram Deepak Kasavajhala"
    }

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
            raise HTTPException(status_code=404, detail="Discover openings before ranking them.")

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
