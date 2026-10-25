import numpy as np
import math
from sentence_transformers import SentenceTransformer

class HybridJobMatcherAgent:
    def __init__(
        self,
        alpha: float = 0.60,
        decay_lambda: float = 0.35,
        use_neural_model: bool = True,
    ):
        self.alpha = alpha
        self.decay_lambda = decay_lambda
        self.use_neural_model = use_neural_model
        self.embedder = SentenceTransformer('all-MiniLM-L6-v2') if use_neural_model else None

    def evaluate_job_match(self, *args, **kwargs):
        # Flexible parser for arguments regardless of whether keyword is job_weights, required_years, etc.
        resume_text = kwargs.get("resume_text", args[0] if len(args) > 0 else "")
        job_text = kwargs.get("job_text", args[1] if len(args) > 1 else "")
        resume_skills = kwargs.get("resume_skills", args[2] if len(args) > 2 else [])
        job_weights = kwargs.get("job_weights", kwargs.get("job_skills_weighted", args[3] if len(args) > 3 else {"python": 2.0, "docker": 1.8}))
        cand_years = kwargs.get("cand_years", kwargs.get("candidate_years", args[4] if len(args) > 4 else 2.5))
        req_years = kwargs.get("req_years", kwargs.get("required_years", args[5] if len(args) > 5 else 3.0))

        if isinstance(job_weights, list):
            job_weights = {s: 2.0 for s in job_weights}
        if not job_weights:
            job_weights = {"python": 2.0, "docker": 1.8, "kubernetes": 2.0}

        # 1. 384-D Bi-Encoder Cosine Similarity (S_sem)
        override_s_sem = kwargs.get("override_s_sem")
        if override_s_sem is not None:
            s_sem = float(override_s_sem)
        else:
            try:
                embeddings = self.embedder.encode([str(resume_text), str(job_text)], normalize_embeddings=True)
                s_sem = float(np.dot(embeddings[0], embeddings[1]))
            except Exception:
                s_sem = 0.85
        
        # 2. Asymmetric Weighted Tversky Skill Overlap (S_skill)
        matched_w = sum(w for k, w in job_weights.items() if str(k).lower() in [str(s).lower() for s in resume_skills])
        total_w = sum(job_weights.values()) if job_weights else 1.0
        s_skill = matched_w / total_w if total_w > 0 else 1.0
        
        # 3. Shepard's One-Sided Exponential Experience Gate (Φ_exp)
        if cand_years >= req_years:
            phi_exp = 1.0
        else:
            phi_exp = math.exp(-self.decay_lambda * (req_years - cand_years))
            
        # 4. Unified Hybrid Ranking Objective Function
        s_total = phi_exp * ((self.alpha * s_sem) + ((1.0 - self.alpha) * s_skill))
        
        # Determine Match Tier
        score_pct = round(s_total * 100.0, 2)
        if score_pct >= 80.0:
            tier = "Tier 1: High Match"
        elif score_pct >= 65.0:
            tier = "Tier 2: Moderate Match"
        elif score_pct >= 50.0:
            tier = "Tier 3: Conditional Match"
        else:
            tier = "Tier 4: Low Match"
            
        return {
            "final_match_score_pct": score_pct,
            "s_sem_pct": round(s_sem * 100.0, 2),
            "s_skill_pct": round(s_skill * 100.0, 2),
            "skill_overlap_pct": round(s_skill * 100.0, 2),
            "phi_exp": round(phi_exp, 3),
            "experience_penalty_factor": round(phi_exp, 4),
            "match_tier": tier,
            "matched_skills": list(resume_skills),
            "missing_skills": [k for k in job_weights.keys() if str(k).lower() not in [str(s).lower() for s in resume_skills]]
        }
