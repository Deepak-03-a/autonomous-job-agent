import fitz  # PyMuPDF
import re
import json

SKILL_TAXONOMY_WEIGHTS = {
    "python": 2.0, "docker": 1.8, "kubernetes": 2.0, "terraform": 1.5,
    "fastapi": 1.2, "aws": 1.8, "azure": 1.5, "linux": 1.5, "ci/cd": 1.8,
    "git": 1.2, "jenkins": 1.5, "ansible": 1.5, "prometheus": 1.2, "grafana": 1.2,
    "react": 1.8, "node.js": 1.8, "sql": 1.5, "postgresql": 1.5, "java": 1.8,
    "spring": 1.5, "c++": 1.8, "javascript": 1.5, "typescript": 1.5, "html": 1.0,
    "css": 1.0, "machine learning": 2.0, "data analysis": 1.8, "pandas": 1.5,
    "numpy": 1.5, "django": 1.5, "flask": 1.2, "rest apis": 1.5,
    "go": 1.8, "golang": 1.8, "rust": 1.8, "c#": 1.8, ".net": 1.8,
    "php": 1.5, "ruby": 1.5, "kotlin": 1.5, "swift": 1.5, "android": 1.8,
    "ios": 1.8, "flutter": 1.8, "react native": 1.8, "vue": 1.8, "angular": 1.8,
    "next.js": 1.8, "express": 1.5, "graphql": 1.5, "mongodb": 1.5,
    "redis": 1.5, "mysql": 1.5, "oracle": 1.5, "snowflake": 1.8,
    "spark": 1.8, "airflow": 1.8, "tensorflow": 1.8, "pytorch": 1.8,
    "scikit-learn": 1.8, "nlp": 1.8, "computer vision": 1.8, "excel": 1.2,
    "tableau": 1.5, "power bi": 1.5, "salesforce": 1.5, "sap": 1.5,
    "workday": 1.5, "jira": 1.0, "figma": 1.2, "ui/ux": 1.5,
    "product management": 1.5, "project management": 1.5, "agile": 1.0,
    "scrum": 1.0, "seo": 1.2, "content marketing": 1.2, "accounting": 1.2
}

# Broader role coverage for LinkedIn skill names and job descriptions. Unknown
# LinkedIn skills remain visible as extras, and these common terms now also take
# part in deterministic scoring when the posting names them.
SKILL_TAXONOMY_WEIGHTS.update({
    "gcp": 1.8, "google cloud": 1.8, "cloud computing": 1.4, "cloud security": 1.6,
    "azure devops": 1.6, "aws lambda": 1.6, "amazon web services": 1.8,
    "helm": 1.5, "istio": 1.5, "openshift": 1.6, "docker compose": 1.2,
    "github actions": 1.4, "gitlab ci": 1.4, "circleci": 1.3, "argo cd": 1.4,
    "infrastructure as code": 1.4, "site reliability engineering": 1.6,
    "observability": 1.4, "datadog": 1.3, "new relic": 1.3, "opentelemetry": 1.4,
    "vault": 1.2, "consul": 1.2, "bash": 1.2, "powershell": 1.2,
    "data engineering": 1.8, "data science": 1.8, "business intelligence": 1.6,
    "statistics": 1.4, "r programming": 1.4, "matplotlib": 1.2, "seaborn": 1.2,
    "jupyter": 1.1, "dbt": 1.6, "databricks": 1.8, "bigquery": 1.6,
    "redshift": 1.5, "amazon redshift": 1.5, "google analytics": 1.3,
    "kafka": 1.6, "apache kafka": 1.6, "hadoop": 1.5, "apache spark": 1.8,
    "hive": 1.3, "flink": 1.5, "elasticsearch": 1.4, "opensearch": 1.3,
    "dynamodb": 1.4, "cassandra": 1.5, "neo4j": 1.3, "sql server": 1.4,
    "microsoft sql server": 1.4, "sqlite": 1.0, "data modeling": 1.4,
    "etl": 1.4, "elt": 1.4, "data warehousing": 1.5, "data visualization": 1.4,
    "deep learning": 1.8, "generative ai": 1.8, "large language models": 1.8,
    "llm": 1.8, "prompt engineering": 1.3, "hugging face": 1.4, "mlflow": 1.4,
    "xgboost": 1.5, "lightgbm": 1.5, "keras": 1.5, "opencv": 1.4,
    "natural language processing": 1.7, "reinforcement learning": 1.5,
    "computer vision": 1.8, "robotics": 1.6, "matlab": 1.3,
    "api design": 1.3, "microservices": 1.5, "software architecture": 1.5,
    "software development": 1.4, "object oriented programming": 1.3,
    "test automation": 1.4, "unit testing": 1.2, "selenium": 1.3,
    "cypress": 1.3, "pytest": 1.3, "playwright": 1.3, "quality assurance": 1.2,
    "web development": 1.3, "web applications": 1.2, "accessibility": 1.1,
    "wordpress": 1.2, "shopify": 1.2, "webflow": 1.1, "php laravel": 1.4,
    "spring boot": 1.5, "asp.net": 1.5, "fastify": 1.3, "svelte": 1.4,
    "tailwind css": 1.2, "bootstrap": 1.1, "webpack": 1.2, "vite": 1.2,
    "figma": 1.2, "user research": 1.2, "interaction design": 1.2,
    "service design": 1.2, "design systems": 1.3, "prototyping": 1.1,
    "product strategy": 1.4, "roadmapping": 1.2, "product analytics": 1.4,
    "user stories": 1.1, "stakeholder management": 1.2, "business analysis": 1.4,
    "requirements analysis": 1.2, "change management": 1.2, "program management": 1.3,
    "team leadership": 1.2, "people management": 1.2, "technical leadership": 1.3,
    "communication": 1.0, "problem solving": 1.0, "customer service": 1.1,
    "customer success": 1.2, "account management": 1.2, "salesforce administration": 1.4,
    "hubspot": 1.2, "marketing strategy": 1.3, "digital marketing": 1.3,
    "social media marketing": 1.2, "email marketing": 1.2, "market research": 1.2,
    "lead generation": 1.2, "copywriting": 1.2, "brand management": 1.2,
    "financial analysis": 1.3, "financial modeling": 1.4, "budgeting": 1.1,
    "bookkeeping": 1.1, "tax preparation": 1.1, "quickbooks": 1.1,
    "human resources": 1.2, "talent acquisition": 1.3, "recruiting": 1.2,
    "workday hcm": 1.4, "payroll": 1.1, "employee relations": 1.1,
    "supply chain": 1.3, "logistics": 1.2, "procurement": 1.2,
    "project planning": 1.2, "microsoft project": 1.1, "asana": 1.1,
    "confluence": 1.0, "notion": 1.0, "kanban": 1.0, "lean": 1.1,
    "six sigma": 1.2, "risk management": 1.2, "compliance": 1.2,
    "cybersecurity": 1.6, "network security": 1.5, "penetration testing": 1.5,
    "incident response": 1.4, "identity and access management": 1.4,
    "iam": 1.3, "soc": 1.2, "siem": 1.3, "splunk": 1.3,
    "ethical hacking": 1.4, "owasp": 1.2, "zero trust": 1.3,
    "healthcare": 1.1, "nursing": 1.2, "clinical research": 1.3,
    "medical coding": 1.2, "pharmacology": 1.2, "teaching": 1.1,
    "curriculum development": 1.1, "technical writing": 1.2, "content writing": 1.1,
})


