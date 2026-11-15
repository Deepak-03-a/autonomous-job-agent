import io
import json
import zipfile
from datetime import date

import pytest
from fastapi.testclient import TestClient

import app.main as main
from adapters.imports.linkedin_zip import ExportError, MAX_MEMBER_BYTES, read_export
from app import database
from core.domains import RoleSpan, predict_domains, target_roles
from core.linkedin import combine, parse_month, parse_tables

TODAY = date(2026, 11, 14)

PROFILE = "First Name,Last Name,Headline,Summary,Industry\nRam,K,DevOps Engineer | AWS | Kubernetes,Builds cloud platforms.,IT\n"
POSITIONS = ("Company Name,Title,Description,Location,Started On,Finished On\n"
             "Acme,DevOps Engineer,\"Runs Kubernetes and Terraform on AWS with Jenkins CI/CD.\",Pune,Jun 2022,\n"
             "Beta,Intern,Learned Linux and Git.,Pune,Jan 2022,Aug 2022\n")
SKILLS = "Name\nKubernetes\nTerraform\nCloud Cost Optimization\nkubernetes\nTeam Leadership\n"
APPLICATIONS = ("Application Date,Contact Email,Contact Phone Number,Company Name,Job Title,Job Url,Resume Name,Question And Answers\n"
                "2026-10-01,secret@example.com,+91 9999999999,Gamma,Site Reliability Engineer,https://x.test/1,cv.pdf,Salary: 40 LPA\n")
SAVED = "Saved Date,Job Url,Job Title,Company Name\n2026-10-05,https://x.test/2,Platform Engineer,Delta\n"
ALERTS = "Search Text,Location\nDevOps Engineer,India\nCloud Engineer,Bengaluru\n"
MESSAGES = "From,Content\nFriend,CANARY_PRIVATE_MESSAGE_TEXT\n"


