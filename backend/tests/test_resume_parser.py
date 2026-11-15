from app.agents.parser import extract_experience_years


def test_resume_experience_uses_employment_date_spans_not_an_unrelated_year_claim():
    text = """Looking for a role requiring 10 years of experience.
    EXPERIENCE
    Software Engineer | Jan 2020 - Dec 2022
    Senior Engineer | Jan 2023 - Present
    EDUCATION
    BSc | 2014 - 2018
    """
    assert extract_experience_years(text) > 6.0
    assert extract_experience_years(text) < 7.0


def test_resume_experience_without_dates_or_an_explicit_claim_is_unknown():
    assert extract_experience_years("Python, SQL, and cloud infrastructure") == 0.0


def test_resume_experience_falls_back_to_an_explicit_total_claim():
    assert extract_experience_years("Professional experience: 5+ years of experience.") == 5.0
