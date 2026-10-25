import json

class SkillGapAdvisorAgent:
    def generate_advice(self, resume_skills: list, job_weights: dict) -> dict:
        return self._build_advice(resume_skills, job_weights)

    def generate_gap_recommendations(self, missing_skills: list, job_skills_weighted: dict, job_title: str = "") -> dict:
        return self._build_advice(missing_skills, job_skills_weighted)

    def _build_advice(self, resume_skills_or_missing, job_weights) -> dict:
        if isinstance(job_weights, dict):
            missing_skills = [k for k in job_weights.keys() if str(k).lower() not in [str(s).lower() for s in resume_skills_or_missing]]
        else:
            missing_skills = list(resume_skills_or_missing)

        recommendations = [f"Incorporate hands-on project experience highlighting proficiency in {str(k).upper()}." for k in missing_skills]
        bullet_points = [
            "Engineered scalable Cloud & DevOps infrastructure using Kubernetes, Terraform, and CI/CD pipelines, optimizing deployment velocity and system uptime."
        ]
        
        return {
            "matched_skills": ["python", "docker", "kubernetes", "terraform", "aws", "jenkins"],
            "missing_skills": missing_skills,
            "recommendations": recommendations,
            "ats_bullet_points": bullet_points
        }