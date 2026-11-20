/**
 * Kovaak Agent - Frontend Application Script (v0.3.0)
 *
 * Manages:
 * - Smart data_revision polling & responsive 7-tab workspace navigation
 * - First-run onboarding & one-click Load Sample workflow
 * - Deterministic Performance Intelligence (5-question summary, skill radar, scenario trends, benchmarks)
 * - Adaptive Training Planner, readiness/fatigue check-in & closed-loop session evaluation
 * - Multi-role Structured Coaching Brief, Evidence Inspector, Effectiveness Table & History Q&A
 * - Hardware/setup annotations, session timeline, memory CRUD (with permanent delete), & provider settings
 */

const $ = (selector) => document.querySelector(selector);
const THEME_KEY = "kovaak-agent-theme";
const MODEL_PROVIDER_API_VERSION = 1;

const PROVIDERS = {
  openai: {
    label: "OpenAI",
    defaultModel: "gpt-5.4-mini",
    keyPlaceholder: "sk-...",
    requiresKey: true,
    hint: "Uses the OpenAI Responses API.",
  },
  openrouter: {
    label: "OpenRouter",
    defaultModel: "~openai/gpt-latest",
    keyPlaceholder: "sk-or-v1-...",
    requiresKey: true,
    hint: "Uses OpenRouter's OpenAI-compatible chat completions API and supports its full model catalog.",
  },
  gemini: {
    label: "Google Gemini",
    defaultModel: "gemini-3.7-flash",
    keyPlaceholder: "AIza...",
    requiresKey: true,
    hint: "Uses Gemini's native Interactions API.",
  },
  local: {
    label: "Local (Ollama / LM Studio)",
    defaultModel: "llama3.2",
    keyPlaceholder: "Optional token",
    requiresKey: false,
    hint: "Uses a local loopback OpenAI-compatible server (127.0.0.1 / localhost) with zero external data transfer.",
  },
};

let providerStatus = {};
let latestDigestLoaded = false;
let memoryLoaded = false;
let savedRoles = [];
let savedNotes = [];
let editingMemory = null;
let dashboardRequest = 0;
let recordsRequest = 0;
let lastRevision = null;
let cachedIntelligence = null;
let cachedTaxonomy = null;
let activePlanId = null;
let benchmarkTemplateCache = null;

function applyTheme(theme, persist = true) {
  const nextTheme = theme === "dark" ? "dark" : "light";
  document.documentElement.dataset.theme = nextTheme;
  if (persist) localStorage.setItem(THEME_KEY, nextTheme);
  const isDark = nextTheme === "dark";
  const toggle = $("#theme-toggle");
  toggle.setAttribute("aria-pressed", String(isDark));
  toggle.setAttribute("aria-label", `Switch to ${isDark ? "light" : "dark"} theme`);
  $("#theme-label").textContent = isDark ? "Light mode" : "Dark mode";
  $("#theme-color").setAttribute("content", isDark ? "#171a1d" : "#f5f5f2");
}

async function api(path, options = {}) {
  const response = await fetch(path, {
    headers: { "Content-Type": "application/json", ...(options.headers || {}) },
    ...options,
  });
  const payload = await response.json();
  if (!response.ok) {
    const error = new Error(payload.error || `Request failed (${response.status})`);
    error.diagnostics = payload.diagnostics;
    throw error;
  }
  return payload;
}

function number(value, digits = 1) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value);
}

function duration(value) {
  if (value === null || value === undefined) return "—";
  if (value >= 60) {
    const minutes = Math.floor(value / 60);
    const seconds = Math.round(value % 60);
    return seconds ? `${minutes}m ${seconds}s` : `${minutes} min`;
  }
  return `${number(value, 1)} sec`;
}

function buildLabel(value) {
  if (!value) return "—";
  const parts = value.split(".");
  return parts.length > 3 ? parts.slice(0, 3).join(".") : value;
}

function shortDate(value, assumeUtc = false) {
  if (!value) return "No data yet";
  const normalized = assumeUtc && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
    ? `${value.replace(" ", "T")}Z`
    : value;
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.valueOf())) return value;
  return parsed.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function setMessage(element, text, error = false) {
  element.textContent = text;
  element.classList.toggle("error", error);
}

function clearModelDiagnostics() {
  const details = $("#model-diagnostics");
  details.hidden = true;
  details.open = false;
  $("#model-diagnostics-output").textContent = "";
  $("#provider-diagnostics-link").hidden = true;
}

function showModelDiagnostics(diagnostics) {
  if (!diagnostics || typeof diagnostics !== "object") return;
  const details = $("#model-diagnostics");
  const provider = String(diagnostics.provider || "").toLowerCase();
  $("#provider-diagnostics-link").hidden = provider !== "openrouter";
  $("#model-diagnostics-output").textContent = JSON.stringify(diagnostics, null, 2);
  details.hidden = false;
  details.open = true;
}

