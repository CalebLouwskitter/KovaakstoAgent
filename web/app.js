/**
 * Kovaak Agent - Frontend Application Script
 *
 * Manages:
 * - Local-first UI state: theme (light/dark), watcher status, metrics, and recent runs table.
 * - Provider configuration: OpenAI, OpenRouter, and Google Gemini session-only key testing.
 * - Coaching report rendering: parses AI markdown output into structured report sections.
 * - Background synchronization: polls the backend status endpoint every 5 seconds.
 */

const $ = (selector) => document.querySelector(selector);
const THEME_KEY = "kovaak-agent-theme";
const MODEL_PROVIDER_API_VERSION = 1;

// Provider catalog defaults and placeholders
const PROVIDERS = {
  openai: {
    label: "OpenAI",
    defaultModel: "gpt-5.4-mini",
    keyPlaceholder: "sk-...",
    hint: "Uses the OpenAI Responses API.",
  },
  openrouter: {
    label: "OpenRouter",
    defaultModel: "~openai/gpt-latest",
    keyPlaceholder: "sk-or-v1-...",
    hint: "Uses OpenRouter's OpenAI-compatible chat completions API and supports its full model catalog.",
  },
  gemini: {
    label: "Google Gemini",
    defaultModel: "gemini-3.7-flash",
    keyPlaceholder: "AIza...",
    hint: "Uses Gemini's native Interactions API.",
  },
};

let providerStatus = {};
let latestDigestLoaded = false;
let memoryLoaded = false;
let savedRoles = [];
let savedNotes = [];
let editingMemory = null;
let dashboardRequest = 0;

/**
 * Toggle between light and dark themes, updating DOM dataset and persisting to localStorage.
 */
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

/**
 * Wrapper around window.fetch with JSON headers, error handling, and diagnostics extraction.
 */
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

/**
 * Format a number using locale formatting with a maximum decimal precision.
 */
function number(value, digits = 1) {
  if (value === null || value === undefined) return "—";
  return new Intl.NumberFormat(undefined, { maximumFractionDigits: digits }).format(value);
}

/**
 * Format duration in seconds to 'Xm Ys' or 'X.Y sec'.
 */
function duration(value) {
  if (value === null || value === undefined) return "—";
  if (value >= 60) {
    const minutes = Math.floor(value / 60);
    const seconds = Math.round(value % 60);
    return seconds ? `${minutes}m ${seconds}s` : `${minutes} min`;
  }
  return `${number(value, 1)} sec`;
}

/**
 * Shorten game version strings to the first 3 segment parts.
 */
function buildLabel(value) {
  if (!value) return "—";
  const parts = value.split(".");
  return parts.length > 3 ? parts.slice(0, 3).join(".") : value;
}

/**
 * Format timestamps into human-readable short dates (e.g. 'Aug 30, 06:05 PM').
 */
function shortDate(value, assumeUtc = false) {
  if (!value) return "No data yet";
  const normalized = assumeUtc && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(value)
    ? `${value.replace(" ", "T")}Z`
    : value;
  const parsed = new Date(normalized);
  if (Number.isNaN(parsed.valueOf())) return value;
  return parsed.toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

/**
 * Update message banner element with status or error text.
 */
function setMessage(element, text, error = false) {
  element.textContent = text;
  element.classList.toggle("error", error);
}

/**
 * Hide provider diagnostics disclosure panel and clear JSON text.
 */
function clearModelDiagnostics() {
  const details = $("#model-diagnostics");
  details.hidden = true;
  details.open = false;
  $("#model-diagnostics-output").textContent = "";
  $("#provider-diagnostics-link").hidden = true;
}

/**
 * Populate and expand provider diagnostics panel with redacted error payload.
 */
function showModelDiagnostics(diagnostics) {
  if (!diagnostics || typeof diagnostics !== "object") return;
  const details = $("#model-diagnostics");
  const provider = String(diagnostics.provider || "").toLowerCase();
  $("#provider-diagnostics-link").hidden = provider !== "openrouter";
  $("#model-diagnostics-output").textContent = JSON.stringify(diagnostics, null, 2);
  details.hidden = false;
  details.open = true;
}

/**
 * Parse bold (**text**), italic (*text*), and code (`text`) inline tokens into DOM nodes.
 */
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

/**
 * Render structured Markdown coaching report into styled sections, lists, and headings.
 */
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
    if (!trimmed) {
      resetLists();
      continue;
    }
    if (/^-{3,}$/.test(trimmed)) {
      resetLists();
      continue;
    }

    // Section headings (H2, H3, H4)
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

    // Unordered or ordered list items
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

    // Regular paragraphs
    resetLists();
    const paragraph = document.createElement("p");
    appendInlineMarkdown(paragraph, trimmed);
    ensureSection().append(paragraph);
  }
}

