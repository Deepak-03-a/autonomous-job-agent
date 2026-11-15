// Job-search agent panel. Loaded before App.jsx and wrapped in a function so its
// helper names cannot collide with App.jsx's globals; it exposes window.AgentPanel.
(function () {
  const { useEffect, useRef, useState } = React;

  // Only ever link to http(s) pages: job data comes from the internet.
  function safeHref(url) {
    try {
      const parsed = new URL(url);
      return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : "";
    } catch (error) {
      return "";
    }
  }

  const QUEUE_CLASS = {
    "Apply now": "apply",
    "One skill away": "close",
    "Stretch": "stretch",
    "Build toward": "build",
  };

  function pct(value) {
    const number = Number(value);
    return Number.isFinite(number) ? `${number.toFixed(1)}%` : "—";
  }

  function when(value) {
    if (!value) return "";
    const date = new Date(value);
    return Number.isNaN(date.getTime()) ? String(value).slice(0, 16) : date.toLocaleString();
  }

  function fromSavedRun(row) {
    return {
      run_id: row.id,
      status: row.status,
      goal: row.goal,
      planner: { mode: row.planner, reason: "saved run" },
      trace: row.trace || [],
      report: row.report || {},
    };
  }

  function Chip({ children, tone }) {
    return <span className={`agent-chip ${tone || ""}`}>{children}</span>;
  }

  function MatchCard({ match, open, onToggle }) {
    const href = safeHref(match.apply_url);
    const queueClass = QUEUE_CLASS[match.queue] || "build";
    return (
      <article className={`agent-match ${open ? "open" : ""}`}>
        <button type="button" className="agent-match-head" onClick={onToggle} aria-expanded={open}>
          <span className="agent-score">{Math.round(match.score)}<small>%</small></span>
          <span className="agent-match-main">
            <strong>{match.title}</strong>
            <span>{match.company}{match.location ? ` · ${match.location}` : ""}</span>
          </span>
          <span className={`agent-queue ${queueClass}`}>{match.queue || "Build toward"}</span>
        </button>
        {open && (
          <div className="agent-match-body">
            <p className="agent-muted">{match.tier}{match.experience_fit ? ` · experience: ${match.experience_fit}` : ""}
              {match.required_years ? ` · asks for ${match.required_years}+ years` : ""}</p>
            <div className="agent-skill-grid">
              <div>
                <div className="agent-label">You have</div>
                <div className="agent-chips">
                  {match.matched_skills.length
                    ? match.matched_skills.map((skill) => <Chip key={skill} tone="good">✓ {skill}</Chip>)
                    : <span className="agent-muted">No listed skills matched yet</span>}
                </div>
              </div>
              <div>
                <div className="agent-label">To learn</div>
                <div className="agent-chips">
                  {match.missing_skills.length
                    ? match.missing_skills.map((skill) => (
                        <Chip key={skill} tone="warn">
                          {skill}
                          {match.best_next_skill && match.best_next_skill.skill === skill
                            ? ` · +${match.best_next_skill.lift_pct} pts` : ""}
                        </Chip>))
                    : <span className="agent-muted">Nothing missing</span>}
                </div>
              </div>
            </div>
            {href
              ? <a className="agent-apply" href={href} target="_blank" rel="noreferrer noopener">Open the official page ↗</a>
              : <span className="agent-muted">No safe apply link was stored for this job.</span>}
            <span className="agent-source">via {match.source || "unknown source"}</span>
          </div>
        )}
      </article>
    );
  }

  function Trace({ plan, trace }) {
    return (
      <details className="agent-card agent-trace">
        <summary>How this was produced · {trace.length} steps</summary>
        <table>
          <thead><tr><th>#</th><th>Tool</th><th>Arguments</th><th>Result</th><th>ms</th></tr></thead>
          <tbody>
            {trace.map((step) => (
              <tr key={step.step} className={step.status === "error" ? "failed" : ""}>
                <td>{step.step}</td>
                <td>{step.tool}</td>
                <td><code>{JSON.stringify(step.args)}</code></td>
                <td>{step.status === "ok" ? step.summary : `Failed: ${step.error}`}</td>
                <td>{step.ms}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </details>
    );
  }

  function Report({ run }) {
    const [openId, setOpenId] = useState(null);
    const report = run.report;
    const profile = report.profile || {};
    const market = report.market || {};
    const narrative = report.narrative || {};
    const sources = report.data_sources || {};
    const planner = run.planner || {};

    return (
      <div className="agent-report" aria-live="polite">
        <div className="agent-card agent-summary">
          <div className="agent-badges">
            <Chip tone={narrative.mode === "ollama" ? "good" : ""}>
              {narrative.mode === "ollama" ? "Summary checked against your data" : "Standard summary"}
            </Chip>
            <Chip>{planner.mode === "ollama" ? "Plan chosen by Ollama" : `Fixed pipeline · ${planner.reason || ""}`}</Chip>
            {run.run_id ? <Chip>Run #{run.run_id}</Chip> : null}
          </div>
          <p className="agent-narrative">{narrative.text}</p>
          <div className="agent-stats">
            <div><strong>{market.jobs_ranked ?? 0}</strong><span>jobs ranked</span></div>
            <div><strong>{market.apply_now ?? 0}</strong><span>ready to apply</span></div>
            <div><strong>{market.one_skill_away ?? 0}</strong><span>one skill away</span></div>
            <div><strong>{profile.experience_years ?? "—"}</strong><span>years of experience</span></div>
          </div>
          {(report.notes || []).map((note) => <p className="agent-note" key={note}>⚠ {note}</p>)}
        </div>

        <div className="agent-card">
          <div className="agent-label">Your profile</div>
          <div className="agent-domains">
            {(profile.domains || []).length === 0 && <span className="agent-muted">No domain could be predicted yet.</span>}
            {(profile.domains || []).map((domain) => (
              <div key={domain.family} className="agent-domain">
                <strong>{domain.family}</strong>
                <span>{Math.round(domain.score * 100)}%</span>
                <small>{(domain.matched_skills || []).slice(0, 5).join(", ")}</small>
              </div>
            ))}
          </div>
          {(profile.target_roles || []).length > 0 && (
            <div className="agent-chips">{profile.target_roles.slice(0, 10).map((role) => <Chip key={role}>{role}</Chip>)}</div>
          )}
        </div>

        {report.explained_job && (
          <div className="agent-card">
            <div className="agent-label">Why {report.explained_job.title} scores {pct(report.explained_job.score)}</div>
            <p className="agent-muted">{report.explained_job.story}</p>
            <div className="agent-stats">
              <div><strong>{pct(report.explained_job.components.semantic_pct)}</strong><span>wording</span></div>
              <div><strong>{pct(report.explained_job.components.skill_pct)}</strong><span>skills</span></div>
              <div><strong>{report.explained_job.components.experience_gate}</strong><span>experience gate</span></div>
            </div>
          </div>
        )}

        {(report.matches || []).length > 0 && (
          <div className="agent-card">
            <div className="agent-label">Best matches</div>
            {report.matches.map((match) => (
              <MatchCard key={match.job_id} match={match} open={openId === match.job_id}
                         onToggle={() => setOpenId(openId === match.job_id ? null : match.job_id)} />
            ))}
          </div>
        )}

        {(report.skill_levers || []).length > 0 && (
          <div className="agent-card">
            <div className="agent-label">Skills that would move the most roles</div>
            <table className="agent-table">
              <thead><tr><th>Skill</th><th>Roles improved</th><th>Move up a tier</th><th>Best gain</th></tr></thead>
              <tbody>
                {report.skill_levers.map((lever) => (
                  <tr key={lever.skill}>
                    <td>{lever.skill}</td><td>{lever.roles_improved}</td><td>{lever.roles_promoted}</td>
                    <td>+{lever.best_lift_pct} pts{lever.best_company ? ` (${lever.best_company})` : ""}</td>
                  </tr>))}
              </tbody>
            </table>
          </div>
        )}

        <div className="agent-card agent-sources">
          <div className="agent-label">Data sources</div>
          {(sources.providers || []).length === 0
            ? <span className="agent-muted">No jobs are stored yet.</span>
            : (
              <ul>
                {sources.providers.map((provider) => (
                  <li key={provider.provider}>
                    <strong>{provider.provider}</strong> · {provider.open_jobs} open
                    {provider.last_seen ? <span className="agent-muted"> · last seen {when(provider.last_seen)}</span> : null}
                  </li>))}
              </ul>)}
          {(sources.recent_ingestion || []).length > 0 && (
            <p className="agent-muted">
              Latest fetches: {sources.recent_ingestion.slice(0, 3).map((r) => `${r.source} (${r.status})`).join(", ")}
            </p>)}
          <p className="agent-policy">{sources.policy}</p>
        </div>

        <Trace plan={run.plan} trace={run.trace || []} />
      </div>
    );
  }

  function ImportResult({ result }) {
    return (
      <div className="agent-card agent-import" aria-live="polite">
        <div className="agent-label">LinkedIn data imported</div>
        <p>
          {result.experience.years} years of experience (from {result.experience.source}),
          {" "}{result.skills.length} skills, {result.positions.length} positions.
        </p>
        {result.extra_skills.length > 0 && (
          <p className="agent-muted">
            Skills outside the matching vocabulary (shown, not scored yet): {result.extra_skills.slice(0, 8).join(", ")}
          </p>)}
        {result.warnings.map((warning) => <p className="agent-note" key={warning}>⚠ {warning}</p>)}
      </div>
    );
  }

  function AgentPanel({ request, onProfileChanged }) {
    const [goal, setGoal] = useState("");
    const [planner, setPlanner] = useState("auto");
    const [summary, setSummary] = useState(true);
    const [running, setRunning] = useState(false);
    const [error, setError] = useState("");
    const [run, setRun] = useState(null);
    const [history, setHistory] = useState([]);
    const [importing, setImporting] = useState(false);
    const [imported, setImported] = useState(null);
    const fileRef = useRef(null);

    function loadHistory() {
      request("/api/v1/agent/runs").then((data) => setHistory(data.runs || [])).catch(() => setHistory([]));
    }
    useEffect(loadHistory, []);

    async function runAgent(event) {
      event.preventDefault();
      if (running) return;
      setRunning(true); setError("");
      try {
        const body = new FormData();
        body.append("goal", goal.trim());
        body.append("planner", planner);
        body.append("narrative", summary ? "auto" : "off");
        setRun(await request("/api/v1/agent/run", { method: "POST", body }));
        loadHistory();
      } catch (requestError) {
        setError(requestError.message);
      } finally {
        setRunning(false);
      }
    }

    async function openRun(id) {
      setError("");
      try {
        setRun(fromSavedRun(await request(`/api/v1/agent/runs/${id}`)));
      } catch (requestError) {
        setError(requestError.message);
      }
    }

    async function importLinkedIn(file) {
      if (!file) return;
      setImporting(true); setError("");
      try {
        const body = new FormData();
        body.append("file", file);
        const data = await request("/api/v1/profile/linkedin-export", { method: "POST", body });
        setImported(data);
        if (onProfileChanged) onProfileChanged(data);
      } catch (requestError) {
        setError(requestError.message);
      } finally {
        setImporting(false);
        if (fileRef.current) fileRef.current.value = "";
      }
    }

    return (
      <section className="agent-section" id="agent">
        <div className="content-heading">
          <div>
            <span className="section-kicker">Job-search agent</span>
            <h2>Let the agent do the searching</h2>
          </div>
        </div>

        <form className="agent-card agent-controls" onSubmit={runAgent}>
          <label className="agent-label" htmlFor="agent-goal">What are you looking for? (optional)</label>
          <input id="agent-goal" value={goal} maxLength={500} onChange={(event) => setGoal(event.target.value)}
                 placeholder="e.g. remote jobs that need Terraform, junior-friendly" />
          <div className="agent-options">
            <label>Planning
              <select value={planner} onChange={(event) => setPlanner(event.target.value)} aria-label="Planning mode">
                <option value="auto">Auto (use Ollama for custom goals)</option>
                <option value="fixed">Fixed pipeline only</option>
              </select>
            </label>
            <label className="agent-check">
              <input type="checkbox" checked={summary} onChange={(event) => setSummary(event.target.checked)} />
              Write a short summary
            </label>
            <button type="button" className="ghost-button" disabled={importing}
                    onClick={() => fileRef.current && fileRef.current.click()}>
              {importing ? "Reading export…" : "Import LinkedIn data (.zip)"}
            </button>
            <input ref={fileRef} type="file" accept=".zip" hidden aria-label="LinkedIn export file"
                   onChange={(event) => importLinkedIn(event.target.files && event.target.files[0])} />
            <button type="submit" className="search-button" disabled={running}>
              {running ? "Working…" : "Run agent"}
            </button>
          </div>
          <p className="agent-muted">
            Uses your newest profile. With no goal it runs the standard pipeline. This can take up to a minute on a CPU.
          </p>
        </form>

        {error && <div className="status-message error" role="alert">{error}</div>}
        {imported && <ImportResult result={imported} />}

        {history.length > 0 && (
          <div className="agent-history">
            <span className="agent-label">Recent runs</span>
            {history.slice(0, 5).map((item) => (
              <button type="button" key={item.id} onClick={() => openRun(item.id)}>
                #{item.id} · {item.goal || "default"} · {when(item.created_at)}
              </button>))}
          </div>
        )}

        {run && <Report run={run} />}
      </section>
    );
  }

  window.AgentPanel = AgentPanel;
})();
