import json
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient

import app.main as main
from app import database
from app.agents.advisor import SkillGapAdvisorAgent
from app.agents.matcher import HybridJobMatcherAgent
from app.services import agent as agent_module
from app.services.agent import (FIXED_PLAN, TOOLS, ToolArgError, make_plan, run_agent, validate_args, validate_plan)
from app.services.context import AgentContext
from app.services.grounding import evidence_grounded, numbers_grounded
from app.services.ranking import NoProfile

SKILLS = ["python", "docker", "kubernetes", "aws", "terraform", "linux", "git"]
RESUME = "DevOps engineer with Docker, Kubernetes, AWS, Terraform, Python and Linux experience."


@pytest.fixture(autouse=True)
def fresh_cooldown():
    agent_module.reset_llm_cooldown()
    yield
    agent_module.reset_llm_cooldown()


@pytest.fixture
def db_factory(tmp_path, monkeypatch):
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "agent.db"))
    database.init_db()
    conn = database.get_db()
    conn.execute("DELETE FROM jobs")
    conn.execute("INSERT INTO resumes (user_id, filename, raw_text, exp_years, skills_json) VALUES (1,'cv.pdf',?,3.0,?)",
                 (RESUME, json.dumps(SKILLS)))
    jobs = [
        ("DevOps Engineer", "Acme", "Pune, India", "kubernetes terraform aws docker pipelines", {"kubernetes": 2.0, "terraform": 1.5, "aws": 1.8}, 2.0, "remote"),
        ("Cloud Engineer", "Beta", "Remote, India", "aws docker linux cloud platform", {"aws": 1.8, "docker": 1.5, "linux": 1.0, "golang": 1.0}, 3.0, "remote"),
        ("Staff SRE", "Gamma", "Delhi, India", "kubernetes golang prometheus grafana sre reliability", {"kubernetes": 2.0, "golang": 1.0, "prometheus": 1.0}, 8.0, ""),
        ("Marketing Lead", "Delta", "Mumbai, India", "brand campaigns seo content", {"seo": 1.2}, 4.0, ""),
    ]
    for i, (title, company, location, desc, weights, years, mode) in enumerate(jobs, 1):
        conn.execute("INSERT INTO jobs (job_code,title,company,url,description,req_years,weights_json,location,country,source,posted_at,is_startup) "
                     "VALUES (?,?,?,?,?,?,?,?,?,?,?,0)",
                     (f"J{i}", title, company, f"https://{company.lower()}.test/apply", desc, years, json.dumps(weights),
                      location, "India", "Test Feed" if mode else "Other Feed", ""))
    conn.commit()
    conn.close()
    return database.get_db


class FakeLLM:
    def __init__(self, plan=None, explain=None, error=None):
        self.plan, self._explain, self.error, self.calls = plan, explain, error, 0

    def generate_json(self, prompt, schema):
        self.calls += 1
        if self.error:
            raise self.error
        return self.plan

    def explain(self, question, context):
        self.calls += 1
        if self.error:
            raise self.error
        return self._explain


def make_ctx(db_factory, planner=None, narrator=None):
    return AgentContext(postgres=None, get_db=db_factory,
                        matcher=HybridJobMatcherAgent(alpha=0.6, decay_lambda=0.35, use_neural_model=False),
                        advisor=SkillGapAdvisorAgent(), embedder=None, planner_llm=planner, narrator_llm=narrator)


# ------------------------------------------------------------ validation
def test_arguments_are_validated_clamped_and_unknown_ones_rejected():
    tool = TOOLS["find_jobs"]
    assert validate_args(tool, {"limit": 500, "min_score": -5, "remote_only": True}) == {"limit": 25, "min_score": 0, "remote_only": True}
    for bad in ({"limit": "ten"}, {"limit": 2.5}, {"remote_only": "yes"}, {"queue": "Hire me"},
                {"location_contains": "x" * 200}, {"delete_everything": True}, "not a dict"):
        with pytest.raises(ToolArgError):
            validate_args(tool, bad)
    with pytest.raises(ToolArgError):
        validate_args(TOOLS["explain_job"], {})           # job_id is required


def test_plans_are_checked_and_repaired():
    plan = validate_plan({"steps": [{"tool": "skill_gaps", "args": {}}]})
    assert [s["tool"] for s in plan] == ["get_profile", "data_freshness", "skill_gaps", "find_jobs"]
    only_job = validate_plan({"steps": [{"tool": "explain_job", "args": {"job_id": 2}}]})
    assert "find_jobs" not in [s["tool"] for s in only_job]       # a question about one job needs no list
    for bad in ({"steps": []}, {"steps": [{"tool": "drop_tables", "args": {}}]}, {"steps": "x"}, {},
                {"steps": [{"tool": "find_jobs", "args": {"limit": 1}}] * 3 + [{"tool": "skill_gaps", "args": {}}] * 4},
                {"steps": [{"tool": "find_jobs", "args": {"sql": "x"}}]}):
        with pytest.raises(ToolArgError):
            validate_plan(bad)