/**
 * Toggle CSS classes on the performance grid when a digest report is present.
 */
function setDigestLayout(hasReport) {
  $(".performance-grid").classList.toggle("report-ready", hasReport);
  $("#digest-panel").classList.toggle("has-report", hasReport);
}

/**
 * Display a generated or cached coaching report in the coaching card.
 */
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

/**
 * Fetch and display the latest cached report from SQLite if available.
 */
async function loadLatestDigest() {
  if (latestDigestLoaded) return;
  const { report } = await api("/api/digest/latest");
  latestDigestLoaded = true;
  if (report) renderDigestReport(report);
}

/**
 * Set text and visual badge tone ('ready', 'error', or default ghost) for a status element.
 */
function setState(element, text, kind = "") {
  element.textContent = text;
  const tone = kind === "ready" ? "badge-success badge-soft" : kind === "error" ? "badge-error badge-soft" : "badge-ghost";
  element.className = `badge state-badge ${tone}`;
}

/**
 * Update UI labels, model defaults, placeholders, and connection badge for the active provider.
 */
function renderProvider(providerId, updateModel = true) {
  const definition = PROVIDERS[providerId] || PROVIDERS.openai;
  const status = providerStatus[providerId];
  $("#model-label").textContent = `${definition.label} model`;
  $("#api-key-label").innerHTML = `${definition.label} API key <span class="quiet">session only</span>`;
  $("#api-key").placeholder = definition.keyPlaceholder;
  $("#provider-hint").textContent = `${definition.hint} Coaching shares selected-period statistics, up to 20 runs, 24 relevant memories, 5 prior reports and lifetime summaries for up to 40 scenarios. Raw CSV data and local file paths stay on this computer.`;
  if (updateModel) $("#model").value = status?.model || definition.defaultModel;
  const connected = Boolean(status?.api_key_connected);
  setState($("#model-state"), connected ? "Connected" : "Disconnected", connected ? "ready" : "");
}

/**
 * Populate cockpit metric cards, shot conversion bar, and settings detail grid.
 */
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

    // Hit-to-miss conversion bar calculation
    const conversion = latest.accuracy == null ? 0 : Math.max(0, Math.min(100, latest.accuracy));
    $("#conversion-label").textContent = latest.hits == null || latest.misses == null
      ? "No shot data"
      : `${number(latest.hits, 0)} landed · ${number(latest.misses, 0)} missed`;
    $("#conversion-fill").style.width = `${conversion}%`;

    // Aim settings grid
    $("#run-weapon").textContent = latest.weapon || "—";
    $("#run-sensitivity").textContent = latest.sensitivity == null
      ? "—"
      : `${number(latest.sensitivity, 2)}${latest.sensitivity_scale ? ` ${latest.sensitivity_scale}` : ""}`;
    $("#run-dpi").textContent = number(latest.dpi, 0);
    $("#run-fov").textContent = latest.fov == null
      ? "—"
      : `${number(latest.fov, 1)}${latest.fov_scale ? ` · ${latest.fov_scale}` : ""}`;
    $("#run-resolution").textContent = latest.resolution || "—";
    $("#run-version").textContent = buildLabel(latest.game_version);
  }

  if (summary.latest_source?.imported_at) {
    $("#last-sync").textContent = `Last import ${shortDate(summary.latest_source.imported_at, true)} · ${summary.latest_source.rows_seen} run${summary.latest_source.rows_seen === 1 ? "" : "s"}`;
  }
}

