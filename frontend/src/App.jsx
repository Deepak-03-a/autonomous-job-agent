const { useEffect, useMemo, useRef, useState } = React;

const API_BASE_URL = window.location.protocol === "file:"
  ? "http://127.0.0.1:8000"
  : (window.location.port === "5173" ? "http://127.0.0.1:8000" : "");

const FALLBACK_JOBS = [{
  title: "Upload a resume to discover your matches",
  company: "TalentAlign AI",
  location: "India · Nationwide",
  source: "Personalized discovery",
  description: "Your resume signals will be matched against live openings from multiple job feeds.",
  weights: {},
}];

function apiUrl(path) { return `${API_BASE_URL}${path}`; }

async function requestJson(path, options) {
  const headers = new Headers(options?.headers || {});
  const response = await fetch(apiUrl(path), { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.detail || "The request could not be completed.");
  return payload;
}

function initials(name = "TalentAlign") {
  return name.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase();
}

function formatPostedAt(value) {
  if (!value) return "Recently posted";
  const timestamp = Number(value);
  if (!Number.isNaN(timestamp) && timestamp > 1000000000) {
    return new Intl.DateTimeFormat("en-IN", { day: "numeric", month: "short" }).format(new Date(timestamp * 1000));
  }
  return String(value).slice(0, 24);
}

function scoreFor(match) { return Number(match?.evaluation?.final_match_score_pct ?? match?.relevance_score ?? 0); }

function App() {
  const [resumeId, setResumeId] = useState(1);
  const [resume, setResume] = useState(null);
  const [country, setCountry] = useState("India");
  const [jobs, setJobs] = useState([]);
  const [selectedJob, setSelectedJob] = useState(null);
  const [filter, setFilter] = useState("All roles");
  const [sort, setSort] = useState("Best match");
  const [loading, setLoading] = useState(false);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [backendOffline, setBackendOffline] = useState(false);
  const [chatQuestion, setChatQuestion] = useState("");
  const [chatAnswer, setChatAnswer] = useState("");
  const [chatLoading, setChatLoading] = useState(false);

  useEffect(() => {
    fetch(apiUrl("/health"), { signal: AbortSignal.timeout(5000) })
      .then((response) => setBackendOffline(!response.ok))
      .catch(() => setBackendOffline(true));
  }, []);
  const fileRef = useRef(null);

  const visibleJobs = useMemo(() => {
    let result = jobs.length ? [...jobs] : FALLBACK_JOBS;
    if (filter === "Startups") result = result.filter((job) => job.company_details?.is_startup || job.is_startup);
    if (filter === "Remote") result = result.filter((job) => /remote|worldwide/i.test(job.company_details?.location || job.location || ""));
    if (filter === "Entry level") result = result.filter((job) => Number(job.company_details?.required_experience_years ?? job.req_years ?? 0) <= 2);
    if (sort === "Newest") result.reverse();
    if (sort === "Salary") result.sort((a, b) => String(a.company_details?.company_name || a.company || "").localeCompare(String(b.company_details?.company_name || b.company || "")));
    return result;
  }, [filter, jobs, sort]);

  const roleCount = jobs.length || 0;
  const activeJob = selectedJob || visibleJobs[0];
  const activeDetails = activeJob?.company_details || activeJob || {};
  const activeEvaluation = activeJob?.evaluation || {};
  const activeAdvice = activeJob?.advisory || {};

  async function askAssistant(event) {
    event.preventDefault();
    if (!activeJob || !chatQuestion.trim()) return;
    setChatLoading(true);
    try {
      const result = await requestJson("/api/v1/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          question: chatQuestion.trim(),
          resume_id: resumeId,
          job_id: activeJob.job_id || activeJob.id || activeDetails.id,
        }),
      });
      setChatAnswer(result.answer);
    } catch (requestError) { setChatAnswer(requestError.message); }
    finally { setChatLoading(false); }
  }

  async function uploadResume(file) {
    if (!file) return;
    setUploading(true); setError("");
    try {
      const body = new FormData(); body.append("file", file); body.append("user_id", "1");
      const data = await requestJson("/api/v1/parse-resume-pdf", { method: "POST", body });
      setResumeId(data.resume_id); setResume(data); setNotice(`${data.filename} is ready. Run discovery to see your matches.`);
    } catch (requestError) { setError(requestError.message); }
    finally { setUploading(false); }
  }

  async function discover() {
    setLoading(true); setError(""); setNotice("");
    try {
      const body = new FormData(); body.append("resume_id", String(resumeId || 1)); body.append("country", country || "India");
      const discovered = await requestJson("/api/v1/market/discover", { method: "POST", body });
      const discoveredJobs = discovered.jobs.map((job) => ({ ...job, company_details: job, relevance_score: job.relevance_score }));
      setJobs(discoveredJobs); setSelectedJob(discoveredJobs[0] || null);
      if (!discoveredJobs.length) {
        const failures = (discovered.source_errors || []).map((item) => `${item.source}: ${item.error}`).join(" · ");
        setNotice(failures
          ? `No openings found. Some job sources could not be reached: ${failures}`
          : `No openings found across ${country || "the selected country"}. Try again shortly or search another location.`);
        return;
      }
      setNotice(`${discoveredJobs.length} openings found across ${country || "the selected country"}${discovered.cache_hit ? " · served from cache" : ""}. Ranking matches in the background…`);
      setLoading(false);
      const rankBody = new FormData(); rankBody.append("resume_id", String(resumeId || 1));
      requestJson("/api/v1/rank-market", { method: "POST", body: rankBody }).then((ranked) => {
        if (ranked?.matches?.length) { setJobs(ranked.matches); setSelectedJob(ranked.matches[0]); setNotice(`${ranked.matches.length} openings ranked for your profile.`); }
      }).catch(() => {});
    } catch (requestError) { setError(requestError.message); }
    finally { setLoading(false); }
  }

  return (
    <div className="app-shell">
      <header className="topbar">
        <a className="brand" href="#top"><span className="brand-mark">T</span><span>talent<span>align</span></span></a>
        <nav className="nav-links"><a className="active" href="#discover">Discover jobs</a><a href="#insights">Career insights</a><a href="#applications">Applications</a></nav>
        <div className="topbar-actions"><button className="ghost-button" type="button" onClick={() => fileRef.current?.click()}>Upload resume</button><div className="avatar">RK</div></div>
      </header>
      {backendOffline && <div className="offline-banner">The Linux backend is offline or unreachable. Wake the host or check the Tailscale connection.</div>}

      <main id="top">
        <section className="hero-section">
          <div className="hero-copy"><div className="eyebrow"><span className="eyebrow-dot" /> Resume-aware job discovery</div><h1>Find work that fits your <em>next chapter.</em></h1><p>One search across the market, tuned to your skills, experience, and ambitions — from high-growth startups to the teams shaping what comes next.</p>
            <div className="hero-search"><div className="search-field"><span className="search-icon">⌕</span><input value={resume?.detected_skills?.slice(0, 3).join(", ") || "Role, skills, or company"} readOnly aria-label="Resume search signals" /></div><div className="location-field"><span className="pin-icon">⌖</span><input value={country} onChange={(event) => setCountry(event.target.value)} aria-label="Search country" /></div><button className="search-button" type="button" onClick={discover} disabled={loading}>{loading ? "Searching…" : "Search jobs"}</button></div>
            <div className="popular-searches"><span>Popular:</span><button onClick={() => setCountry("India")}>India-wide</button><button onClick={() => setFilter("Startups")}>Startups</button><button onClick={() => setFilter("Remote")}>Remote friendly</button></div>
          </div>
          <div className="hero-proof"><div className="proof-card"><strong>All your possibilities</strong><span>Enterprise, startup, and remote roles in one place.</span></div><div className="proof-stat"><strong>24/7</strong><span>market refresh</span></div></div>
        </section>

        <section className="content-wrap" id="discover">
          <div className="content-heading"><div><span className="section-kicker">Your opportunity feed</span><h2>{roleCount ? `${roleCount} roles worth a closer look` : "Your next opportunity starts here"}</h2></div><div className="view-toggle"><button className="selected">▦</button><button>☷</button></div></div>
          <div className="workspace-grid">
            <aside className="filters-panel"><div className="filter-title"><span>Refine your search</span><button onClick={() => setFilter("All roles")}>Reset</button></div><div className="resume-drop" onClick={() => fileRef.current?.click()}><div className="upload-icon">↥</div><strong>{uploading ? "Reading your resume…" : resume ? "Resume loaded" : "Add your resume"}</strong><span>{resume ? `${resume.detected_skills?.length || 0} signals detected` : "Get more relevant matches"}</span><input ref={fileRef} type="file" accept=".pdf" hidden onChange={(event) => uploadResume(event.target.files?.[0])} /></div><label className="filter-label" htmlFor="country-filter">Location</label><div className="filter-input"><span>⌖</span><input id="country-filter" value={country} onChange={(event) => setCountry(event.target.value)} /></div><label className="filter-label">Work style</label><div className="filter-options">{["All roles", "Remote", "Startups", "Entry level"].map((option) => <button key={option} className={filter === option ? "filter-option active" : "filter-option"} onClick={() => setFilter(option)}><span className="checkmark">{filter === option ? "✓" : ""}</span>{option}<span className="option-count">{option === "All roles" ? roleCount : ""}</span></button>)}</div><label className="filter-label">Experience</label><div className="experience-bar"><span /><i /><b /></div><div className="experience-labels"><span>Any level</span><span>10+ years</span></div><div className="filter-note"><span>✦</span><p>Your feed improves as you add projects and skills to your resume.</p></div></aside>

            <section className="results-panel"><div className="results-toolbar"><div className="result-count">{roleCount ? <><strong>{roleCount}</strong> openings matched to your profile</> : "Upload your resume, then search the market"}</div><label className="sort-control">Sort by <select value={sort} onChange={(event) => setSort(event.target.value)}><option>Best match</option><option>Newest</option><option>Salary</option></select></label></div>{(error || notice) && <div className={error ? "status-message error" : "status-message"}>{error || notice}</div>}<div className="job-list">{visibleJobs.map((job, index) => { const details = job.company_details || job; const score = scoreFor(job); const hasMatchScore = job.evaluation?.final_match_score_pct != null; const relevance = details.relevance_score ?? job.relevance_score; return <article className={activeJob === job ? "job-card selected" : "job-card"} key={`${details.company_name || details.company}-${details.role_title || details.title}-${index}`} onClick={() => setSelectedJob(job)}><div className="company-logo" style={{ background: `hsl(${(index * 43 + 195) % 360} 55% 30%)` }}>{initials(details.company_name || details.company)}</div><div className="job-main"><div className="job-card-top"><span className="company-name">{details.company_name || details.company}</span>{details.is_startup && <span className="startup-pill">Startup</span>}<span className="posted-date">{formatPostedAt(details.posted_at)}</span></div><h3>{details.role_title || details.title}</h3><div className="job-meta"><span>⌖ {details.location || "India"}</span><span>◷ {details.required_experience_years ? `${details.required_experience_years}+ yrs` : "Flexible experience"}</span></div><div className="tag-row">{Object.keys(job.job_weights || job.weights || {}).filter((tag) => tag !== "general").slice(0, 4).map((tag) => <span key={tag}>{tag}</span>)}</div></div><div className="job-score"><strong>{hasMatchScore ? `${score.toFixed(0)}%` : relevance ? `${relevance}` : "New"}</strong><span>{hasMatchScore ? "fit" : relevance ? "signals" : "match"}</span></div></article>; })}</div></section>

            <aside className="detail-panel" id="insights"><div className="detail-label">Selected opportunity</div><div className="detail-company"><div className="company-logo large">{initials(activeDetails.company_name || activeDetails.company)}</div><div><strong>{activeDetails.company_name || activeDetails.company || "Your next role"}</strong><span>{activeDetails.source || "Personalized feed"}</span></div></div><h2>{activeDetails.role_title || activeDetails.title || "Discover your next role"}</h2><div className="detail-location">⌖ {activeDetails.location || "India · Nationwide"}</div><div className="fit-card"><div className="fit-ring"><span>{activeEvaluation.final_match_score_pct ? Math.round(activeEvaluation.final_match_score_pct) : "—"}</span><small>%</small></div><div><strong>{activeEvaluation.match_tier || "Awaiting your resume"}</strong><p>{activeEvaluation.score_story || "Search the market to see why a role fits you."}</p></div></div><div className="detail-section"><div className="detail-section-title">Why this role</div><p>{activeDetails.description || "Your personalized role summary will appear here after discovery."}</p></div><div className="detail-section"><div className="detail-section-title">Your edge</div><div className="skill-list">{(activeAdvice.matched_skills || Object.keys(activeDetails.weights || {}).slice(0, 4)).slice(0, 4).map((skill) => <span key={skill}>✓ {skill}</span>)}</div></div><a className="apply-button" href={activeDetails.careers_url || activeDetails.company_website || activeDetails.portal_url || "#"} target="_blank" rel="noreferrer">Visit official company careers <span>↗</span></a><div className="safe-note">↗ You’ll apply on the original employer careers page.</div><div className="detail-section chat-section"><div className="detail-section-title">Ask about this match</div><form onSubmit={askAssistant}><input value={chatQuestion} onChange={(event) => setChatQuestion(event.target.value)} maxLength={1000} placeholder="Why does this role fit me?" aria-label="Ask about this job match" /><button type="submit" disabled={chatLoading || !activeJob}>{chatLoading ? "Thinking…" : "Ask"}</button></form>{chatAnswer && <p className="chat-answer" aria-live="polite">{chatAnswer}</p>}<div className="chat-caption">Answers use your saved resume and job details.</div></div></aside>
          </div>
        </section>
      </main>
      <footer className="footer"><span>talent<span>align</span></span><span>Built for better next chapters.</span><span>© 2026 TalentAlign AI</span></footer>
    </div>
  );
}

ReactDOM.createRoot(document.getElementById("root")).render(<App />);