# --------------------------------------------------------------- grounding
def test_grounding_helpers():
    context = {"top": [{"title": "DevOps Engineer", "score": 71.79}]}
    assert evidence_grounded(["DevOps Engineer"], context)
    assert not evidence_grounded(["Site Reliability"], context) and not evidence_grounded(["a"], context)
    assert not evidence_grounded([], context)
    assert numbers_grounded("It scores 71.79.", context) and not numbers_grounded("It scores 93.", context)


# ------------------------------------------------------------ the pipeline
def test_fixed_pipeline_produces_a_ranked_report_with_a_full_trace(db_factory):
    result = run_agent(make_ctx(db_factory))
    assert result["status"] == "success" and result["planner"] == {"mode": "fixed", "reason": "default goal needs no planning"}
    assert [t["tool"] for t in result["trace"]] == [s["tool"] for s in FIXED_PLAN]
    assert all(t["status"] == "ok" and t["ms"] >= 0 and t["summary"] for t in result["trace"])
    report = result["report"]
    scores = [m["score"] for m in report["matches"]]
    assert scores == sorted(scores, reverse=True) and len(report["matches"]) == 4
    assert report["matches"][0]["title"] in ("DevOps Engineer", "Cloud Engineer")
    assert report["matches"][-1]["title"] == "Marketing Lead"
    assert report["profile"]["domains"][0]["family"] == "DevOps, SRE and Cloud"
    assert {p["provider"] for p in report["data_sources"]["providers"]} == {"Test Feed", "Other Feed"}
    assert "never logs in to, or scrapes, LinkedIn or Indeed" in report["data_sources"]["policy"]
    assert report["narrative"]["mode"] == "template" and report["market"]["jobs_ranked"] == 4
    json.dumps(result)                                             # the whole run is serialisable


def test_a_goal_driven_plan_applies_validated_filters(db_factory):
    llm = FakeLLM(plan={"steps": [{"tool": "find_jobs", "args": {"remote_only": True, "limit": 5}}]})
    result = run_agent(make_ctx(db_factory, planner=llm), goal="remote jobs only please")
    assert result["planner"]["mode"] == "ollama" and llm.calls == 1
    assert [m["title"] for m in result["report"]["matches"]] == ["Cloud Engineer"]   # the only remote job
    find = next(t for t in result["trace"] if t["tool"] == "find_jobs")
    assert find["args"] == {"remote_only": True, "limit": 5}


def test_filters_experience_skill_and_company(db_factory):
    ctx = make_ctx(db_factory)
    ctx.cache["profile"] = agent_module.load_profile(ctx)
    find = TOOLS["find_jobs"].run
    assert {j["title"] for j in find(ctx, {"max_required_years": 3})["jobs"]} == {"DevOps Engineer", "Cloud Engineer"}
    assert {j["title"] for j in find(ctx, {"must_have_skill": "golang"})["jobs"]} == {"Cloud Engineer", "Staff SRE"}
    assert [j["company"] for j in find(ctx, {"company_contains": "gam"})["jobs"]] == ["Gamma"]
    assert find(ctx, {"min_score": 99})["jobs"] == []


def job_id_of(company):
    conn = database.get_db()
    row = conn.execute("SELECT id FROM jobs WHERE company = ?", (company,)).fetchone()
    conn.close()
    return row["id"]


def test_explain_job_uses_the_matchers_own_numbers(db_factory):
    beta = job_id_of("Beta")
    llm = FakeLLM(plan={"steps": [{"tool": "explain_job", "args": {"job_id": beta}}]})
    result = run_agent(make_ctx(db_factory, planner=llm), goal=f"why is job {beta} scored the way it is?")
    explained = result["report"]["explained_job"]
    assert explained["job_id"] == beta and explained["company"] == "Beta"
    assert explained["missing_skills"] == ["golang"] and explained["skill_lifts"][0]["skill"] == "golang"
    assert result["report"]["matches"] == []


def test_unknown_job_is_a_failed_step_not_a_failed_run(db_factory):
    llm = FakeLLM(plan={"steps": [{"tool": "explain_job", "args": {"job_id": 999}}, {"tool": "skill_gaps", "args": {}}]})
    result = run_agent(make_ctx(db_factory, planner=llm), goal="explain job 999 and my skill gaps")
    assert result["status"] == "partial"
    failed = [t for t in result["trace"] if t["status"] == "error"]
    assert failed[0]["tool"] == "explain_job" and "999" in failed[0]["error"]
    assert any("explain_job" in n for n in result["report"]["notes"])
    assert result["report"]["skill_levers"] is not None          # the other steps still ran