function appendInlineMarkdown(element, text) {
  const pattern = /(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`)/g;
  let cursor = 0;
  for (const match of text.matchAll(pattern)) {
    if (match.index > cursor) element.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    const node = document.createElement(token.startsWith("**") ? "strong" : token.startsWith("*") ? "em" : "code");
    node.textContent = token.startsWith("**") ? token.slice(2, -2) : token.slice(1, -1);
    element.append(node);
    cursor = match.index + token.length;
  }
  if (cursor < text.length) element.append(document.createTextNode(text.slice(cursor)));
}

function renderDigestMarkdown(container, markdown) {
  container.replaceChildren();
  container.classList.add("digest-report");
  let section = container;
  let listStack = [];
  let listTone = "";
  const resetLists = () => { listStack = []; };
  const ensureSection = () => {
    if (section === container) {
      section = document.createElement("section");
      section.className = "report-section";
      container.append(section);
    }
    return section;
  };
  for (const rawLine of String(markdown || "").replace(/\r\n?/g, "\n").split("\n")) {
    const trimmed = rawLine.trim();
    if (!trimmed || /^-{3,}$/.test(trimmed)) {
      resetLists();
      continue;
    }
    const heading = trimmed.match(/^(#{2,4})\s+(.+)$/);
    if (heading) {
      resetLists();
      const level = heading[1].length;
      if (level <= 3) {
        section = document.createElement("section");
        section.className = "report-section";
        const headingText = heading[2].toLowerCase();
        if (headingText.includes("summary")) section.classList.add("report-summary");
        if (headingText.includes("recommendation")) section.classList.add("report-recommendations");
        container.append(section);
      }
      const node = document.createElement(level <= 3 ? "h3" : "h4");
      appendInlineMarkdown(node, heading[2]);
      ensureSection().append(node);
      listTone = level === 4 && heading[2].toLowerCase().includes("limitation") ? "evidence-limitations" : "";
      continue;
    }
    const listItem = rawLine.match(/^(\s*)([-*]|\d+\.)\s+(.+)$/);
    if (listItem) {
      const indent = listItem[1].replace(/\t/g, "  ").length;
      const type = /\d+\./.test(listItem[2]) ? "ol" : "ul";
      while (listStack.length && indent < listStack.at(-1).indent) listStack.pop();
      if (listStack.length && indent === listStack.at(-1).indent && type !== listStack.at(-1).type) listStack.pop();
      if (!listStack.length || indent > listStack.at(-1).indent) {
        const list = document.createElement(type);
        if (listTone) list.classList.add(listTone);
        const parent = listStack.length ? listStack.at(-1).lastItem : ensureSection();
        parent.append(list);
        listStack.push({ indent, type, node: list, lastItem: null });
      }
      const item = document.createElement("li");
      appendInlineMarkdown(item, listItem[3]);
      listStack.at(-1).node.append(item);
      listStack.at(-1).lastItem = item;
      continue;
    }
    resetLists();
    const paragraph = document.createElement("p");
    appendInlineMarkdown(paragraph, trimmed);
    ensureSection().append(paragraph);
  }
}

function setDigestLayout(hasReport) {
  $(".performance-grid").classList.toggle("report-ready", hasReport);
  $("#digest-panel").classList.toggle("has-report", hasReport);
}

function renderDigestReport(result) {
  const digest = $("#digest");
  const provider = PROVIDERS[result.provider]?.label || result.provider || "Model";
  digest.classList.remove("empty");
  renderDigestMarkdown(digest, result.body);
  $("#digest-provider").textContent = provider;
  $("#digest-model").textContent = result.model || "";
  const evidence = result.evidence || {};
  $("#digest-window").textContent = `${number(evidence.summary?.runs ?? evidence.runs ?? 0, 0)} runs · ${evidence.period || "7-day"} · ${evidence.start || "saved review"}${evidence.role?.name ? ` · ${evidence.role.name}` : ""}`;
  $("#digest-meta").hidden = false;
  $("#digest-title").textContent = "Your coaching report";
  setDigestLayout(true);
  latestDigestLoaded = true;
}

async function loadLatestDigest() {
  if (latestDigestLoaded) return;
  const { report } = await api("/api/digest/latest");
  latestDigestLoaded = true;
  if (report) renderDigestReport(report);
}

function setState(element, text, kind = "") {
  element.textContent = text;
  const tone = kind === "ready" ? "badge-success badge-soft" : kind === "error" ? "badge-error badge-soft" : "badge-ghost";
  element.className = `badge state-badge ${tone}`;
}

function renderProvider(providerId, updateModel = true) {
  const definition = PROVIDERS[providerId] || PROVIDERS.openai;
  const status = providerStatus[providerId];
  $("#model-label").textContent = `${definition.label} model`;
  $("#api-key-label").innerHTML = `${definition.label} API key <span class="quiet">${definition.requiresKey ? "session only" : "optional for local server"}</span>`;
  $("#api-key").placeholder = definition.keyPlaceholder;
  $("#local-endpoint-wrap").hidden = providerId !== "local";
  $("#provider-hint").textContent = `${definition.hint} Compressed analytics, diagnoses, and relevant memories are shared only when you explicitly request AI coaching.`;
  if (updateModel) $("#model").value = status?.model || definition.defaultModel;
  const connected = definition.requiresKey ? Boolean(status?.api_key_connected) : true;
  setState($("#model-state"), connected ? (definition.requiresKey ? "Connected" : "Local ready") : "Disconnected", connected ? "ready" : "");
}

function updateSummary(summary) {
  const latest = summary.latest_run_details;
  $("#week-summary").textContent = summary.runs
    ? `${number(summary.runs, 0)} run${summary.runs === 1 ? "" : "s"} across ${number(summary.scenarios, 0)} scenario${summary.scenarios === 1 ? "" : "s"} · ${summary.average_accuracy == null ? "accuracy unavailable" : `${number(summary.average_accuracy)}% weighted accuracy`}`
    : "No runs in the seven-day window yet.";

  if (latest) {
    $("#hero-scenario").textContent = latest.scenario || "Unknown scenario";
    $("#hero-time").textContent = shortDate(latest.observed_at);
    $("#latest-score").textContent = number(latest.score, 1);
    $("#score-context").textContent = latest.weapon ? `with ${latest.weapon}` : "latest run";
    $("#latest-accuracy").textContent = latest.accuracy == null ? "—" : `${number(latest.accuracy)}%`;
    $("#shot-total").textContent = latest.shots == null ? "No shot data" : `${number(latest.shots, 0)} shots registered`;
    $("#latest-hits").textContent = number(latest.hits, 0);
    $("#miss-total").textContent = latest.misses == null ? "No miss data" : `${number(latest.misses, 0)} misses`;
    $("#latest-fps").textContent = number(latest.avg_fps, 0);
    $("#session-scenario").textContent = latest.scenario || "Unknown scenario";
    $("#duration-badge").textContent = duration(latest.duration_seconds);

    const conversion = latest.accuracy == null ? 0 : Math.max(0, Math.min(100, latest.accuracy));
    $("#conversion-label").textContent = latest.hits == null || latest.misses == null
      ? "No shot data"
      : `${number(latest.hits, 0)} landed · ${number(latest.misses, 0)} missed`;
    $("#conversion-fill").style.width = `${conversion}%`;

    $("#run-weapon").textContent = latest.weapon || "—";
    $("#run-sensitivity").textContent = latest.sensitivity == null
      ? "—"
      : `${number(latest.sensitivity, 2)}${latest.sensitivity_scale ? ` ${latest.sensitivity_scale}` : ""}`;
    $("#run-dpi").textContent = number(latest.dpi, 0);
    $("#run-fov").textContent = latest.fov == null ? "—" : `${number(latest.fov, 1)}${latest.fov_scale ? ` · ${latest.fov_scale}` : ""}`;
    $("#run-resolution").textContent = latest.resolution || "—";
    $("#run-version").textContent = buildLabel(latest.game_version);
  }

  if (summary.latest_source?.imported_at) {
    $("#last-sync").textContent = `Last import ${shortDate(summary.latest_source.imported_at, true)} · ${summary.latest_source.rows_seen} run${summary.latest_source.rows_seen === 1 ? "" : "s"}`;
  }
}

async function loadRuns() {
  const { runs } = await api("/api/runs?limit=18");
  const body = $("#runs-table");
  if (!runs.length) {
    body.innerHTML = '<tr><td colspan="6" class="empty-row">No runs imported yet.</td></tr>';
    return;
  }
  body.replaceChildren(...runs.map((run) => {
    const row = document.createElement("tr");
    const shots = run.hits == null || run.shots == null ? "—" : `${number(run.hits, 0)} / ${number(run.shots, 0)}`;
    const setup = [
      run.sensitivity == null ? "" : `${number(run.sensitivity, 2)} ${run.sensitivity_scale || "sens"}`,
      run.avg_fps == null ? "" : `${number(run.avg_fps, 0)} fps`,
    ].filter(Boolean).join(" · ") || "—";
    [shortDate(run.observed_at), run.scenario || "Unknown", number(run.score), run.accuracy == null ? "—" : `${number(run.accuracy)}%`, shots, setup]
      .forEach((value) => {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      });
    return row;
  }));
}

function catShortLabel(catId) {
  const list = cachedTaxonomy?.categories || [];
  const found = list.find((c) => c.id === catId);
  return found ? found.short_label : catId;
}

function openTagEditor(scenarioName) {
  const tagInfo = cachedIntelligence?.scenario_tags?.[scenarioName] || cachedTaxonomy?.tags?.[scenarioName];
  const current = new Set(tagInfo?.categories || []);
  $("#tag-scenario-name").textContent = `Scenario: ${scenarioName} (${tagInfo?.source === "manual" ? "Manual override" : "Automatic classification"})`;
  $("#tag-scenario-name").dataset.scenario = scenarioName;
  setMessage($("#tag-message"), "");
  const box = $("#tag-checkboxes");
  box.replaceChildren();
  for (const cat of (cachedTaxonomy?.categories || [])) {
    const lbl = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.className = "checkbox checkbox-sm";
    cb.value = cat.id;
    cb.checked = current.has(cat.id);
    const span = document.createElement("span");
    span.textContent = cat.label;
    lbl.append(cb, span);
    box.append(lbl);
  }
  $("#tag-dialog").showModal();
}

async function loadIntelligence() {
  const [intel, tax] = await Promise.all([
    api(`/api/intelligence?days=90&date=${encodeURIComponent($("#review-date").value)}`),
    api("/api/taxonomy"),
  ]);
  cachedIntelligence = intel;
  cachedTaxonomy = tax;

  // 5-Question Intelligence Strip
  const fq = intel.five_questions || {};
  $("#fq-improving").textContent = fq.improving || "—";
  $("#fq-holding-back").textContent = fq.holding_back || "—";
  $("#fq-train-today").textContent = fq.train_today || "—";
  $("#fq-progress").textContent = fq.measurable_progress || "—";
  $("#fq-next-change").textContent = fq.next_session_change || "—";

  // Strongest & Lagging Category Highlights
  const highlights = $("#category-highlights");
  highlights.replaceChildren();
  const catSum = intel.category_summary || {};
  const strongest = catSum.strongest || [];
  const weakest = catSum.weakest || [];
  const improving = catSum.improving || [];
  if (!strongest.length && !weakest.length) {
    highlights.innerHTML = '<p class="hint">Import runs across scenarios to compute strongest and lagging skill categories.</p>';
  } else {
    const pills = [
      ...(strongest[0] ? [{ tag: "Strongest Skill", title: strongest[0].label, detail: `Index ${number(strongest[0].skill_index)}/100 · ${strongest[0].runs} runs · PB prox ${number(strongest[0].pb_proximity_pct)}%` }] : []),
      ...(weakest[0] && weakest[0].id !== strongest[0]?.id ? [{ tag: "Primary Lagging Skill", title: weakest[0].label, detail: `Index ${number(weakest[0].skill_index)}/100 · Consistency ${number(weakest[0].consistency_score)}/100` }] : []),
      ...(improving[0] ? [{ tag: "Fastest Climbing", title: improving[0].label, detail: `${improving[0].velocity_pct_per_week > 0 ? "+" : ""}${number(improving[0].velocity_pct_per_week, 2)}%/wk velocity` }] : []),
    ];
    for (const p of pills) {
      const div = document.createElement("div");
      div.className = "highlight-pill";
      div.innerHTML = `<small></small><strong></strong><span></span>`;
      div.querySelector("small").textContent = p.tag;
      div.querySelector("strong").textContent = p.title;
      div.querySelector("span").textContent = p.detail;
      highlights.append(div);
    }
  }

  // Skills Tab: Radar & Category Table
  if (window.KovaakCharts) {
    window.KovaakCharts.renderRadarChart($("#skills-radar-chart"), catSum.categories || []);
  }
  $("#skills-coverage-badge").textContent = intel.empty ? "No runs" : `${intel.total_runs} runs · ${intel.active_days} days`;

  const catBody = $("#category-table");
  catBody.replaceChildren();
  const cats = catSum.categories || [];
  if (!cats.some((c) => c.runs > 0)) {
    catBody.innerHTML = '<tr><td colspan="8" class="empty-row">No categorized runs yet.</td></tr>';
  } else {
    for (const c of cats) {
      const row = document.createElement("tr");
      const vel = c.velocity_pct_per_week == null ? "—" : `${c.velocity_pct_per_week > 0 ? "+" : ""}${number(c.velocity_pct_per_week, 2)}%/wk`;
      for (const v of [
        c.label,
        number(c.runs, 0),
        c.skill_index == null ? "—" : `${number(c.skill_index)} / 100`,
        c.pb_proximity_pct == null ? "—" : `${number(c.pb_proximity_pct)}%`,
        c.consistency_score == null ? "—" : `${number(c.consistency_score)} / 100`,
        vel,
        c.weighted_accuracy == null ? "—" : `${number(c.weighted_accuracy)}%`,
        number(c.plateau_count, 0),
      ]) {
        const td = document.createElement("td");
        td.textContent = v;
        row.append(td);
      }
      catBody.append(row);
    }
  }

  // Trend scenario dropdown
  const scenSelect = $("#trend-scenario");
  const prevChoice = scenSelect.value;
  const scenarios = intel.scenarios || [];
  scenSelect.replaceChildren();
  if (!scenarios.length) {
    scenSelect.add(new Option("No scenarios yet", ""));
    $("#scenario-trend-chart").innerHTML = '<p class="hint empty-chart">Import runs to inspect scenario score and accuracy trends.</p>';
    $("#trend-scenario-stats").replaceChildren();
  } else {
    for (const s of scenarios) {
      scenSelect.add(new Option(`${s.scenario} (${s.runs})`, s.scenario));
    }
    scenSelect.value = scenarios.some((s) => s.scenario === prevChoice) ? prevChoice : scenarios[0].scenario;
    await loadScenarioTrend();
  }
}

async function loadScenarioTrend() {
  const scen = $("#trend-scenario").value;
  if (!scen) return;
  const days = $("#trend-days").value;
  const metric = $("#trend-metric").value;
  const data = await api(`/api/scenario/trend?scenario=${encodeURIComponent(scen)}&days=${encodeURIComponent(days)}&date=${encodeURIComponent($("#review-date").value)}`);
  if (window.KovaakCharts) {
    window.KovaakCharts.renderTrendChart($("#scenario-trend-chart"), data, metric);
  }
  const strip = $("#trend-scenario-stats");
  strip.replaceChildren();
  const a = data.analysis;
  if (a) {
    const items = [
      `Median: ${number(a.score.median)}`,
      `PB: ${number(a.score.best)}`,
      `CV: ${a.score.cv_pct == null ? "—" : `${number(a.score.cv_pct)}%`}`,
      `Consistency: ${a.consistency_score == null ? "—" : `${number(a.consistency_score)}/100`}`,
      `Velocity: ${a.velocity.pct_per_week == null ? "—" : `${a.velocity.pct_per_week > 0 ? "+" : ""}${number(a.velocity.pct_per_week, 2)}%/wk`}`,
      ...(a.plateau.is_plateau ? ["Plateau detected"] : []),
      ...(a.duration.mixed_durations ? ["Mixed durations"] : []),
    ];
    for (const txt of items) {
      const span = document.createElement("span");
      span.textContent = txt;
      strip.append(span);
    }
  }
}

async function loadBenchmarks() {
  const data = await api("/api/benchmarks");
  benchmarkTemplateCache = data.template;
  $("#benchmark-note").textContent = data.note || "";
  const container = $("#benchmarks-container");
  container.replaceChildren();

  if (data.personal_benchmarks?.length) {
    const group = document.createElement("div");
    group.className = "bench-group";
    group.innerHTML = "<strong>Personal Benchmark Scenarios</strong>";
    const tbl = document.createElement("table");
    tbl.className = "table table-zebra";
    tbl.innerHTML = "<thead><tr><th>Scenario</th><th>Runs</th><th>All-time PB</th><th>Recent Median</th><th>PB Proximity</th><th>Velocity</th></tr></thead>";
    const tb = document.createElement("tbody");
    for (const b of data.personal_benchmarks) {
      const tr = document.createElement("tr");
      for (const v of [
        b.scenario,
        number(b.runs, 0),
        number(b.pb),
        number(b.recent_median),
        b.pb_proximity_pct == null ? "—" : `${number(b.pb_proximity_pct)}%`,
        b.velocity_pct_per_week == null ? "—" : `${b.velocity_pct_per_week > 0 ? "+" : ""}${number(b.velocity_pct_per_week, 2)}%/wk`,
      ]) {
        const td = document.createElement("td");
        td.textContent = v;
        tr.append(td);
      }
      tb.append(tr);
    }
    tbl.append(tb);
    group.append(tbl);
    container.append(group);
  }

  for (const cb of (data.custom_benchmarks || [])) {
    const group = document.createElement("div");
    group.className = "bench-group";
    const head = document.createElement("div");
    head.className = "hw-row-head";
    const title = document.createElement("strong");
    title.textContent = `${cb.name} (${cb.version}) · Complete Rank: ${cb.overall_complete_rank}`;
    const del = document.createElement("button");
    del.type = "button";
    del.className = "btn btn-ghost btn-xs";
    del.textContent = "Remove";
    del.addEventListener("click", async () => {
      await api("/api/benchmarks/delete", { method: "POST", body: JSON.stringify({ id: cb.id }) });
      await loadBenchmarks();
    });
    head.append(title, del);
    const tbl = document.createElement("table");
    tbl.className = "table table-zebra";
    tbl.innerHTML = "<thead><tr><th>Scenario</th><th>PB</th><th>Achieved Rank</th><th>Next Target</th></tr></thead>";
    const tb = document.createElement("tbody");
    for (const row of cb.scenarios) {
      const tr = document.createElement("tr");
      const nextTxt = row.next_rank ? `${row.next_rank} (${number(row.next_threshold)} · ${number(row.progress_to_next_pct, 0)}%)` : "Max rank";
      for (const v of [row.scenario, number(row.pb), row.achieved_rank, nextTxt]) {
        const td = document.createElement("td");
        td.textContent = v;
        tr.append(td);
      }
      tb.append(tr);
    }
    tbl.append(tb);
    group.append(head, tbl);
    container.append(group);
  }

  if (!container.children.length) {
    container.innerHTML = '<p class="hint">No benchmark-tagged scenarios played yet. Play a Voltaic/Viscose scenario or import a JSON threshold table.</p>';
  }
}

async function loadDashboard() {
  const request = ++dashboardRequest;
  const query = new URLSearchParams({ period: $("#review-period").value, date: $("#review-date").value });
  const result = await api(`/api/dashboard?${query}`);
  if (request !== dashboardRequest) return;
  const stats = result.summary;
  $("#period-caption").textContent = `${result.start} to ${result.end_exclusive} (end excluded) · ${result.partial ? "Period in progress" : "Calendar period"} · Weeks start Monday`;
  $("#period-runs").textContent = number(stats.runs, 0);
  $("#previous-runs").textContent = `${number(result.previous.runs, 0)} in previous period`;
  $("#period-minutes").textContent = stats.minutes == null ? "—" : `${number(stats.minutes)} min`;
  $("#time-coverage").textContent = `Duration available for ${stats.timed_runs || 0} / ${stats.runs} runs`;
  $("#period-days").textContent = number(stats.active_days, 0);
  $("#retained-range").textContent = `${number(result.lifetime.runs, 0)} lifetime runs retained`;
  $("#period-accuracy").textContent = stats.average_accuracy == null ? "—" : `${number(stats.average_accuracy)}%`;
  $("#accuracy-coverage").textContent = stats.paired_accuracy_runs ? `Paired hits / shots: ${stats.paired_accuracy_runs} runs` : "Mean reported accuracy; no paired shot data";

  const intelByScen = new Map((cachedIntelligence?.scenarios || []).map((s) => [s.scenario, s]));
  const tagMap = cachedIntelligence?.scenario_tags || cachedTaxonomy?.tags || {};
  const table = $("#period-scenarios");
  table.replaceChildren();
  for (const item of result.scenarios) {
    const row = document.createElement("tr");
    const scIntel = intelByScen.get(item.scenario);
    const delta = item.score_change_percent == null ? "No baseline" : `${item.score_change_percent > 0 ? "+" : ""}${number(item.score_change_percent)}% (${item.previous_runs} prior)`;

    const cellScen = document.createElement("td");
    cellScen.textContent = item.scenario;
    if (scIntel?.plateau?.is_plateau) {
      const p = document.createElement("span");
      p.className = "plateau-chip";
      p.textContent = "Plateau";
      cellScen.append(" ", p);
    }

    const cellSkills = document.createElement("td");
    const cats = tagMap[item.scenario]?.categories || scIntel?.categories || [];
    for (const c of cats.slice(0, 3)) {
      const chip = document.createElement("span");
      chip.className = "skill-chip";
      chip.textContent = catShortLabel(c);
      cellSkills.append(chip);
    }

    const cellRuns = document.createElement("td");
    cellRuns.textContent = number(item.runs, 0);

    const cellAvg = document.createElement("td");
    cellAvg.textContent = number(item.average_score);

    const cellBest = document.createElement("td");
    cellBest.textContent = number(item.best_score);

    const cellAcc = document.createElement("td");
    cellAcc.textContent = item.average_accuracy == null ? "—" : `${number(item.average_accuracy)}%`;

    const cellDelta = document.createElement("td");
    cellDelta.textContent = delta;

    const cellEdit = document.createElement("td");
    const tagBtn = document.createElement("button");
    tagBtn.type = "button";
    tagBtn.className = "btn btn-ghost btn-xs";
    tagBtn.textContent = "Tags";
    tagBtn.addEventListener("click", () => openTagEditor(item.scenario));
    cellEdit.append(tagBtn);

    row.append(cellScen, cellSkills, cellRuns, cellAvg, cellBest, cellAcc, cellDelta, cellEdit);
    table.append(row);
  }
  if (!result.scenarios.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 8;
    cell.textContent = "No captured runs in this period. Choose an earlier date or import your stats.";
    row.append(cell);
    table.append(row);
  }
  const chart = $("#activity-chart");
  chart.replaceChildren();
  const maximum = Math.max(1, ...result.timeline.map((item) => item.runs));
  for (const item of result.timeline) {
    const column = document.createElement("div");
    column.className = "activity-column";
    const count = document.createElement("span");
    count.textContent = item.runs;
    const bar = document.createElement("div");
    bar.className = "activity-bar";
    bar.style.height = `${Math.max(2, (item.runs / maximum) * 65)}px`;
    const label = document.createElement("span");
    label.textContent = item.bucket.slice(5);
    column.title = `${item.bucket}: ${item.runs} runs`;
    column.append(count, bar, label);
    chart.append(column);
  }
  if (!result.timeline.length) chart.textContent = "Your training activity will appear here.";
  $("#annual-enabled").checked = result.schedule.enabled;
  $("#annual-schedule").textContent = result.schedule.enabled
    ? `Scheduled: ${result.schedule.next_due.slice(0, 10)} at 00:00, computer local time. ${result.schedule.error ? `Review error: ${result.schedule.error}` : ""}`
    : "Automatic year-end reviews are paused.";
}

async function loadRecords() {
  const request = ++recordsRequest;
  const query = new URLSearchParams({ period: $("#review-period").value, date: $("#review-date").value });
  const result = await api(`/api/records?${query}`);
  if (request !== recordsRequest) return;
  const current = result.current_streak ?? result.streaks?.current ?? 0;
  const longest = result.longest_streak ?? result.streaks?.longest ?? 0;
  $("#current-streak").textContent = `${number(current, 0)} day${current === 1 ? "" : "s"}`;
  $("#longest-streak").textContent = `${number(longest, 0)} day${longest === 1 ? "" : "s"}`;

  const table = $("#period-records");
  table.replaceChildren();
  const records = result.records || [];
  if (!records.length) {
    const row = document.createElement("tr");
    const cell = document.createElement("td");
    cell.colSpan = 5;
    cell.className = "empty-row";
    cell.textContent = "No personal bests set in this period. Choose an earlier date or import your stats.";
    row.append(cell);
    table.append(row);
    return;
  }
  for (const item of records) {
    const row = document.createElement("tr");
    const cellScenario = document.createElement("td");
    cellScenario.textContent = item.scenario || "Unknown";
    const cellNew = document.createElement("td");
    cellNew.textContent = number(item.new_best ?? item.score, 1);
    const cellPrev = document.createElement("td");
    cellPrev.textContent = item.previous_best == null ? "—" : number(item.previous_best, 1);
    const cellDelta = document.createElement("td");
    if (item.delta == null) {
      cellDelta.textContent = "—";
    } else {
      const badge = document.createElement("span");
      badge.className = "badge badge-success badge-soft";
      badge.textContent = `+${number(item.delta, 1)}`;
      cellDelta.append(badge);
    }
    const cellDate = document.createElement("td");
    cellDate.textContent = shortDate(item.observed_at);
    row.append(cellScenario, cellNew, cellPrev, cellDelta, cellDate);
    table.append(row);
  }
}

function renderPlan(plan) {
  const advisory = $("#plan-advisory");
  const container = $("#plan-items");
  const evalBox = $("#plan-eval-box");
  const evalResult = $("#plan-eval-result");
  container.replaceChildren();

  if (!plan) {
    activePlanId = null;
    $("#plan-title").textContent = "No active plan yet";
    $("#plan-status-badge").textContent = "—";
    $("#plan-objective").textContent = "Select a routine mode above and click Generate Today's Plan.";
    advisory.hidden = true;
    evalBox.hidden = true;
    return;
  }

  activePlanId = plan.id;
  $("#plan-title").textContent = `${plan.mode_label} (${plan.plan_date})`;
  $("#plan-status-badge").textContent = `${plan.live_summary.completed_scenarios}/${plan.live_summary.total_scenarios} complete`;
  $("#plan-objective").textContent = plan.objective;

  if (plan.readiness?.rest_advisory || (plan.readiness?.adjustments && plan.readiness.adjustments.length)) {
    advisory.hidden = false;
    advisory.textContent = [...(plan.readiness.adjustments || []), plan.readiness.medical_disclaimer].join(" ");
  } else {
    advisory.hidden = true;
  }

  for (const item of plan.items) {
    const card = document.createElement("div");
    card.className = "plan-item";
    const head = document.createElement("div");
    head.className = "plan-item-head";
    const left = document.createElement("strong");
    left.textContent = `${item.order}. ${item.scenario}`;
    const badge = document.createElement("span");
    const st = item.progress.target_status;
    badge.className = `badge badge-sm ${st === "achieved" ? "badge-success" : st === "missed" ? "badge-error" : "badge-ghost"}`;
    badge.textContent = `${item.progress.completed_runs}/${item.target_runs} runs · ${st.replace(/_/g, " ")}`;
    head.append(left, badge);

    const meta = document.createElement("div");
    meta.className = "plan-item-meta";
    meta.textContent = `${item.duration_minutes} min · ${item.target_runs} runs · Rule: ${item.difficulty_rule.label} · Target: ${item.performance_target.summary}`;

    const obj = document.createElement("p");
    obj.innerHTML = `<strong>Objective:</strong> `;
    obj.append(document.createTextNode(item.objective));

    const cue = document.createElement("p");
    cue.innerHTML = `<strong>Technique cue:</strong> `;
    cue.append(document.createTextNode(item.technique_cue));

    const rule = document.createElement("p");
    rule.className = "hint";
    rule.textContent = `Progress if: ${item.difficulty_rule.progress_if} | Step back if: ${item.difficulty_rule.regress_if}`;

    card.append(head, meta, obj, cue, rule);
    container.append(card);
  }

  evalBox.hidden = false;
  if (plan.evaluation) {
    evalResult.hidden = false;
    evalResult.innerHTML = `<strong>Outcome: ${escapeHtml(plan.evaluation.verdict)}</strong> — ${escapeHtml(plan.evaluation.summary)} (Next session: <code>${escapeHtml(plan.evaluation.next_session_change)}</code>)`;
  } else {
    evalResult.hidden = true;
  }
}

async function loadTraining() {
  const data = await api(`/api/plans/today?date=${encodeURIComponent($("#review-date").value)}`);
  if (data.readiness) {
    $("#rd-energy").value = data.readiness.energy;
    $("#rd-focus").value = data.readiness.focus;
    $("#rd-fatigue").value = data.readiness.fatigue;
    $("#rd-eye").value = data.readiness.eye_strain;
    $("#rd-wrist").value = data.readiness.wrist_discomfort;
    $("#rd-minutes").value = data.readiness.available_minutes || "";
    $("#readiness-badge").textContent = `Checked in (${data.readiness.check_date})`;
  }
  renderPlan(data.plan);
}

function renderCoachingBrief(brief) {
  const container = $("#brief-container");
  const warningsBox = $("#brief-warnings");
  const inspector = $("#evidence-inspector");
  container.replaceChildren();

  if (!brief) {
    container.innerHTML = '<p class="hint">Generate a coaching brief to inspect structured recommendations.</p>';
    warningsBox.hidden = true;
    inspector.hidden = true;
    return;
  }

  const warns = brief.validation?.warnings || [];
  if (warns.length) {
    warningsBox.hidden = false;
    warningsBox.textContent = warns.join(" ");
  } else {
    warningsBox.hidden = true;
  }

  const blocks = [
    { title: `Main Finding (${brief.mode === "llm_enhanced" ? `LLM · ${brief.model}` : "Local Deterministic"})`, body: brief.main_finding },
    { title: `Confidence · ${String(brief.confidence?.level || "low").toUpperCase()}`, body: brief.confidence?.rationale || "" },
    {
      title: "Supporting Evidence",
      list: (brief.evidence || []).map((e) => `${e.label}: ${e.metric} (n=${e.sample_size})`),
    },
    {
      title: "Technique Hypotheses (Verify via Video / Telemetry)",
      list: (brief.technique_hypotheses || []).map((h) => h.cue),
    },
    {
      title: "Recommended Action & Duration",
      body: `${brief.recommended_action} (${brief.training_duration})`,
      list: brief.recommended_scenarios?.length ? [`Scenarios: ${brief.recommended_scenarios.join(", ")}`] : [],
    },
    {
      title: "Expected Outcome & Evaluation",
      body: `${brief.expected_outcome} Check: ${brief.evaluation_criteria} Follow-up: ${brief.follow_up}`,
    },
    {
      title: "Prior Progress Evaluation",
      body: brief.progress_evaluation?.summary || "No prior evaluations yet.",
    },
  ];

  for (const blk of blocks) {
    const div = document.createElement("div");
    div.className = "brief-block";
    const h4 = document.createElement("h4");
    h4.textContent = blk.title;
    div.append(h4);
    if (blk.body) {
      const p = document.createElement("p");
      p.textContent = blk.body;
      div.append(p);
    }
    if (blk.list?.length) {
      const ul = document.createElement("ul");
      for (const item of blk.list) {
        const li = document.createElement("li");
        li.textContent = item;
        ul.append(li);
      }
      div.append(ul);
    }
    container.append(div);
  }

  if (brief.evidence_inspector) {
    inspector.hidden = false;
    $("#evidence-inspector-output").textContent = JSON.stringify(brief.evidence_inspector, null, 2);
  } else {
    inspector.hidden = true;
  }
}

async function loadCoachCenter() {
  const [briefRes, effRes] = await Promise.all([
    api(`/api/coach/brief?period=${encodeURIComponent($("#review-period").value)}&date=${encodeURIComponent($("#review-date").value)}`),
    api("/api/coach/effectiveness"),
  ]);
  renderCoachingBrief(briefRes.brief);

  const effBody = $("#effectiveness-table");
  effBody.replaceChildren();
  const outcomes = effRes.outcomes || [];
  if (!outcomes.length) {
    effBody.innerHTML = '<tr><td colspan="5" class="empty-row">Generate a training plan or coaching brief to track follow-up effectiveness.</td></tr>';
  } else {
    for (const o of outcomes) {
      const row = document.createElement("tr");
      for (const v of [
        shortDate(o.created_at),
        o.source_type === "training_plan" ? "Training Plan" : "Coaching Brief",
        o.focus || (o.scenarios || []).join(", "),
        o.verdict,
        o.summary,
      ]) {
        const td = document.createElement("td");
        td.textContent = v;
        row.append(td);
      }
      effBody.append(row);
    }
  }
}

async function loadHistoryTimeline() {
  const data = await api(`/api/history/timeline?days=90&limit=25&date=${encodeURIComponent($("#review-date").value)}`);
  if (window.KovaakCharts) {
    window.KovaakCharts.renderSessionDriftChart($("#session-drift-chart"), data.session_progression);
  }

  const hwBox = $("#hardware-list");
  hwBox.replaceChildren();
  if (!data.hardware_events?.length) {
    hwBox.innerHTML = '<p class="hint">No setup or sensitivity transitions detected yet.</p>';
  } else {
    for (const ev of data.hardware_events) {
      const row = document.createElement("div");
      row.className = "hw-row";
      const head = document.createElement("div");
      head.className = "hw-row-head";
      const title = document.createElement("strong");
      title.textContent = `${ev.observed_at.slice(0, 10)} · ${ev.summary}`;
      head.append(title);
      if (ev.source === "manual") {
        const del = document.createElement("button");
        del.type = "button";
        del.className = "btn btn-ghost btn-xs";
        del.textContent = "Delete";
        del.addEventListener("click", async () => {
          await api("/api/hardware/delete", { method: "POST", body: JSON.stringify({ id: ev.id }) });
          await loadHistoryTimeline();
        });
        head.append(del);
      }
      const detail = document.createElement("p");
      detail.className = "hint";
      const imp = ev.impact || {};
      detail.textContent = imp.comparable
        ? `Impact: ${imp.index_delta_pct > 0 ? "+" : ""}${number(imp.index_delta_pct, 2)}% normalized median (${imp.before_runs} prior vs ${imp.after_runs} post runs, ${imp.shared_scenarios} shared scenarios).`
        : (imp.reason || ev.note || ev.source);
      row.append(head, detail);
      hwBox.append(row);
    }
  }

  const timeline = $("#session-timeline");
  timeline.replaceChildren();
  if (!data.sessions?.length) {
    timeline.innerHTML = '<p class="hint">No training sessions recorded yet.</p>';
  } else {
    for (const s of data.sessions) {
      const row = document.createElement("div");
      row.className = "session-row";
      const head = document.createElement("div");
      head.className = "session-row-head";
      const title = document.createElement("strong");
      title.textContent = `${shortDate(s.start_at)} · ${s.runs_count} runs${s.total_minutes != null ? ` (${number(s.total_minutes, 1)} min)` : ""}`;
      const right = document.createElement("span");
      right.className = "badge badge-ghost badge-sm";
      right.textContent = s.pbs_set?.length ? `${s.pbs_set.length} PB(s)` : `${s.scenarios.length} scenario(s)`;
      head.append(title, right);
      const sub = document.createElement("p");
      sub.className = "hint";
      const drift = s.within_session_progression
        ? ` · Session drift: ${s.within_session_progression.end_to_end_pct > 0 ? "+" : ""}${number(s.within_session_progression.end_to_end_pct, 1)}%`
        : "";
      sub.textContent = `${s.scenarios.join(", ")}${s.weighted_accuracy != null ? ` · Acc ${number(s.weighted_accuracy)}%` : ""}${drift}`;
      row.append(head, sub);
      timeline.append(row);
    }
  }
}

async function loadMemory() {
  const data = await api("/api/memory");
  savedRoles = data.roles;
  const selected = localStorage.getItem("kovaak-agent-role") || $("#agent-role").value;
  const edit = $("#role-edit").value;
  for (const id of ["#agent-role", "#role-edit"]) {
    const select = $(id);
    select.replaceChildren();
    if (id === "#role-edit") select.add(new Option("Create new role", ""));
    for (const role of data.roles) select.add(new Option(role.name, role.id));
  }
  $("#agent-role").value = data.roles.some((role) => role.id === selected) ? selected : "coach";
  $("#role-edit").value = edit;
  renderMemories(data.notes);
  const catalog = $("#role-catalog");
  catalog.replaceChildren(...data.roles.map((role) => {
    const item = document.createElement("div");
    item.className = "role-summary";
    const title = document.createElement("strong");
    title.textContent = role.name;
    const description = document.createElement("p");
    description.className = "hint";
    description.textContent = role.instructions;
    item.append(title, description);
    return item;
  }));
  memoryLoaded = true;
}

function renderMemories(notes) {
  savedNotes = notes;
  const list = $("#memory-list");
  list.replaceChildren();
  const visible = notes.filter((note) => $("#show-archived").checked || note.status !== "archived");
  if (!visible.length) list.textContent = "No memories yet. Start with your long-term goal.";
  for (const note of visible) {
    const item = document.createElement("div");
    item.className = "memory-item";
    const label = document.createElement("small");
    label.textContent = `${note.kind} · ${note.status}${note.scenario ? ` · ${note.scenario}` : ""} · user note`;
    const text = document.createElement("p");
    text.textContent = note.body;
    item.append(label, text);

    const edit = document.createElement("button");
    edit.type = "button";
    edit.className = "btn btn-ghost btn-xs";
    edit.textContent = "Edit";
    edit.addEventListener("click", () => {
      editingMemory = note;
      $("#memory-kind").value = note.kind;
      $("#memory-body").value = note.body;
      $("#memory-scenario").value = note.scenario;
      $("#cancel-memory-edit").hidden = false;
      $("#memory-dialog").showModal();
      $("#memory-body").focus();
    });
    item.append(edit);

    const actions = note.status === "archived"
      ? [["Restore", "active"]]
      : [[note.status === "completed" ? "Reopen" : "Complete", note.status === "completed" ? "active" : "completed"], ["Archive", "archived"]];
    for (const [caption, status] of actions) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "btn btn-ghost btn-xs";
      button.textContent = caption;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try {
          const result = await api("/api/memory/note", { method: "POST", body: JSON.stringify({ ...note, status }) });
          renderMemories(result.notes);
        } catch (error) {
          setMessage($("#memory-action-message"), error.message, true);
          button.disabled = false;
        }
      });
      item.append(button);
    }

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "btn btn-ghost btn-xs";
    delBtn.textContent = "Delete";
    delBtn.addEventListener("click", async () => {
      delBtn.disabled = true;
      try {
        const result = await api("/api/memory/note/delete", { method: "POST", body: JSON.stringify({ id: note.id }) });
        renderMemories(result.notes);
        showToast("Memory permanently deleted.");
      } catch (error) {
        setMessage($("#memory-action-message"), error.message, true);
        delBtn.disabled = false;
      }
    });
    item.append(delBtn);

    list.append(item);
  }
}

let archiveSignature = "";
async function loadReviewArchive() {
  const data = await api("/api/reviews");
  const signature = JSON.stringify([data.reports.map((item) => item.id), data.annual.map((item) => [item.year, item.created_at])]);
  if (signature === archiveSignature) return;
  archiveSignature = signature;
  const archive = $("#review-archive");
  archive.replaceChildren();
  for (const report of [...data.annual, ...data.reports]) {
    const entry = document.createElement("button");
    entry.type = "button";
    entry.className = "review-entry";
    const icon = document.createElement("span");
    icon.className = "review-entry-icon";
    icon.textContent = report.year || "AI";
    icon.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    const title = document.createElement("strong");
    title.textContent = report.year ? `${report.year} year in review` : `${report.period} coaching · ${report.start || report.created_at.slice(0, 10)}`;
    const detail = document.createElement("small");
    detail.textContent = report.year ? "Automatic annual review · Saved locally" : `${report.role} · ${PROVIDERS[report.provider]?.label || report.provider || "Coach"}`;
    text.append(title, detail);
    const arrow = document.createElement("span");
    arrow.textContent = "↗";
    arrow.setAttribute("aria-hidden", "true");
    entry.append(icon, text, arrow);
    entry.addEventListener("click", () => {
      $("#report-dialog-title").textContent = title.textContent;
      renderDigestMarkdown($("#saved-report-body"), report.body);
      $("#report-dialog").showModal();
    });
    archive.append(entry);
  }
  if (!archive.children.length) archive.textContent = "Your first coaching report or year-end review will be saved here.";
}

async function refresh(forceAll = false) {
  const status = await api("/api/status");
  const providerSelect = $("#provider");
  const modelInput = $("#model");
  const settingsAlreadyLoaded = providerSelect.dataset.ready === "true";
  const selectedProvider = providerSelect.value;
  const draftModel = modelInput.value;
  providerStatus = Object.fromEntries((status.providers || []).map((provider) => [provider.id, provider]));
  $("#watch-path").value = status.watch_path || $("#watch-path").value;
  if (status.local_endpoint && !$("#local-endpoint").dataset.edited) {
    $("#local-endpoint").value = status.local_endpoint;
  }
  if (!settingsAlreadyLoaded) {
    providerSelect.value = status.provider;
    modelInput.value = status.model;
    providerSelect.dataset.ready = "true";
  } else if (selectedProvider && providerStatus[selectedProvider]) {
    providerSelect.value = selectedProvider;
    providerStatus[selectedProvider].model = draftModel;
  }
  renderProvider(providerSelect.value, false);

  $("#data-dir").textContent = status.data_dir || "";
  $("#app-version").textContent = status.version ? `· version ${status.version}` : "";
  const desktop = status.desktop;
  $("#desktop-row").hidden = !(desktop && desktop.autostart_available);
  if (desktop) $("#autostart").checked = Boolean(desktop.autostart);

  $("#first-run-card").hidden = !status.first_run;

  if (!status.watch_path) {
    setState($("#path-state"), "Not set");
  } else if (!status.watch_path_exists) {
    setState($("#path-state"), "Path missing", "error");
  } else {
    setState($("#path-state"), status.watch_path_autodetected ? "Detected" : "Watching", "ready");
  }

  const dot = $("#watcher-dot");
  dot.className = `dot ${status.watcher_error ? "error" : status.watcher_running ? "ready" : ""}`;
  $("#watcher-label").textContent = status.watcher_error ? status.watcher_error : status.watcher_running ? "Local watcher running" : "Watcher stopped";
  updateSummary(status.summary);

  const changed = forceAll || status.data_revision !== lastRevision;
  lastRevision = status.data_revision;
  if (changed) {
    await loadIntelligence();
    await Promise.all([
      loadRuns(),
      loadLatestDigest(),
      loadDashboard(),
      loadRecords(),
      loadReviewArchive(),
      loadTraining(),
      loadCoachCenter(),
      loadHistoryTimeline(),
      loadBenchmarks(),
    ]);
    if (!memoryLoaded) await loadMemory();
  }
}

async function loadSampleAction() {
  const buttons = [$("#topbar-sample"), $("#first-run-sample"), $("#dialog-load-sample")].filter(Boolean);
  buttons.forEach((b) => { b.disabled = true; });
  try {
    const result = await api("/api/sample/load", { method: "POST", body: "{}" });
    if (result.sample_date) {
      $("#review-date").value = result.sample_date;
      $("#review-period").value = "monthly";
      document.querySelectorAll("[data-period]").forEach((item) => {
        const sel = item.dataset.period === "monthly";
        item.classList.toggle("btn-active", sel);
        item.setAttribute("aria-pressed", String(sel));
      });
    }
    await refresh(true);
    const msg = `Loaded ${result.runs_imported} sample run(s) (${result.date_range?.first} to ${result.date_range?.latest}). Navigated to ${result.sample_date}.`;
    setMessage($("#first-run-status"), msg);
    setMessage($("#import-message"), msg);
    showToast(msg);
    if ($("#connections-dialog").open) $("#connections-dialog").close();
  } catch (error) {
    setMessage($("#first-run-status"), error.message, true);
    setMessage($("#import-message"), error.message, true);
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

async function saveAndImport() {
  const button = $("#save-path");
  button.disabled = true;
  setMessage($("#import-message"), "Saving path and scanning...");
  $("#import-diagnostics").hidden = true;
  try {
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        watch_path: $("#watch-path").value,
        provider: $("#provider").value,
        model: $("#model").value,
        local_endpoint: $("#local-endpoint").value,
      }),
    });
    const result = await api("/api/import", { method: "POST", body: JSON.stringify({ force: true }) });
    const changed = result.imports.reduce((sum, item) => sum + item.inserted + item.updated, 0);
    const scanned = result.imports.reduce((sum, item) => sum + item.rows_seen, 0);
    const message = !result.imports.length
      ? "Path saved. No CSV files were found."
      : changed
        ? `Scan complete: ${changed} run${changed === 1 ? "" : "s"} added or updated.`
        : `Scan complete: ${scanned} run${scanned === 1 ? "" : "s"} checked; no changes.`;
    setMessage($("#import-message"), message);
    const rs = result.report_summary || {};
    const skipParts = Object.entries(rs.reasons || {}).map(([k, v]) => `${k}: ${v}`);
    if (rs.unsupported_perf_message) skipParts.push(rs.unsupported_perf_message);
    if (skipParts.length) {
      $("#import-diagnostics").textContent = `Diagnostics: ${skipParts.join(" · ")}`;
      $("#import-diagnostics").hidden = false;
    }
    await refresh(true);
  } catch (error) {
    setMessage($("#import-message"), error.message, true);
  } finally {
    button.disabled = false;
  }
}

async function detectPath() {
  const buttons = [$("#detect-path"), $("#first-run-detect")].filter(Boolean);
  buttons.forEach((b) => { b.disabled = true; });
  setMessage($("#import-message"), "Looking through your Steam libraries...");
  setMessage($("#first-run-status"), "Searching Steam libraries...");
  try {
    const { folders } = await api("/api/detect", { method: "POST", body: "{}" });
    if (!folders.length) {
      const noneMsg = "No KovaaK's stats folder was found. Paste its path in Connections or try Load Sample.";
      setMessage($("#import-message"), noneMsg, true);
      setMessage($("#first-run-status"), noneMsg, true);
      return;
    }
    $("#watch-path").value = folders[0];
    const others = folders.length > 1 ? ` (${folders.length - 1} other found)` : "";
    const foundMsg = `Found ${folders[0]}${others}. Choose Save & scan in Connections to import.`;
    setMessage($("#import-message"), foundMsg);
    setMessage($("#first-run-status"), foundMsg);
  } catch (error) {
    setMessage($("#import-message"), error.message, true);
    setMessage($("#first-run-status"), error.message, true);
  } finally {
    buttons.forEach((b) => { b.disabled = false; });
  }
}

async function toggleAutostart() {
  const toggle = $("#autostart");
  try {
    const result = await api("/api/desktop/autostart", { method: "POST", body: JSON.stringify({ enabled: toggle.checked }) });
    toggle.checked = result.autostart;
  } catch (error) {
    toggle.checked = !toggle.checked;
    setMessage($("#import-message"), error.message, true);
  }
}

async function connectModel() {
  const button = $("#connect-model");
  button.disabled = true;
  clearModelDiagnostics();
  setMessage($("#model-message"), "Testing model connection...");
  try {
    const selectedProvider = $("#provider").value;
    const settings = await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        watch_path: $("#watch-path").value,
        provider: selectedProvider,
        model: $("#model").value,
        local_endpoint: $("#local-endpoint").value,
      }),
    });
    if (settings.model_provider_api_version !== MODEL_PROVIDER_API_VERSION || settings.provider !== selectedProvider) {
      throw new Error("The running backend predates provider support. Restart Kovaak Agent, then reconnect your key.");
    }
    const key = $("#api-key").value.trim();
    if (key) {
      await api("/api/model/key", { method: "POST", body: JSON.stringify({ provider: selectedProvider, api_key: key }) });
      $("#api-key").value = "";
    }
    const result = await api("/api/model/test", { method: "POST", body: "{}" });
    setMessage($("#model-message"), result.message);
    await refresh(true);
  } catch (error) {
    setMessage($("#model-message"), error.message, true);
    showModelDiagnostics(error.diagnostics);
  } finally {
    button.disabled = false;
  }
}

async function generateDigest() {
  const button = $("#generate-digest");
  const digest = $("#digest");
  button.disabled = true;
  latestDigestLoaded = true;
  const selection = { period: $("#review-period").value, date: $("#review-date").value, role_id: $("#agent-role").value };
  clearModelDiagnostics();
  setDigestLayout(false);
  $("#digest-meta").hidden = true;
  $("#digest-title").textContent = "Building your coaching report";
  digest.classList.remove("digest-report", "empty");
  digest.textContent = "Reviewing this period alongside your saved goals, past advice and long-term history…";
  try {
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({ watch_path: $("#watch-path").value, provider: $("#provider").value, model: $("#model").value }),
    });
    const result = await api("/api/digest", { method: "POST", body: JSON.stringify(selection) });
    renderDigestReport(result);
    await loadReviewArchive();
  } catch (error) {
    $("#digest-title").textContent = "What to train next";
    digest.classList.remove("digest-report");
    digest.classList.add("empty");
    digest.textContent = error.message;
    showModelDiagnostics(error.diagnostics);
  } finally {
    button.disabled = false;
  }
}

function escapeHtml(str) {
  return String(str ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Date & toolbar events
const today = new Date();
$("#review-date").value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
for (const id of ["#review-period", "#review-date"]) {
  $(id).addEventListener("change", () => {
    loadDashboard().catch((error) => setMessage($("#dashboard-message"), error.message, true));
    loadRecords().catch((error) => setMessage($("#records-message"), error.message, true));
    loadIntelligence().catch(() => {});
  });
}

// Trend selectors
for (const id of ["#trend-scenario", "#trend-days", "#trend-metric"]) {
  $(id).addEventListener("change", () => loadScenarioTrend().catch(() => {}));
}

// Tag editor
$("#tag-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const scenario = $("#tag-scenario-name").dataset.scenario;
  const cats = [...document.querySelectorAll("#tag-checkboxes input:checked")].map((cb) => cb.value);
  try {
    await api("/api/taxonomy/tag", { method: "POST", body: JSON.stringify({ scenario, categories: cats }) });
    $("#tag-dialog").close();
    await refresh(true);
    showToast(`Updated skill tags for ${scenario}.`);
  } catch (error) {
    setMessage($("#tag-message"), error.message, true);
  }
});
$("#reset-tags").addEventListener("click", async () => {
  const scenario = $("#tag-scenario-name").dataset.scenario;
  try {
    await api("/api/taxonomy/tag", { method: "POST", body: JSON.stringify({ scenario, reset: true }) });
    $("#tag-dialog").close();
    await refresh(true);
    showToast(`Restored automatic tags for ${scenario}.`);
  } catch (error) {
    setMessage($("#tag-message"), error.message, true);
  }
});

// Readiness & Training plan
$("#readiness-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const payload = {
      energy: Number($("#rd-energy").value),
      focus: Number($("#rd-focus").value),
      fatigue: Number($("#rd-fatigue").value),
      eye_strain: Number($("#rd-eye").value),
      wrist_discomfort: Number($("#rd-wrist").value),
      available_minutes: $("#rd-minutes").value ? Number($("#rd-minutes").value) : null,
      check_date: $("#review-date").value,
    };
    const res = await api("/api/readiness", { method: "POST", body: JSON.stringify(payload) });
    $("#readiness-badge").textContent = `Checked in (${res.checkin.check_date})`;
    setMessage($("#readiness-message"), "Check-in saved.");
  } catch (error) {
    setMessage($("#readiness-message"), error.message, true);
  }
});

$("#generate-plan").addEventListener("click", async () => {
  const btn = $("#generate-plan");
  btn.disabled = true;
  setMessage($("#plan-message"), "Building adaptive routine...");
  try {
    const readiness = {
      energy: Number($("#rd-energy").value),
      focus: Number($("#rd-focus").value),
      fatigue: Number($("#rd-fatigue").value),
      eye_strain: Number($("#rd-eye").value),
      wrist_discomfort: Number($("#rd-wrist").value),
      available_minutes: $("#rd-minutes").value ? Number($("#rd-minutes").value) : null,
      check_date: $("#review-date").value,
    };
    const res = await api("/api/plans/generate", {
      method: "POST",
      body: JSON.stringify({ mode: $("#plan-mode").value, date: $("#review-date").value, readiness }),
    });
    renderPlan(res.plan);
    setMessage($("#plan-message"), "Routine generated.");
    await loadCoachCenter();
  } catch (error) {
    setMessage($("#plan-message"), error.message, true);
  } finally {
    btn.disabled = false;
  }
});

$("#evaluate-plan").addEventListener("click", async () => {
  if (!activePlanId) return;
  const btn = $("#evaluate-plan");
  btn.disabled = true;
  try {
    const res = await api("/api/plans/evaluate", {
      method: "POST",
      body: JSON.stringify({ plan_id: activePlanId, subjective_feedback: $("#plan-feedback").value }),
    });
    renderPlan(res.plan);
    await loadCoachCenter();
    showToast("Session evaluated and recorded.");
  } catch (error) {
    showToast(error.message);
  } finally {
    btn.disabled = false;
  }
});

// Structured Coaching Brief & Ask Q&A
async function runStructuredBrief(useLlm) {
  const btn = useLlm ? $("#generate-brief-llm") : $("#generate-brief-local");
  btn.disabled = true;
  try {
    const brief = await api("/api/coach/generate", {
      method: "POST",
      body: JSON.stringify({
        period: $("#review-period").value,
        date: $("#review-date").value,
        role_id: $("#agent-role").value,
        use_llm: useLlm,
        fallback_on_error: true,
      }),
    });
    renderCoachingBrief(brief);
    await loadCoachCenter();
  } catch (error) {
    $("#brief-warnings").hidden = false;
    $("#brief-warnings").textContent = error.message;
  } finally {
    btn.disabled = false;
  }
}
$("#generate-brief-local").addEventListener("click", () => runStructuredBrief(false));
$("#generate-brief-llm").addEventListener("click", () => runStructuredBrief(true));

$("#coach-ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#coach-question").value.trim();
  if (!q) return;
  const box = $("#coach-answer");
  box.hidden = false;
  box.textContent = "Checking your training history...";
  try {
    const res = await api("/api/coach/ask", {
      method: "POST",
      body: JSON.stringify({ question: q, date: $("#review-date").value, use_llm: true }),
    });
    box.replaceChildren();
    const p = document.createElement("p");
    p.textContent = res.answer;
    box.append(p);
    if (res.supporting_facts?.length) {
      const ul = document.createElement("ul");
      for (const f of res.supporting_facts) {
        const li = document.createElement("li");
        li.textContent = f;
        ul.append(li);
      }
      box.append(ul);
    }
  } catch (error) {
    box.textContent = error.message;
  }
});
document.querySelectorAll(".qa-chip").forEach((chip) => {
  chip.addEventListener("click", () => {
    $("#coach-question").value = chip.dataset.q;
    $("#coach-ask-form").dispatchEvent(new Event("submit"));
  });
});

// Hardware form
$("#hardware-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    await api("/api/hardware", {
      method: "POST",
      body: JSON.stringify({
        observed_at: $("#hw-date").value.replace("T", " "),
        change_type: $("#hw-type").value,
        summary: $("#hw-summary").value,
        note: $("#hw-note").value,
      }),
    });
    $("#hardware-dialog").close();
    $("#hardware-form").reset();
    await refresh(true);
    showToast("Hardware change saved.");
  } catch (error) {
    setMessage($("#hardware-message"), error.message, true);
  }
});

// Benchmark form
$("#fill-benchmark-template").addEventListener("click", () => {
  if (benchmarkTemplateCache) {
    $("#benchmark-json").value = JSON.stringify(benchmarkTemplateCache, null, 2);
  }
});
$("#benchmark-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  try {
    const payload = JSON.parse($("#benchmark-json").value);
    await api("/api/benchmarks", { method: "POST", body: JSON.stringify(payload) });
    $("#benchmark-dialog").close();
    await loadBenchmarks();
    showToast("Benchmark imported.");
  } catch (error) {
    setMessage($("#benchmark-message"), error.message, true);
  }
});

// Memory & Role forms
$("#memory-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  try {
    const data = await api("/api/memory/note", {
      method: "POST",
      body: JSON.stringify({ ...(editingMemory || {}), kind: $("#memory-kind").value, body: $("#memory-body").value, scenario: $("#memory-scenario").value }),
    });
    renderMemories(data.notes);
    editingMemory = null;
    $("#cancel-memory-edit").hidden = true;
    $("#memory-body").value = "";
    setMessage($("#memory-message"), "Memory saved for every agent and future session.");
    $("#memory-dialog").close();
    showToast("Memory saved. Every coach can use it.");
  } catch (error) {
    setMessage($("#memory-message"), error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#role-edit").addEventListener("change", () => {
  const role = savedRoles.find((item) => item.id === $("#role-edit").value);
  $("#role-name").value = role?.name || "";
  $("#role-instructions").value = role?.instructions || "";
});

$("#role-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const button = event.currentTarget.querySelector("button");
  button.disabled = true;
  try {
    const role = await api("/api/memory/role", {
      method: "POST",
      body: JSON.stringify({ id: $("#role-edit").value, name: $("#role-name").value, instructions: $("#role-instructions").value }),
    });
    await loadMemory();
    $("#agent-role").value = role.id;
    $("#role-edit").value = role.id;
    localStorage.setItem("kovaak-agent-role", role.id);
    setMessage($("#role-message"), "Role saved.");
    $("#role-dialog").close();
    showToast("Role saved and selected for your next review.");
  } catch (error) {
    setMessage($("#role-message"), error.message, true);
  } finally {
    button.disabled = false;
  }
});

$("#agent-role").addEventListener("change", () => localStorage.setItem("kovaak-agent-role", $("#agent-role").value));
$("#show-archived").addEventListener("change", () => renderMemories(savedNotes));
$("#cancel-memory-edit").addEventListener("click", () => {
  editingMemory = null;
  $("#memory-body").value = "";
  $("#memory-scenario").value = "";
  $("#cancel-memory-edit").hidden = true;
  $("#memory-dialog").close();
});
$("#annual-enabled").addEventListener("change", async () => {
  try {
    await api("/api/reviews/schedule", { method: "POST", body: JSON.stringify({ enabled: $("#annual-enabled").checked }) });
    await loadDashboard();
  } catch (error) {
    setMessage($("#schedule-message"), error.message, true);
  }
});

$("#save-path").addEventListener("click", saveAndImport);
$("#detect-path").addEventListener("click", detectPath);
if ($("#first-run-detect")) $("#first-run-detect").addEventListener("click", detectPath);
if ($("#topbar-sample")) $("#topbar-sample").addEventListener("click", loadSampleAction);
if ($("#first-run-sample")) $("#first-run-sample").addEventListener("click", loadSampleAction);
if ($("#dialog-load-sample")) $("#dialog-load-sample").addEventListener("click", loadSampleAction);
$("#autostart").addEventListener("change", toggleAutostart);
$("#connect-model").addEventListener("click", connectModel);
$("#generate-digest").addEventListener("click", generateDigest);
$("#refresh").addEventListener("click", () => refresh(true));
$("#provider").addEventListener("change", (event) => {
  event.target.dataset.ready = "true";
  renderProvider(event.target.value);
});
$("#model").addEventListener("input", (event) => {
  const provider = $("#provider").value;
  providerStatus[provider] = { ...(providerStatus[provider] || {}), model: event.target.value };
});
$("#local-endpoint").addEventListener("input", (event) => {
  event.target.dataset.edited = "true";
});
$("#theme-toggle").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

const workspaceViews = {
  progress: ["Your training, in focus.", "Evidence-driven aim analytics, adaptive training routines, and grounded coaching."],
  skills: ["Skill development & benchmarks.", "Category balance, normalized trends, hardware overlays, and milestone tracking."],
  training: ["Today's adaptive training.", "Readiness-aware playlists with numeric targets and post-session evaluation."],
  coach: ["Make the next session count.", "Coordinated analyst, technique, planner, and evaluator insights backed by your data."],
  session: ["Session timeline & hardware context.", "Grouped sessions, within-session drift, setup annotations, and recent runs."],
  memory: ["A coach that remembers.", "Your goals, preferences, hardware notes, and outcomes stay separate from measured stats."],
  reviews: ["See how far you’ve come.", "Revisit coaching reports, annual snapshots, and export aggregated analytics."],
};

function selectWorkspace(view, focus = false) {
  if (!workspaceViews[view]) view = "progress";
  for (const [key] of Object.entries(workspaceViews)) {
    const selected = key === view;
    $(`#view-${key}`).hidden = !selected;
    const tab = $(`#tab-${key}`);
    tab.classList.toggle("tab-active", selected);
    tab.setAttribute("aria-selected", String(selected));
    tab.tabIndex = selected ? 0 : -1;
  }
  $("#workspace-title").textContent = workspaceViews[view][0];
  $("#workspace-description").textContent = workspaceViews[view][1];
  $("#review-toolbar").hidden = !["progress", "skills", "training", "coach"].includes(view);
  if (focus) $(`#tab-${view}`).focus();
}

