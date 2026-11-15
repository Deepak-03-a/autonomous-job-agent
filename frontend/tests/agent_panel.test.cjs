const test = require("node:test");
const assert = require("node:assert/strict");
const { React, render, screen, fireEvent, waitFor, cleanup, AgentPanel, File } = require("./harness.cjs");

const h = React.createElement;
test.afterEach(cleanup);

const MATCH = {
  job_id: 11, title: "Platform Engineer", company: "Acme", location: "Pune, India", source: "Greenhouse",
  score: 71.79, tier: "Tier 2: Moderate Match", queue: "One skill away", experience_fit: "Near fit", required_years: 3,
  matched_skills: ["kubernetes", "aws"], missing_skills: ["golang"], best_next_skill: { skill: "golang", lift_pct: 4.2 },
  apply_url: "https://acme.test/apply/11", posted_at: "2026-11-10",
};
const RUN = {
  run_id: null, status: "success", goal: "find the best jobs for my profile",
  planner: { mode: "fixed", reason: "default goal needs no planning" },
  plan: [], trace: [
    { step: 1, tool: "get_profile", args: {}, status: "ok", ms: 3, summary: "12 skills, 4.8 years, 2 domains" },
    { step: 2, tool: "find_jobs", args: { limit: 10 }, status: "ok", ms: 120, summary: "2 of 2 matching jobs (2 ranked)" },
    { step: 3, tool: "explain_job", args: { job_id: 999 }, status: "error", ms: 2, error: "ToolError: job 999 was not found" },
  ],
  report: {
    goal: "find the best jobs for my profile",
    profile: { resume_id: 3, filename: "cv.pdf", experience_years: 4.8, extra_skills: [], target_roles: ["devops", "sre"],
               domains: [{ family: "DevOps, SRE and Cloud", score: 0.886, matched_skills: ["aws", "docker", "kubernetes"] }] },
    market: { jobs_ranked: 2, apply_now: 1, one_skill_away: 1, semantic_mode: "lexical" },
    matches: [MATCH, { ...MATCH, job_id: 12, title: "Backend Engineer", company: "Beta", score: 55, queue: "Stretch",
                       apply_url: "javascript:alert(1)", missing_skills: [], best_next_skill: null }],
    skill_levers: [{ skill: "golang", roles_improved: 5, roles_promoted: 2, best_lift_pct: 4.2, best_company: "Acme" }],
    explained_job: null,
    data_sources: { providers: [{ provider: "Greenhouse", open_jobs: 40, last_seen: "2026-11-14T08:00:00Z" }],
                    recent_ingestion: [{ source: "Acme", status: "ok" }],
                    policy: "This app never logs in to, or scrapes, LinkedIn or Indeed." },
    narrative: { text: "2 jobs ranked; Platform Engineer is your best match.", mode: "template", evidence: [] },
    notes: ["No ranked job passed your filters; try relaxing them."],
  },
};

function fakeRequest(overrides = {}) {
  const calls = [];
  const request = async (path, options) => {
    calls.push({ path, options });
    const handler = overrides[path] || (path === "/api/v1/agent/runs" ? () => ({ runs: [] }) : null);
    if (!handler) throw new Error(`unexpected request ${path}`);
    return handler(options);
  };
  request.calls = calls;
  return request;
}

const runButton = () => screen.getByRole("button", { name: /run agent|working/i });

test("renders the controls and hides history when there is none", async () => {
  render(h(AgentPanel, { request: fakeRequest() }));
  assert.ok(screen.getByText("Let the agent do the searching"));
  assert.ok(screen.getByLabelText(/what are you looking for/i));
  assert.equal(runButton().disabled, false);
  await waitFor(() => assert.equal(screen.queryByText("Recent runs"), null));
});