/**
 * Fetch and render up to 18 recent runs into the scenario history table.
 */
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

/**
 * Synchronize UI with backend status: watcher state, path validation, summary metrics, and table.
 */
async function refresh() {
  const status = await api("/api/status");
  const providerSelect = $("#provider");
  const modelInput = $("#model");
  const settingsAlreadyLoaded = providerSelect.dataset.ready === "true";
  const selectedProvider = providerSelect.value;
  const draftModel = modelInput.value;
  providerStatus = Object.fromEntries((status.providers || []).map((provider) => [provider.id, provider]));
  $("#watch-path").value = status.watch_path || $("#watch-path").value;
  if (!settingsAlreadyLoaded) {
    providerSelect.value = status.provider;
    modelInput.value = status.model;
    providerSelect.dataset.ready = "true";
  } else if (selectedProvider && providerStatus[selectedProvider]) {
    providerSelect.value = selectedProvider;
    providerStatus[selectedProvider].model = draftModel;
  }
  renderProvider(providerSelect.value, false);

  if (!status.watch_path) {
    setState($("#path-state"), "Not set");
  } else if (!status.watch_path_exists) {
    setState($("#path-state"), "Path missing", "error");
  } else {
    setState($("#path-state"), "Watching", "ready");
  }

  const dot = $("#watcher-dot");
  dot.className = `dot ${status.watcher_error ? "error" : status.watcher_running ? "ready" : ""}`;
  $("#watcher-label").textContent = status.watcher_error ? status.watcher_error : status.watcher_running ? "Local watcher running" : "Watcher stopped";
  updateSummary(status.summary);
  await loadRuns();
  await loadLatestDigest();
  await Promise.all([loadDashboard(), loadReviewArchive()]);
  if (!memoryLoaded) await loadMemory();
}

/**
 * Save updated watch path and trigger a manual force-scan of all CSV files in the path.
 */
async function saveAndImport() {
  const button = $("#save-path");
  button.disabled = true;
  setMessage($("#import-message"), "Saving path and scanning...");
  try {
    await api("/api/settings", {
      method: "POST",
      body: JSON.stringify({
        watch_path: $("#watch-path").value,
        provider: $("#provider").value,
        model: $("#model").value,
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
    await refresh();
  } catch (error) {
    setMessage($("#import-message"), error.message, true);
  } finally {
    button.disabled = false;
  }
}

/**
 * Submit in-memory session key for the selected provider and test connectivity with a ping prompt.
 */
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
      }),
    });
    if (
      settings.model_provider_api_version !== MODEL_PROVIDER_API_VERSION
      || settings.provider !== selectedProvider
    ) {
      throw new Error("The running backend predates provider support. Restart Kovaak Agent, then reconnect your key.");
    }
    const key = $("#api-key").value.trim();
    if (key) {
      await api("/api/model/key", {
        method: "POST",
        body: JSON.stringify({ provider: selectedProvider, api_key: key }),
      });
      $("#api-key").value = "";
    }
    const result = await api("/api/model/test", { method: "POST", body: "{}" });
    setMessage($("#model-message"), result.message);
    await refresh();
  } catch (error) {
    setMessage($("#model-message"), error.message, true);
    showModelDiagnostics(error.diagnostics);
  } finally {
    button.disabled = false;
  }
}

