from core.domain import JobPosting, ResumeProfile, ScoreConfig
from core.scoring import fit_score, rank


def test_core_scoring_reproduces_stage_one_example():
    resume = ResumeProfile(
        resume_id=1,
        raw_text="DevOps Engineer with AWS, Kubernetes, Docker, Terraform, Jenkins, Python",
        exp_years=2.5,
        skills=("kubernetes", "docker", "terraform", "aws", "jenkins", "python"),
    )
    job = JobPosting(
        source="test",
        external_id="job-1",
        company="Example",
        title="Senior Cloud DevOps Engineer",
        requirements={"kubernetes": 2.0, "terraform": 2.0, "aws": 1.5, "jenkins": 1.5, "golang": 1.0},
        metadata={"req_years": 3.0},
    )

    result = fit_score(resume, job, ScoreConfig(), semantic_similarity=0.842)

    assert abs(result.final_score * 100 - 71.80) <= 0.02
    assert result.missing_skills == ("golang",)


def test_rank_applies_open_and_experience_gates():
    resume = ResumeProfile(resume_id=1, raw_text="analyst", exp_years=2, skills=("python",))
    jobs = [
        JobPosting("test", "open", "Open Co", "Analyst", requirements={"python": 1}, metadata={"req_years": 2}),
        JobPosting("test", "closed", "Closed Co", "Analyst", status="closed", requirements={"python": 1}),
    ]

    ranked = rank(resume, jobs, ScoreConfig(), {"open": 0.8, "closed": 0.9})

    assert [job.external_id for job, _ in ranked] == ["open"]