test("running sends the form, disables the button meanwhile, then shows the whole report", async () => {
  let release;
  const request = fakeRequest({ "/api/v1/agent/run": () => new Promise((resolve) => { release = () => resolve(RUN); }) });
  render(h(AgentPanel, { request }));
  fireEvent.click(runButton());

  await waitFor(() => assert.equal(runButton().textContent, "Working…"));
  assert.equal(runButton().disabled, true);
  const sent = request.calls.find((c) => c.path === "/api/v1/agent/run");
  assert.equal(sent.options.method, "POST");
  assert.deepEqual([sent.options.body.get("goal"), sent.options.body.get("planner"), sent.options.body.get("narrative")], ["", "auto", "auto"]);
  assert.equal(sent.options.body.get("resume_id"), null);   // the newest profile is used, never a guessed id

  release();
  await screen.findByText("2 jobs ranked; Platform Engineer is your best match.");
  assert.ok(screen.getByText("Standard summary"));
  assert.ok(screen.getByText(/Fixed pipeline · default goal needs no planning/));
  assert.ok(screen.getByText("DevOps, SRE and Cloud"));
  assert.ok(screen.getByText("89%"));
  assert.ok(screen.getByText("Platform Engineer"));
  assert.ok(screen.getByText("golang"));                      // skill lever row
  assert.ok(screen.getByText("Greenhouse"));
  assert.ok(screen.getByText(/never logs in to, or scrapes, LinkedIn or Indeed/));
  assert.ok(screen.getByText(/No ranked job passed your filters/));
  assert.ok(screen.getByText(/How this was produced · 3 steps/));
  assert.equal(runButton().disabled, false);
});

test("goal and options are sent as chosen", async () => {
  const request = fakeRequest({ "/api/v1/agent/run": () => RUN });
  render(h(AgentPanel, { request }));
  fireEvent.change(screen.getByLabelText(/what are you looking for/i), { target: { value: "  remote jobs with Terraform " } });
  fireEvent.change(screen.getByLabelText("Planning mode"), { target: { value: "fixed" } });
  fireEvent.click(screen.getByLabelText("Write a short summary"));
  fireEvent.click(runButton());
  await screen.findByText(/2 jobs ranked/);
  const body = request.calls.find((c) => c.path === "/api/v1/agent/run").options.body;
  assert.deepEqual([body.get("goal"), body.get("planner"), body.get("narrative")], ["remote jobs with Terraform", "fixed", "off"]);
});

test("a match expands to show skills, the lift and a safe apply link; unsafe links are never rendered", async () => {
  render(h(AgentPanel, { request: fakeRequest({ "/api/v1/agent/run": () => RUN }) }));
  fireEvent.click(runButton());
  await screen.findByText("Platform Engineer");

  assert.equal(screen.queryByText("Open the official page ↗"), null);
  fireEvent.click(screen.getByRole("button", { name: /Platform Engineer/ }));
  assert.ok(screen.getByText("golang · +4.2 pts"));
  assert.ok(screen.getByText("✓ kubernetes"));
  const link = screen.getByText("Open the official page ↗");
  assert.equal(link.getAttribute("href"), "https://acme.test/apply/11");
  assert.match(link.getAttribute("rel"), /noopener/);
  fireEvent.click(screen.getByRole("button", { name: /Platform Engineer/ }));
  assert.equal(screen.queryByText("Open the official page ↗"), null);

  fireEvent.click(screen.getByRole("button", { name: /Backend Engineer/ }));
  assert.ok(screen.getByText(/No safe apply link/));
  assert.equal(document.querySelector('a[href^="javascript"]'), null);
});

test("failed steps are shown as failed in the trace", async () => {
  render(h(AgentPanel, { request: fakeRequest({ "/api/v1/agent/run": () => RUN }) }));
  fireEvent.click(runButton());
  await screen.findByText(/How this was produced/);
  const failed = screen.getByText(/Failed: ToolError: job 999 was not found/);
  assert.match(failed.closest("tr").className, /failed/);
});

test("a request error is shown, the report stays empty and the button works again", async () => {
  const request = fakeRequest({ "/api/v1/agent/run": () => { throw new Error("Upload a resume or import your LinkedIn data first."); } });
  render(h(AgentPanel, { request }));
  fireEvent.click(runButton());
  const alert = await screen.findByRole("alert");
  assert.match(alert.textContent, /Upload a resume/);
  assert.equal(runButton().disabled, false);
  assert.equal(screen.queryByText(/How this was produced/), null);
});