/**
 * Request an LLM coaching synthesis over the 7-day evidence window and render the resulting Markdown.
 */
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
  digest.classList.remove("digest-report");
  digest.classList.remove("empty");
  digest.textContent = "Reviewing this period alongside your saved goals, past advice and long-term history…";
  try {
    await api("/api/settings", { method: "POST", body: JSON.stringify({ watch_path: $("#watch-path").value, provider: $("#provider").value, model: $("#model").value }) });
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
  const table = $("#period-scenarios");
  table.replaceChildren();
  for (const item of result.scenarios) {
    const row = document.createElement("tr");
    const delta = item.score_change_percent == null ? "No baseline" : `${item.score_change_percent > 0 ? "+" : ""}${number(item.score_change_percent)}% (${item.previous_runs} prior runs)`;
    for (const value of [item.scenario, number(item.runs, 0), number(item.average_score), number(item.best_score), item.average_accuracy == null ? "—" : `${number(item.average_accuracy)}%`, delta]) {
      const cell = document.createElement("td"); cell.textContent = value; row.append(cell);
    }
    table.append(row);
  }
  if (!result.scenarios.length) {
    const row = document.createElement("tr"); const cell = document.createElement("td");
    cell.colSpan = 6; cell.textContent = "No captured runs in this period. Choose an earlier date or import your stats."; row.append(cell); table.append(row);
  }
  const chart = $("#activity-chart"); chart.replaceChildren();
  const maximum = Math.max(1, ...result.timeline.map(item => item.runs));
  for (const item of result.timeline) {
    const column = document.createElement("div"); column.className = "activity-column";
    const count = document.createElement("span"); count.textContent = item.runs;
    const bar = document.createElement("div"); bar.className = "activity-bar"; bar.style.height = `${Math.max(2, item.runs / maximum * 65)}px`;
    const label = document.createElement("span"); label.textContent = item.bucket.slice(5);
    column.title = `${item.bucket}: ${item.runs} runs`;
    column.append(count, bar, label); chart.append(column);
  }
  if (!result.timeline.length) chart.textContent = "Your training activity will appear here.";
  $("#annual-enabled").checked = result.schedule.enabled;
  $("#annual-schedule").textContent = result.schedule.enabled ? `Scheduled: ${result.schedule.next_due.slice(0, 10)} at 00:00, computer local time. ${result.schedule.error ? `Review error: ${result.schedule.error}` : ""}` : "Automatic year-end reviews are paused.";
}

async function loadMemory() {
  const data = await api("/api/memory");
  savedRoles = data.roles;
  const selected = localStorage.getItem("kovaak-agent-role") || $("#agent-role").value;
  const edit = $("#role-edit").value;
  for (const id of ["#agent-role", "#role-edit"]) {
    const select = $(id); select.replaceChildren();
    if (id === "#role-edit") select.add(new Option("Create new role", ""));
    for (const role of data.roles) select.add(new Option(role.name, role.id));
  }
  $("#agent-role").value = data.roles.some(role => role.id === selected) ? selected : "coach";
  $("#role-edit").value = edit;
  renderMemories(data.notes);
  const catalog = $("#role-catalog");
  catalog.replaceChildren(...data.roles.map(role => {
    const item = document.createElement("div"); item.className = "role-summary";
    const title = document.createElement("strong"); title.textContent = role.name;
    const description = document.createElement("p"); description.className = "hint"; description.textContent = role.instructions;
    item.append(title, description); return item;
  }));
  memoryLoaded = true;
}

function renderMemories(notes) {
  savedNotes = notes;
  const list = $("#memory-list"); list.replaceChildren();
  const visible = notes.filter(note => $("#show-archived").checked || note.status !== "archived");
  if (!visible.length) list.textContent = "No memories yet. Start with your long-term goal.";
  for (const note of visible) {
    const item = document.createElement("div"); item.className = "memory-item";
    const label = document.createElement("small"); label.textContent = `${note.kind} · ${note.status}${note.scenario ? ` · ${note.scenario}` : ""} · saved locally`;
    const text = document.createElement("p"); text.textContent = note.body; item.append(label, text);
    const edit = document.createElement("button"); edit.type = "button"; edit.className = "btn btn-ghost btn-xs"; edit.textContent = "Edit";
    edit.addEventListener("click", () => {
      editingMemory = note; $("#memory-kind").value = note.kind; $("#memory-body").value = note.body;
      $("#memory-scenario").value = note.scenario; $("#cancel-memory-edit").hidden = false;
      $("#memory-dialog").showModal();
      $("#memory-body").focus();
    });
    item.append(edit);
    const actions = note.status === "archived" ? [["Restore", "active"]] : [[note.status === "completed" ? "Reopen" : "Complete", note.status === "completed" ? "active" : "completed"], ["Archive", "archived"]];
    for (const [caption, status] of actions) {
      const button = document.createElement("button"); button.type = "button"; button.className = "btn btn-ghost btn-xs"; button.textContent = caption;
      button.addEventListener("click", async () => {
        button.disabled = true;
        try { const result = await api("/api/memory/note", { method: "POST", body: JSON.stringify({ ...note, status }) }); renderMemories(result.notes); }
        catch (error) { setMessage($("#memory-action-message"), error.message, true); button.disabled = false; }
      });
      item.append(button);
    }
    list.append(item);
  }
}