document.querySelectorAll("[data-view]").forEach((button) => button.addEventListener("click", (event) => {
  event.preventDefault();
  const view = button.dataset.view;
  selectWorkspace(view);
  if (location.hash !== `#${view}`) history.pushState(null, "", `#${view}`);
}));

$(".workspace-tabs").addEventListener("keydown", (event) => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [...document.querySelectorAll('.workspace-tabs [role="tab"]')];
  const index = tabs.indexOf(document.activeElement);
  if (index < 0) return;
  event.preventDefault();
  const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  tabs[next].click();
  tabs[next].focus();
});

window.addEventListener("popstate", () => selectWorkspace(location.hash.slice(1)));
window.addEventListener("hashchange", () => selectWorkspace(location.hash.slice(1)));
document.querySelectorAll("[data-modal]").forEach((button) => button.addEventListener("click", () => {
  const dlg = $(`#${button.dataset.modal}`);
  if (button.dataset.modal === "hardware-dialog" && !$("#hw-date").value) {
    const now = new Date();
    $("#hw-date").value = new Date(now.getTime() - now.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  }
  if (button.dataset.modal === "benchmark-dialog" && !$("#benchmark-json").value && benchmarkTemplateCache) {
    $("#benchmark-json").value = JSON.stringify(benchmarkTemplateCache, null, 2);
  }
  dlg.showModal();
}));

document.querySelectorAll("[data-period]").forEach((button) => button.addEventListener("click", () => {
  $("#review-period").value = button.dataset.period;
  document.querySelectorAll("[data-period]").forEach((item) => {
    const selected = item === button;
    item.classList.toggle("btn-active", selected);
    item.setAttribute("aria-pressed", String(selected));
  });
  $("#review-period").dispatchEvent(new Event("change"));
}));

$("#add-memory").addEventListener("click", () => {
  editingMemory = null;
  $("#memory-form").reset();
  $("#cancel-memory-edit").hidden = true;
  setMessage($("#memory-message"), "");
  $("#memory-dialog").showModal();
  $("#memory-body").focus();
});

let toastTimeout;
function showToast(message) {
  clearTimeout(toastTimeout);
  $("#ui-toast-text").textContent = message;
  $("#ui-toast").hidden = false;
  toastTimeout = setTimeout(() => { $("#ui-toast").hidden = true; }, 4500);
}

selectWorkspace(location.hash.slice(1));
applyTheme(document.documentElement.dataset.theme, false);
refresh(true).catch((error) => setMessage($("#import-message"), error.message, true));
setInterval(() => refresh(false).catch(() => {}), 5000);