def make_zip(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


FULL = {"Profile.csv": PROFILE, "Positions.csv": POSITIONS, "Skills.csv": SKILLS,
        "Jobs/Job Applications.csv": APPLICATIONS, "Jobs/Saved Jobs.csv": SAVED,
        "Jobs/Saved Job Alerts.csv": ALERTS, "messages.csv": MESSAGES, "Connections.csv": "Notes:\nx\n\nFirst Name\nA\n"}


# ------------------------------------------------------------------ reading
def test_only_allowlisted_files_are_ever_opened():
    tables, warnings, files_read = read_export(make_zip(FULL))
    assert sorted(files_read) == sorted(["Profile.csv", "Positions.csv", "Skills.csv", "Jobs/Job Applications.csv",
                                         "Jobs/Saved Jobs.csv", "Jobs/Saved Job Alerts.csv"])
    assert "CANARY_PRIVATE_MESSAGE_TEXT" not in json.dumps(tables)
    assert warnings == []


def test_missing_core_files_warn_and_non_zip_is_rejected():
    _, warnings, _ = read_export(make_zip({"Skills.csv": SKILLS}))
    assert any("Positions.csv" in w for w in warnings) and any("Profile.csv" in w for w in warnings)
    with pytest.raises(ExportError):
        read_export(b"this is not a zip")


def test_oversized_members_are_skipped_not_read():
    big = "Name\n" + "x" * (MAX_MEMBER_BYTES + 10)
    tables, warnings, files_read = read_export(make_zip({"Skills.csv": big, "Positions.csv": POSITIONS}))
    assert "skills" not in tables and "positions" in tables
    assert any("larger than" in w for w in warnings)


def test_preamble_lines_bom_and_header_case_are_tolerated():
    noisy = "\ufeffNotes:\nsome explanatory text\n\nCOMPANY NAME,title,Started On\nAcme,SRE,2021\n"
    tables, _, _ = read_export(make_zip({"positions.CSV": noisy}))
    profile = parse_tables(tables)
    assert profile.positions[0].title == "SRE" and profile.positions[0].start == date(2021, 1, 1)


# ------------------------------------------------------------------ parsing
@pytest.mark.parametrize("text,expected", [("Jan 2022", date(2022, 1, 1)), ("September 2021", date(2021, 9, 1)),
                                           ("2020", date(2020, 1, 1)), ("03/2023", date(2023, 3, 1)),
                                           ("2023-07-15", date(2023, 7, 1)), ("", None), ("soon", None), ("13/2023", None)])
def test_month_parsing(text, expected):
    assert parse_month(text) == expected


def test_overlapping_roles_are_counted_once_and_current_roles_run_to_today():
    profile = parse_tables(read_export(make_zip(FULL))[0])
    assert profile.experience_years(TODAY) == 4.8     # Jan 2022 to Nov 2026, the overlap not doubled


def test_private_application_fields_never_reach_the_profile():
    profile = parse_tables(read_export(make_zip(FULL))[0])
    blob = json.dumps(profile.applications)
    assert "secret@example.com" not in blob and "9999999999" not in blob and "40 LPA" not in blob
    assert profile.applications == [{"title": "Site Reliability Engineer", "company": "Gamma",
                                     "url": "https://x.test/1", "date": "2026-10-01"}]
    assert profile.saved_jobs[0]["company"] == "Delta" and profile.alert_phrases == ["DevOps Engineer", "Cloud Engineer"]


def test_combine_unions_skills_keeps_non_taxonomy_ones_separate_and_prefers_dated_years():
    profile = parse_tables(read_export(make_zip(FULL))[0])
    merged = combine("Resume text mentions python.", ["python"], 2.0, profile, TODAY)
    assert {"python", "kubernetes", "terraform", "aws", "jenkins", "linux", "git"} <= set(merged.skills)
    assert merged.skills.count("kubernetes") == 1
    assert merged.extra_skills == ["Cloud Cost Optimization", "Team Leadership"]
    assert (merged.experience_years, merged.experience_source) == (4.8, "linkedin positions")
    assert "LinkedIn profile" in merged.raw_text and merged.raw_text.startswith("Resume text")


def test_without_dated_positions_the_resume_years_are_used():
    profile = parse_tables({"skills": [{"Name": "Docker"}]})
    merged = combine("", [], 3.0, profile, TODAY)
    assert (merged.experience_years, merged.experience_source) == (3.0, "resume text")


# ------------------------------------------------------------------ domains
def test_devops_profile_ranks_devops_first_with_evidence():
    skills = ["python", "docker", "kubernetes", "terraform", "aws", "jenkins", "git", "linux", "ci/cd"]
    top = predict_domains(skills, [RoleSpan("DevOps Engineer", 30), RoleSpan("Intern", 6)], "DevOps Engineer | AWS")[0]
    assert top.family == "DevOps, SRE and Cloud" and top.score > 0.8
    assert "kubernetes" in top.matched_skills and top.matched_titles == ("DevOps Engineer",)
    assert "devops" in target_roles([top])


def test_titles_move_the_ranking_when_skills_are_ambiguous():
    skills = ["python", "sql"]
    as_analyst = predict_domains(skills, [RoleSpan("Data Analyst", 36)])[0].family
    as_backend = predict_domains(skills, [RoleSpan("Backend Developer", 36)])[0].family
    assert as_analyst == "Data Analytics and BI" and as_backend == "Backend Engineering"


def test_other_fields_and_empty_profiles():
    assert predict_domains(["seo", "content marketing"], [RoleSpan("Marketing Executive", 24)])[0].family == "Marketing and Content"
    assert predict_domains([], [], "") == []
    assert predict_domains(["excel"]) == []     # one weak signal is not a prediction


# ------------------------------------------------------------------- the API
@pytest.fixture
def client(tmp_path, monkeypatch):
    import dataclasses
    monkeypatch.setattr(database, "DB_PATH", str(tmp_path / "t.db"))
    test_token = "test-token-for-local-api-tests-0123456789"
    monkeypatch.setattr(main, "settings", dataclasses.replace(main.settings, api_token=test_token, environment="test"))
    with TestClient(main.app, headers={"Authorization": f"Bearer {test_token}"}) as c:
        yield c


def upload(client, data, name="export.zip", **form):
    return client.post("/api/v1/profile/linkedin-export", files={"file": (name, data)}, data=form)


def test_import_creates_a_combined_profile_and_returns_the_analysis(client):
    response = upload(client, make_zip(FULL))
    assert response.status_code == 200
    body = response.json()
    assert body["experience"] == {"years": 4.8, "source": "linkedin positions"}
    assert body["predicted_domains"][0]["family"] == "DevOps, SRE and Cloud"
    assert body["target_roles"][:2] == ["devops engineer", "cloud engineer"]    # your own alert phrases come first
    assert body["extra_skills"] == ["Cloud Cost Optimization", "Team Leadership"]
    assert (body["saved_jobs"], body["applications"]) == (1, 1)
    assert "CANARY_PRIVATE_MESSAGE_TEXT" not in response.text and "secret@example.com" not in response.text
    db = database.get_db()
    row = db.execute("SELECT * FROM resumes WHERE id = ?", (body["resume_id"],)).fetchone()
    db.close()
    assert "kubernetes" in json.loads(row["skills_json"]) and row["exp_years"] == 4.8


def test_import_rejects_bad_uploads(client, monkeypatch):
    assert upload(client, b"x", name="export.pdf").status_code == 400
    assert upload(client, b"not a zip").status_code == 400
    assert upload(client, make_zip({"Connections.csv": "a,b\n1,2\n"})).status_code == 400   # nothing usable inside
    assert upload(client, make_zip(FULL), resume_id="999").status_code == 404
    monkeypatch.setattr(main, "MAX_EXPORT_BYTES", 10)
    assert upload(client, make_zip(FULL)).status_code == 413


def test_pdf_upload_now_reports_predicted_domains_and_target_roles(client):
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 72), "DevOps engineer. 3 years of experience with Docker, Kubernetes, Terraform, AWS, Jenkins and Linux.")
    response = client.post("/api/v1/parse-resume-pdf", files={"file": ("cv.pdf", doc.tobytes())})
    assert response.status_code == 200
    body = response.json()
    assert body["predicted_domains"][0]["family"] == "DevOps, SRE and Cloud"
    assert "devops" in body["target_roles"]
