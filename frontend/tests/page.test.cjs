// Loads the page the way the browser does: React and ReactDOM UMD builds, then each
// text/babel script compiled by Babel standalone and run as a classic script in one
// shared global scope. Catches syntax Babel rejects and collisions between App.jsx
// and AgentPanel.jsx that esbuild-based tests would not.
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { JSDOM } = require("jsdom");
const Babel = require("@babel/standalone");

const root = path.join(__dirname, "..");
const read = (file) => fs.readFileSync(file, "utf8");
const umd = (name) => read(path.join(root, "node_modules", name));

const RUN = {
  run_id: null, status: "success", goal: "find the best jobs for my profile",
  planner: { mode: "fixed", reason: "default goal needs no planning" }, plan: [],
  trace: [{ step: 1, tool: "get_profile", args: {}, status: "ok", ms: 2, summary: "ok" }],
  report: {
    profile: { experience_years: 3, domains: [], target_roles: [], extra_skills: [] },
    market: { jobs_ranked: 1, apply_now: 1, one_skill_away: 0 },
    matches: [{ job_id: 1, title: "Cloud Engineer", company: "Acme", location: "Pune", source: "Feed", score: 80, tier: "Tier 1",
                queue: "Apply now", matched_skills: [], missing_skills: [], best_next_skill: null, apply_url: "https://acme.test" }],
    skill_levers: [], data_sources: { providers: [], recent_ingestion: [], policy: "policy text" },
    narrative: { text: "One job is ready to apply to.", mode: "template", evidence: [] }, notes: [],
  },
};

function loadPage(fetchImpl) {
  const dom = new JSDOM('<!doctype html><div id="root"></div>', { runScripts: "outside-only", url: "http://localhost/", pretendToBeVisual: true });
  const { window } = dom;
  window.fetch = fetchImpl;
  window.Headers = globalThis.Headers;
  if (!window.AbortSignal.timeout) window.AbortSignal.timeout = () => undefined;
  window.eval(umd("react/umd/react.production.min.js"));
  window.eval(umd("react-dom/umd/react-dom.production.min.js"));
  // The same order as index.html.
  for (const file of ["AgentPanel.jsx", "App.jsx"]) {
    window.eval(Babel.transform(read(path.join(root, "src", file)), { presets: ["react"] }).code);
  }
  return window;
}

const json = (data, ok = true) => ({ ok, json: async () => data });
const settle = (ms = 60) => new Promise((resolve) => setTimeout(resolve, ms));

test("index.html loads the panel before the app, from the same origin", () => {
  const html = read(path.join(root, "index.html"));
  const panel = html.indexOf("/frontend/src/AgentPanel.jsx");
  const app = html.indexOf("/frontend/src/App.jsx");
  assert.ok(panel > 0 && panel < app);
});

test("both files compile under Babel and the app renders the agent section and nav link", async () => {
  const window = loadPage(async () => json({ runs: [] }));
  await settle();
  const doc = window.document;
  assert.ok(doc.querySelector("#agent"), "agent section is on the page");
  assert.match(doc.querySelector("#agent").textContent, /Let the agent do the searching/);
  assert.ok([...doc.querySelectorAll(".nav-links a")].some((a) => a.getAttribute("href") === "#agent"));
  assert.ok(doc.querySelector("#discover"), "the existing discovery section is still there");
});

test("running the agent from the real app calls the API and shows the report", async () => {
  const calls = [];
  const window = loadPage(async (url, options = {}) => {
    calls.push(String(url));
    if (String(url).endsWith("/api/v1/agent/run")) return json(RUN);
    return json({ runs: [] });
  });
  await settle();
  window.document.querySelector("#agent button[type=submit]").click();
  await settle(150);
  assert.ok(calls.some((u) => u.endsWith("/api/v1/agent/run")));
  assert.match(window.document.querySelector("#agent").textContent, /One job is ready to apply to\./);
  assert.match(window.document.querySelector("#agent").textContent, /Cloud Engineer/);
});

test("if the panel script fails to load, the rest of the app still renders", async () => {
  const dom = new JSDOM('<!doctype html><div id="root"></div>', { runScripts: "outside-only", url: "http://localhost/", pretendToBeVisual: true });
  const { window } = dom;
  window.fetch = async () => json({ runs: [] });
  window.Headers = globalThis.Headers;
  if (!window.AbortSignal.timeout) window.AbortSignal.timeout = () => undefined;
  window.eval(umd("react/umd/react.production.min.js"));
  window.eval(umd("react-dom/umd/react-dom.production.min.js"));
  window.eval(Babel.transform(read(path.join(root, "src", "App.jsx")), { presets: ["react"] }).code);   // no AgentPanel
  await settle();
  assert.ok(window.document.querySelector("#discover"));
  assert.equal(window.document.querySelector("#agent"), null);
});