# ---------------------------------------------------------------- fallbacks
@pytest.mark.parametrize("llm,reason", [
    (FakeLLM(error=httpx.ConnectError("refused")), "unreachable"),
    (FakeLLM(error=httpx.ReadTimeout("slow")), "unreachable"),
    (FakeLLM(plan={"steps": [{"tool": "drop_tables", "args": {}}]}), "rejected"),
    (FakeLLM(plan={"steps": [{"tool": "find_jobs", "args": {"limit": "lots"}}]}), "rejected"),
    (FakeLLM(plan="not json at all"), "rejected"),
])
def test_a_bad_or_missing_model_falls_back_to_the_fixed_pipeline(db_factory, llm, reason):
    result = run_agent(make_ctx(db_factory, planner=llm), goal="remote jobs")
    assert result["planner"]["mode"] == "fixed" and reason in result["planner"]["reason"]
    assert [t["tool"] for t in result["trace"]] == [s["tool"] for s in FIXED_PLAN]
    assert len(result["report"]["matches"]) == 4


def test_an_unreachable_model_is_not_retried_during_the_cooldown(db_factory):
    llm = FakeLLM(error=httpx.ConnectError("refused"))
    ctx = make_ctx(db_factory, planner=llm, narrator=llm)
    run_agent(ctx, goal="remote jobs")
    run_agent(make_ctx(db_factory, planner=llm, narrator=llm), goal="remote jobs again")
    assert llm.calls == 1


def test_planner_fixed_never_calls_the_model(db_factory):
    llm = FakeLLM(plan={"steps": []})
    assert run_agent(make_ctx(db_factory, planner=llm, narrator=llm), goal="x", planner="fixed", narrative="off")["planner"]["reason"] == "requested"
    assert llm.calls == 0


# --------------------------------------------------------------- narrative
def test_a_grounded_narrative_is_used_and_an_invented_one_is_not(db_factory):
    top = run_agent(make_ctx(db_factory))["report"]["matches"][0]
    good = FakeLLM(explain={"answer": f"Your best match is {top['title']} at {top['company']}.",
                           "evidence": [top["title"]]})
    result = run_agent(make_ctx(db_factory, narrator=good))
    assert result["report"]["narrative"]["mode"] == "ollama"
    for bad in ({"answer": f"Best is {top['title']}.", "evidence": ["Senior Staff Wizard"]},
                {"answer": f"{top['title']} scores 99.9%.", "evidence": [top["title"]]},
                {"answer": "x", "evidence": []}):
        agent_module.reset_llm_cooldown()
        narrative = run_agent(make_ctx(db_factory, narrator=FakeLLM(explain=bad)))["report"]["narrative"]
        assert narrative["mode"] == "template" and narrative["evidence"] == []
    off = run_agent(make_ctx(db_factory, narrator=good), narrative="off")["report"]["narrative"]
    assert off["mode"] == "template"


def test_no_profile_and_no_jobs_are_handled(db_factory, tmp_path, monkeypatch):
    conn = database.get_db(); conn.execute("DELETE FROM jobs"); conn.commit(); conn.close()
    empty = run_agent(make_ctx(db_factory))
    assert empty["report"]["matches"] == [] and any("No jobs are stored" in n for n in empty["report"]["notes"])
    conn = database.get_db(); conn.execute("DELETE FROM resumes"); conn.commit(); conn.close()
    with pytest.raises(NoProfile):
        run_agent(make_ctx(db_factory))


def test_a_missing_explicit_resume_is_not_replaced_by_another(db_factory):
    with pytest.raises(NoProfile):
        run_agent(make_ctx(db_factory), resume_id=999)


# ---------------------------------------------------------------- the API
@pytest.fixture
def client(db_factory):
    with TestClient(main.app) as c:
        yield c


def test_the_endpoint_runs_the_pipeline_and_validates_input(client, db_factory):
    response = client.post("/api/v1/agent/run", data={"narrative": "off"})
    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] is None and body["status"] == "success" and len(body["trace"]) == 4
    assert client.post("/api/v1/agent/run", data={"planner": "magic"}).status_code == 400
    assert client.post("/api/v1/agent/run", data={"goal": "x" * 501}).status_code == 400
    assert client.post("/api/v1/agent/run", data={"resume_id": 999}).status_code == 404
    assert client.get("/api/v1/agent/runs").json() == {"runs": []}
    assert client.get("/api/v1/agent/runs/1").status_code == 404


def test_the_endpoint_requires_the_token_when_one_is_set(client, monkeypatch):
    import dataclasses
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, api_token="tok"))
    assert client.post("/api/v1/agent/run").status_code == 401
    assert client.post("/api/v1/agent/run", headers={"Authorization": "Bearer tok"}, data={"narrative": "off"}).status_code == 200