test("text from the model or the internet is rendered as text, never as HTML", async () => {
  const hostile = structuredClone(RUN);
  hostile.report.narrative.text = '<img src=x onerror="window.__pwned=1"> best match';
  hostile.report.matches[0].title = "<script>window.__pwned=1</script>Engineer";
  render(h(AgentPanel, { request: fakeRequest({ "/api/v1/agent/run": () => hostile }) }));
  fireEvent.click(runButton());
  await screen.findByText(/best match/);
  assert.equal(document.querySelector("img"), null);
  assert.equal(document.querySelector(".agent-match script"), null);
  assert.equal(window.__pwned, undefined);
});

test("a checked summary is labelled as verified, and saved runs can be reopened", async () => {
  const verified = structuredClone(RUN);
  verified.report.narrative = { text: "Checked text.", mode: "ollama", evidence: ["Platform Engineer"] };
  const saved = { id: 7, goal: "remote jobs", planner: "ollama", status: "success", trace: RUN.trace,
                  report: verified.report, created_at: "2026-11-14T10:00:00Z" };
  const request = fakeRequest({
    "/api/v1/agent/runs": () => ({ runs: [{ id: 7, goal: "remote jobs", planner: "ollama", status: "success", steps: 4, created_at: saved.created_at }] }),
    "/api/v1/agent/runs/7": () => saved,
  });
  render(h(AgentPanel, { request }));
  fireEvent.click(await screen.findByRole("button", { name: /#7 · remote jobs/ }));
  await screen.findByText("Checked text.");
  assert.ok(screen.getByText("Summary checked against your data"));
  assert.ok(screen.getByText("Plan chosen by Ollama"));
  assert.ok(screen.getByText("Run #7"));
});

test("a history failure is silent", async () => {
  const request = fakeRequest({ "/api/v1/agent/runs": () => { throw new Error("offline"); } });
  render(h(AgentPanel, { request }));
  await waitFor(() => assert.ok(request.calls.length >= 1));
  assert.equal(screen.queryByRole("alert"), null);
  assert.equal(screen.queryByText("Recent runs"), null);
});

test("a saved run that can no longer be opened shows the server's reason", async () => {
  const request = fakeRequest({
    "/api/v1/agent/runs": () => ({ runs: [{ id: 7, goal: "", created_at: "2026-11-14T10:00:00Z" }] }),
    "/api/v1/agent/runs/7": () => { throw new Error("That run was not found."); },
  });
  render(h(AgentPanel, { request }));
  fireEvent.click(await screen.findByRole("button", { name: /#7 · default/ }));
  assert.match((await screen.findByRole("alert")).textContent, /not found/);
  assert.equal(screen.queryByText(/How this was produced/), null);
});

test("importing a LinkedIn export posts the file, summarises it and tells the app", async () => {
  const result = { resume_id: 9, filename: "linkedin-export (cv.pdf)", experience: { years: 4.8, source: "linkedin positions" },
                   skills: ["aws", "docker"], positions: [{}, {}], extra_skills: ["Team Leadership"], warnings: ["No Profile.csv found."] };
  const seen = [];
  const request = fakeRequest({ "/api/v1/profile/linkedin-export": () => result });
  render(h(AgentPanel, { request, onProfileChanged: (data) => seen.push(data.resume_id) }));
  const file = new File(["zip"], "export.zip", { type: "application/zip" });
  fireEvent.change(screen.getByLabelText("LinkedIn export file"), { target: { files: [file] } });
  await screen.findByText("LinkedIn data imported");
  assert.match(screen.getByText(/4\.8 years of experience/).textContent, /linkedin positions/);
  assert.ok(screen.getByText(/Team Leadership/));
  assert.ok(screen.getByText(/No Profile\.csv found/));
  const sent = request.calls.find((c) => c.path === "/api/v1/profile/linkedin-export");
  assert.equal(sent.options.body.get("file").name, "export.zip");
  assert.deepEqual(seen, [9]);
});

test("a rejected LinkedIn export shows the server's reason", async () => {
  const request = fakeRequest({ "/api/v1/profile/linkedin-export": () => { throw new Error("That file is not a zip archive."); } });
  render(h(AgentPanel, { request }));
  fireEvent.change(screen.getByLabelText("LinkedIn export file"), { target: { files: [new File(["x"], "a.zip")] } });
  assert.match((await screen.findByRole("alert")).textContent, /not a zip/);
  assert.equal(screen.queryByText("LinkedIn data imported"), null);
});