def extract_skills_from_text(text: str) -> list[str]:
    """Extract known skills without injecting a DevOps profile for unknown resumes."""
    text_lower = text.lower()
    return [
        skill for skill in SKILL_TAXONOMY_WEIGHTS
        if re.search(r"(?<![\w-])" + re.escape(skill) + r"(?![\w-])", text_lower)
    ]

class ResumeParserAgent:
    """Dynamic resume parser agent that extracts text, experience tenure, and skills from ANY uploaded PDF resume."""
    def parse_pdf_bytes(self, pdf_bytes: bytes) -> dict:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        full_text = ""
        for page in doc:
            full_text += page.get_text()
            
        found_skills = extract_skills_from_text(full_text)

        exp_years = extract_experience_years(full_text)
        
        return {
            "raw_text": full_text,
            "exp_years": exp_years,
            "skills": found_skills
        }


class ResumeAndJobParserAgent(ResumeParserAgent):
    def extract_text_from_pdf_bytes(self, pdf_bytes: bytes) -> str:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        full_text = ""
        for page in doc:
            full_text += page.get_text()
        return full_text

    def extract_skills_from_text(self, text: str) -> set:
        return set(extract_skills_from_text(text))

    def extract_years_of_experience(self, text: str, default_years: float = 0.0) -> float:
        years = extract_experience_years(text)
        return years if years > 0 else default_years


def extract_experience_years(text: str) -> float:
    """Estimate experience from employment date spans, then explicit total claims."""
    lines = (text or "").splitlines()
    start = next((i for i, line in enumerate(lines)
                  if re.search(r"\b(work\s+experience|professional\s+experience|employment\s+history|experience)\b", line, re.I)), None)
    if start is not None:
        end = next((i for i in range(start + 1, len(lines))
                    if re.search(r"^\s*(education|certifications?|skills|projects|publications)\b", lines[i], re.I)), len(lines))
        section = "\n".join(lines[start:end])
        month = r"(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)"
        dates = rf"(?:{month}\.?\s+\d{{4}}|\d{{1,2}}/\d{{4}}|\d{{4}})"
        ranges = re.findall(rf"({dates})\s*(?:-|–|—|to)\s*(Present|Current|Now|{dates})", section, re.I)
        spans = []
        for left, right in ranges:
            a, b = _resume_month(left), _resume_month(right)
            if a is not None:
                spans.append((a, b or _today_month()))
        if spans:
            spans.sort()
            months, current_end = 0, None
            for first, last in spans:
                last = max(first, last)
                if current_end is None or first > current_end:
                    months += last - first
                    current_end = last
                elif last > current_end:
                    months += last - current_end
                    current_end = last
            if months:
                return round(months / 12, 1)

    claims = re.findall(
        r"\b(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?(?:professional\s+|total\s+)?experience\b",
        text or "", re.I,
    )
    return float(claims[0]) if claims else 0.0


def _resume_month(value: str) -> int | None:
    value = value.strip().casefold().replace(".", "")
    month_match = re.fullmatch(r"([a-z]+)\s+(\d{4})", value)
    numeric_match = re.fullmatch(r"(\d{1,2})/(\d{4})", value)
    year_match = re.fullmatch(r"\d{4}", value)
    if year_match:
        return int(value) * 12
    if numeric_match:
        month, year = int(numeric_match.group(1)), int(numeric_match.group(2))
        return year * 12 + month if 1 <= month <= 12 else None
    if month_match:
        names = {name: idx for idx, name in enumerate(
            ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"), 1)}
        token = month_match.group(1)[:3]
        return int(month_match.group(2)) * 12 + names[token] if token in names else None
    return None


def _today_month() -> int:
    from datetime import date
    now = date.today()
    return now.year * 12 + now.month