let archiveSignature = "";
async function loadReviewArchive() {
  const data = await api("/api/reviews");
  const signature = JSON.stringify([data.reports.map(item => item.id), data.annual.map(item => [item.year, item.created_at])]);
  if (signature === archiveSignature) return;
  archiveSignature = signature;
  const archive = $("#review-archive"); archive.replaceChildren();
  for (const report of [...data.annual, ...data.reports]) {
    const entry = document.createElement("button"); entry.type = "button"; entry.className = "review-entry";
    const icon = document.createElement("span"); icon.className = "review-entry-icon"; icon.textContent = report.year || "AI"; icon.setAttribute("aria-hidden", "true");
    const text = document.createElement("span");
    const title = document.createElement("strong"); title.textContent = report.year ? `${report.year} year in review` : `${report.period} coaching · ${report.start || report.created_at.slice(0,10)}`;
    const detail = document.createElement("small"); detail.textContent = report.year ? "Automatic annual review · Saved locally" : `${report.role} · ${PROVIDERS[report.provider]?.label || report.provider || "Coach"}`;
    text.append(title, detail);
    const arrow = document.createElement("span"); arrow.textContent = "↗"; arrow.setAttribute("aria-hidden", "true");
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

const today = new Date();
$("#review-date").value = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, "0")}-${String(today.getDate()).padStart(2, "0")}`;
for (const id of ["#review-period", "#review-date"]) $(id).addEventListener("change", () => loadDashboard().catch(error => setMessage($("#dashboard-message"), error.message, true)));
$("#memory-form").addEventListener("submit", async event => {
  event.preventDefault(); const button = event.currentTarget.querySelector("button"); button.disabled = true;
  try {
    const data = await api("/api/memory/note", { method: "POST", body: JSON.stringify({ ...(editingMemory || {}), kind: $("#memory-kind").value, body: $("#memory-body").value, scenario: $("#memory-scenario").value }) });
    renderMemories(data.notes); editingMemory = null; $("#cancel-memory-edit").hidden = true; $("#memory-body").value = ""; setMessage($("#memory-message"), "Memory saved for every agent and future session.");
    $("#memory-dialog").close(); showToast("Memory saved. Every coach can use it.");
  } catch (error) { setMessage($("#memory-message"), error.message, true); }
  finally { button.disabled = false; }
});
$("#role-edit").addEventListener("change", () => {
  const role = savedRoles.find(item => item.id === $("#role-edit").value);
  $("#role-name").value = role?.name || ""; $("#role-instructions").value = role?.instructions || "";
});
$("#role-form").addEventListener("submit", async event => {
  event.preventDefault(); const button = event.currentTarget.querySelector("button"); button.disabled = true;
  try {
    const role = await api("/api/memory/role", { method: "POST", body: JSON.stringify({ id: $("#role-edit").value, name: $("#role-name").value, instructions: $("#role-instructions").value }) });
    await loadMemory(); $("#agent-role").value = role.id; $("#role-edit").value = role.id;
    localStorage.setItem("kovaak-agent-role", role.id);
    setMessage($("#role-message"), "Role saved. Shared player memory carries over automatically.");
    $("#role-dialog").close(); showToast("Role saved and selected for your next review.");
  } catch (error) { setMessage($("#role-message"), error.message, true); }
  finally { button.disabled = false; }
});
$("#agent-role").addEventListener("change", () => localStorage.setItem("kovaak-agent-role", $("#agent-role").value));
$("#show-archived").addEventListener("change", () => renderMemories(savedNotes));
$("#cancel-memory-edit").addEventListener("click", () => { editingMemory = null; $("#memory-body").value = ""; $("#memory-scenario").value = ""; $("#cancel-memory-edit").hidden = true; $("#memory-dialog").close(); });
$("#annual-enabled").addEventListener("change", async () => {
  try { await api("/api/reviews/schedule", { method: "POST", body: JSON.stringify({ enabled: $("#annual-enabled").checked }) }); await loadDashboard(); }
  catch (error) { setMessage($("#schedule-message"), error.message, true); }
});

// Event listeners for user actions
$("#save-path").addEventListener("click", saveAndImport);
$("#connect-model").addEventListener("click", connectModel);
$("#generate-digest").addEventListener("click", generateDigest);
$("#refresh").addEventListener("click", refresh);
$("#provider").addEventListener("change", (event) => {
  event.target.dataset.ready = "true";
  renderProvider(event.target.value);
});
$("#model").addEventListener("input", (event) => {
  const provider = $("#provider").value;
  providerStatus[provider] = { ...(providerStatus[provider] || {}), model: event.target.value };
});
$("#theme-toggle").addEventListener("click", () => {
  applyTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});

// Initial boot sequence and periodic 5-second polling interval
const workspaceViews = {
  progress: ["Your training, in focus.", "Spot the changes. Keep the context. Know what to train next."],
  session: ["The details behind the score.", "Your latest run, settings and recent attempts in one place."],
  coach: ["Make the next session count.", "Turn your training history into a clear, measurable next step."],
  memory: ["A coach that remembers.", "Your goals, preferences and outcomes follow you from session to session."],
  reviews: ["See how far you’ve come.", "Revisit your coaching reports and each year of progress."],
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
  $("#review-toolbar").hidden = !["progress", "coach"].includes(view);
  if (focus) $(`#tab-${view}`).focus();
}
document.querySelectorAll("[data-view]").forEach(button => button.addEventListener("click", event => {
  event.preventDefault();
  const view = button.dataset.view;
  selectWorkspace(view);
  if (location.hash !== `#${view}`) history.pushState(null, "", `#${view}`);
}));
$(".workspace-tabs").addEventListener("keydown", event => {
  if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
  const tabs = [...document.querySelectorAll('.workspace-tabs [role="tab"]')];
  const index = tabs.indexOf(document.activeElement);
  if (index < 0) return;
  event.preventDefault();
  const next = event.key === "Home" ? 0 : event.key === "End" ? tabs.length - 1 : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
  tabs[next].click(); tabs[next].focus();
});
window.addEventListener("popstate", () => selectWorkspace(location.hash.slice(1)));
window.addEventListener("hashchange", () => selectWorkspace(location.hash.slice(1)));
document.querySelectorAll("[data-modal]").forEach(button => button.addEventListener("click", () => $(`#${button.dataset.modal}`).showModal()));
document.querySelectorAll("[data-period]").forEach(button => button.addEventListener("click", () => {
  $("#review-period").value = button.dataset.period;
  document.querySelectorAll("[data-period]").forEach(item => {
    const selected = item === button;
    item.classList.toggle("btn-active", selected); item.setAttribute("aria-pressed", String(selected));
  });
  $("#review-period").dispatchEvent(new Event("change"));
}));
$("#add-memory").addEventListener("click", () => {
  editingMemory = null; $("#memory-form").reset(); $("#cancel-memory-edit").hidden = true;
  setMessage($("#memory-message"), ""); $("#memory-dialog").showModal(); $("#memory-body").focus();
});
let toastTimeout;
function showToast(message) {
  clearTimeout(toastTimeout); $("#ui-toast-text").textContent = message; $("#ui-toast").hidden = false;
  toastTimeout = setTimeout(() => { $("#ui-toast").hidden = true; }, 4500);
}
selectWorkspace(location.hash.slice(1));
applyTheme(document.documentElement.dataset.theme, false);
refresh().catch((error) => setMessage($("#import-message"), error.message, true));
setInterval(() => refresh().catch(() => {}), 5000);
