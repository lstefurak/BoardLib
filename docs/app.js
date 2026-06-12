const config = window.BOARDLOG_CONFIG || {};
const state = {
  gate: "",
  accessKey: "",
  rows: [],
  sessions: [],
  gradeOrder: [],
  selectedIndex: 0,
  sourceLabel: "",
};

const $ = (id) => document.getElementById(id);

function setStep(step) {
  document.body.dataset.step = step;
}

function updateKeyStatus() {
  $("keyStatus").textContent = state.accessKey ? "set" : "not set";
}

function updateStatusPill() {
  const pill = $("statusPill");
  if (!state.gate) {
    pill.textContent = "Locked";
  } else if (state.rows.length) {
    pill.textContent = `${state.rows.length} rows`;
  } else {
    pill.textContent = "Unlocked";
  }
}

function setStatus(message, tone = "muted") {
  const status = $("status");
  $("statusMessage").textContent = message;
  status.classList.remove("is-error", "is-good", "is-busy");
  if (tone === "error") status.classList.add("is-error");
  if (tone === "good") status.classList.add("is-good");
  if (tone === "busy") status.classList.add("is-busy");
}

function setFetchBusy(isBusy) {
  const submitButton = $("fetchButton");
  submitButton.disabled = isBusy;
  submitButton.classList.toggle("is-busy-button", isBusy);
  submitButton.querySelector("[data-button-label]").textContent = isBusy ? "Fetching..." : "Fetch Logbook";
}

function currentEndpoint() {
  return ($("endpointInput").value || config.defaultEndpoint || "").trim();
}

// The Function URL is public; the gate phrase and access key are sent as
// headers and verified server-side by the Lambda. The page keeps both secrets
// in sessionStorage for the lifetime of the tab (cleared by Lock) so a
// refresh doesn't force re-entry; nothing is persisted beyond the tab.
async function callBackend(endpoint, bodyObj, extraHeaders = {}) {
  return fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...extraHeaders },
    body: JSON.stringify(bodyObj),
  });
}

async function verifyGate(phrase) {
  const endpoint = currentEndpoint();
  // CSV-only use has no backend and nothing to protect, so open the door locally.
  if (!endpoint) return;
  const response = await callBackend(endpoint, { action: "unlock" }, { "X-Board-Gate": phrase });
  if (response.status === 403) throw new Error("The door stays shut.");
  if (!response.ok) throw new Error(`Gate check failed (HTTP ${response.status}).`);
}

async function unlock(phrase) {
  await verifyGate(phrase);
  state.gate = phrase;
  sessionStorage.setItem("boardlog:gate", phrase);
  $("gate").classList.add("is-hidden");
  $("app").classList.remove("is-hidden");
  setStep(state.rows.length ? "analyze" : "load");
  updateStatusPill();
}

function lock() {
  state.gate = "";
  state.accessKey = "";
  sessionStorage.removeItem("boardlog:gate");
  sessionStorage.removeItem("boardlog:key");
  $("app").classList.add("is-hidden");
  $("gate").classList.remove("is-hidden");
  $("knockInput").value = "";
  // Clear the secrets from the DOM too, or they stay one "Show" click away.
  $("keyInput").value = "";
  $("passwordInput").value = "";
  updateKeyStatus();
  setStep("unlock");
  updateStatusPill();
  if ($("keyDialog").open) $("keyDialog").close();
}

function parseCsv(text) {
  const rows = [];
  let field = "";
  let row = [];
  let quoted = false;
  for (let index = 0; index < text.length; index += 1) {
    const char = text[index];
    const next = text[index + 1];
    if (quoted && char === "\"" && next === "\"") {
      field += "\"";
      index += 1;
    } else if (char === "\"") {
      quoted = !quoted;
    } else if (!quoted && char === ",") {
      row.push(field);
      field = "";
    } else if (!quoted && (char === "\n" || char === "\r")) {
      if (char === "\r" && next === "\n") index += 1;
      row.push(field);
      if (row.some((value) => value.trim())) rows.push(row);
      row = [];
      field = "";
    } else {
      field += char;
    }
  }
  row.push(field);
  if (row.some((value) => value.trim())) rows.push(row);
  if (!rows.length) {
    throw new Error("That CSV file is empty.");
  }
  const headers = rows.shift().map((header) => header.trim());
  return rows.map((values) => Object.fromEntries(headers.map((header, index) => [header, values[index] || ""])));
}

function parseBool(value) {
  return ["1", "true", "yes", "y"].includes(String(value).trim().toLowerCase());
}

function parseIntSafe(value, fallback = 0) {
  const parsed = Number.parseInt(String(value), 10);
  return Number.isFinite(parsed) ? parsed : fallback;
}

function cleanText(value) {
  const text = String(value || "").trim();
  return text.toLowerCase() === "nan" ? "" : text;
}

// Normalizes rows from either source: CSV rows carry string flags ("true"),
// API JSON rows carry real booleans; parseBool handles both.
function normalizeRows(rawRows) {
  return rawRows.map((row) => {
    const date = new Date(cleanText(row.date).replace(" ", "T"));
    if (Number.isNaN(date.getTime())) return null;
    const loggedGrade = cleanText(row.logged_grade) || cleanText(row.displayed_grade) || "Ungraded";
    return {
      board: cleanText(row.board),
      angle: parseIntSafe(row.angle),
      climb_name: cleanText(row.climb_name),
      date: date.toISOString(),
      session_date: date.toISOString().slice(0, 10),
      logged_grade: loggedGrade,
      displayed_grade: cleanText(row.displayed_grade),
      is_benchmark: parseBool(row.is_benchmark),
      tries: parseIntSafe(row.tries, 1),
      is_mirror: parseBool(row.is_mirror),
      is_repeat: parseBool(row.is_repeat),
      is_ascent: parseBool(row.is_ascent),
      comment: cleanText(row.comment),
    };
  }).filter((row) => row && row.climb_name);
}

function gradeKey(grade) {
  const match = String(grade).match(/V(\d+)/);
  return match ? Number(match[1]) : 999;
}

function summarize(rows) {
  const byDate = new Map();
  rows.forEach((row) => {
    if (!byDate.has(row.session_date)) byDate.set(row.session_date, []);
    byDate.get(row.session_date).push(row);
  });

  const sessions = [...byDate.entries()].sort(([a], [b]) => a.localeCompare(b)).map(([date, sessionRows]) => {
    const ascents = sessionRows.filter((row) => row.is_ascent);
    const gradeCounts = {};
    ascents.forEach((row) => {
      gradeCounts[row.logged_grade] = (gradeCounts[row.logged_grade] || 0) + 1;
    });
    return {
      date,
      board: sessionRows[0]?.board || "",
      ascents: ascents.length,
      total_tries: sessionRows.reduce((sum, row) => sum + row.tries, 0),
      unique_climbs: new Set(ascents.map((row) => row.climb_name)).size,
      benchmarks: ascents.filter((row) => row.is_benchmark).length,
      repeats: ascents.filter((row) => row.is_repeat).length,
      angles: [...new Set(sessionRows.map((row) => row.angle))].sort((a, b) => a - b),
      grade_counts: Object.fromEntries(Object.entries(gradeCounts).sort(([a], [b]) => gradeKey(a) - gradeKey(b))),
    };
  });

  const gradeOrder = [...new Set(rows.filter((row) => row.is_ascent).map((row) => row.logged_grade))]
    .sort((a, b) => gradeKey(a) - gradeKey(b));

  return { sessions, gradeOrder };
}

function loadCsvText(text, sourceLabel) {
  loadRows(normalizeRows(parseCsv(text)), sourceLabel);
}

function loadJsonPayload(payload, sourceLabel) {
  const rows = Array.isArray(payload) ? payload : payload.rows;
  if (!Array.isArray(rows)) {
    throw new Error("Export response did not include a rows array.");
  }
  loadRows(normalizeRows(rows), sourceLabel);
}

function collapseConnectPanel() {
  $("connectBody").classList.add("is-hidden");
  $("connectSummary").classList.remove("is-hidden");
  $("connectSummaryText").textContent = `${state.rows.length} rows loaded from ${state.sourceLabel}.`;
}

function expandConnectPanel() {
  $("connectBody").classList.remove("is-hidden");
  $("connectSummary").classList.add("is-hidden");
  setStep("load");
}

function loadRows(rows, sourceLabel) {
  // Validate before touching state, so a failed load leaves the previously
  // rendered dashboard fully working instead of pointing at empty sessions.
  const summary = summarize(rows);
  if (!summary.sessions.length) {
    throw new Error("No sessions found in that CSV.");
  }
  state.rows = rows;
  state.sessions = summary.sessions;
  state.gradeOrder = summary.gradeOrder;
  state.selectedIndex = state.sessions.length - 1;
  state.sourceLabel = sourceLabel;
  $("dashboard").classList.remove("is-hidden");
  setStep("analyze");
  setStatus(`Loaded ${state.rows.length} rows from ${sourceLabel}.`, "good");
  collapseConnectPanel();
  updateStatusPill();
  renderSessionOptions();
  render();
}

function formatDate(value) {
  const date = new Date(`${value}T00:00:00`);
  return date.toLocaleDateString(undefined, { weekday: "short", year: "numeric", month: "short", day: "numeric" });
}

function formatTime(value) {
  return new Date(value).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
}

function currentSessionRows() {
  const session = state.sessions[state.selectedIndex];
  return state.rows.filter((row) => row.session_date === session.date).sort((a, b) => a.date.localeCompare(b.date));
}

function gradeSummaryText(session) {
  return state.gradeOrder
    .filter((grade) => session.grade_counts[grade])
    .map((grade) => `${grade} x${session.grade_counts[grade]}`)
    .join(", ") || "No sends logged";
}

function workoutText(session) {
  const angles = session.angles.length ? `${session.angles.join("/")} deg` : "angle unknown";
  const lines = [
    `${formatDate(session.date)} - ${session.board} @ ${angles}`,
    `${session.ascents} sends, ${session.total_tries} total tries, ${session.unique_climbs} unique climbs`,
    `Grades: ${gradeSummaryText(session)}`,
    `Benchmarks: ${session.benchmarks}; repeats: ${session.repeats}`,
  ];
  const comments = currentSessionRows()
    .filter((row) => row.comment)
    .map((row) => `- ${row.climb_name}: ${row.comment}`);
  if (comments.length) lines.push("Notes:", ...comments);
  return lines.join("\n");
}

function renderSessionOptions() {
  $("sessionSelect").innerHTML = state.sessions.map((session, index) => (
    `<option value="${index}">${formatDate(session.date)} (${session.ascents} sends)</option>`
  )).join("");
}

// Climb names, grades, and comments come from shared-board content authored by
// other users, so escape them before they go into innerHTML.
function escapeHtml(value) {
  return String(value ?? "").replace(
    /[&<>"']/g,
    (char) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]),
  );
}

function statIcon(name) {
  const paths = {
    sends: "<path d='M7 12l3 3 7-8'></path>",
    climbs: "<path d='M8 4v16M16 4v16M4 8h16M4 16h16'></path>",
    tries: "<path d='M4 12a8 8 0 111.9 5.2'></path><path d='M4 18v-6h6'></path>",
    benchmarks: "<path d='M12 3l2.7 5.5 6.1.9-4.4 4.3 1 6.1L12 16.9 6.6 19.8l1-6.1-4.4-4.3 6.1-.9L12 3z'></path>",
    repeats: "<path d='M17 2l4 4-4 4'></path><path d='M3 11V9a3 3 0 013-3h15'></path><path d='M7 22l-4-4 4-4'></path><path d='M21 13v2a3 3 0 01-3 3H3'></path>",
  };
  return `<svg class="stat-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name]}</svg>`;
}

function typeBadges(row) {
  const result = row.is_ascent
    ? "<span class='badge badge-send'>send</span>"
    : "<span class='badge badge-attempt'>attempt</span>";
  const benchmark = row.is_benchmark ? "<span class='badge badge-benchmark'>benchmark</span>" : "";
  return result + benchmark;
}

function emptyChartMarkup() {
  return `<div class="empty-chart">
    <svg viewBox="0 0 64 64" fill="none" stroke="currentColor" stroke-width="3" aria-hidden="true">
      <rect x="12" y="8" width="40" height="48" rx="7"></rect>
      <circle cx="24" cy="21" r="3"></circle>
      <circle cx="40" cy="23" r="4"></circle>
      <circle cx="29" cy="37" r="4"></circle>
      <circle cx="44" cy="44" r="3"></circle>
    </svg>
    <p class="hint">No ascents logged for this day.</p>
  </div>`;
}

function render() {
  const session = state.sessions[state.selectedIndex];
  $("sessionSelect").value = String(state.selectedIndex);
  $("sessionHeading").textContent = formatDate(session.date);

  const stats = [
    ["sends", "Ascents", session.ascents],
    ["climbs", "Unique climbs", session.unique_climbs],
    ["tries", "Total tries", session.total_tries],
    ["benchmarks", "Benchmarks", session.benchmarks],
    ["repeats", "Repeats", session.repeats],
  ];
  $("stats").innerHTML = stats.map(([icon, label, value]) => (
    `<div class="stat">${statIcon(icon)}<div><strong>${value}</strong><span>${label}</span></div></div>`
  )).join("");

  const maxCount = Math.max(1, ...Object.values(session.grade_counts));
  $("gradeChart").innerHTML = state.gradeOrder
    .filter((grade) => session.grade_counts[grade])
    .map((grade) => {
      const count = session.grade_counts[grade];
      const width = Math.max(4, Math.round((count / maxCount) * 100));
      return `<div class="bar-row">
        <div class="bar-label">${escapeHtml(grade)}</div>
        <div class="bar-track"><div class="bar" style="width:${width}%"></div></div>
        <div class="bar-count">${count}</div>
      </div>`;
    }).join("") || emptyChartMarkup();

  $("workoutLog").textContent = workoutText(session);
  $("climbRows").innerHTML = currentSessionRows().map((row) => (
    `<tr>
      <td>${formatTime(row.date)}</td>
      <td>${escapeHtml(row.climb_name)}${row.is_mirror ? " (mirror)" : ""}</td>
      <td>${escapeHtml(row.logged_grade)}</td>
      <td>${row.angle}</td>
      <td>${row.tries}</td>
      <td>${typeBadges(row)}</td>
    </tr>`
  )).join("");
}

function openKeyDialog(message) {
  if (message) setStatus(message, "error");
  $("keyInput").value = state.accessKey;
  $("keyDialog").showModal();
  $("keyInput").focus();
}

function setActiveTab(tabName) {
  document.querySelectorAll(".tab").forEach((tab) => {
    const isActive = tab.dataset.tab === tabName;
    tab.classList.toggle("is-active", isActive);
    tab.setAttribute("aria-selected", String(isActive));
    tab.tabIndex = isActive ? 0 : -1;
  });
  document.querySelectorAll("[data-panel]").forEach((panel) => {
    panel.classList.toggle("is-hidden", panel.dataset.panel !== tabName);
  });
}

$("knockForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await unlock($("knockInput").value.trim());
  } catch (error) {
    $("knockInput").value = "";
    $("knockInput").placeholder = error.message;
  }
});

$("lockButton").addEventListener("click", lock);
$("loadDifferentButton").addEventListener("click", expandConnectPanel);
$("changeKeyButton").addEventListener("click", () => openKeyDialog());
$("cancelKeyButton").addEventListener("click", () => $("keyDialog").close());

$("keyForm").addEventListener("submit", (event) => {
  event.preventDefault();
  state.accessKey = $("keyInput").value.trim();
  updateKeyStatus();
  $("keyDialog").close();
  setStatus(state.accessKey ? "Access key ready for the next export." : "Access key cleared.", "muted");
});

const tabs = Array.from(document.querySelectorAll(".tab"));
tabs.forEach((tab) => {
  tab.addEventListener("click", () => setActiveTab(tab.dataset.tab));
  tab.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const step = event.key === "ArrowRight" ? 1 : -1;
    const next = tabs[(tabs.indexOf(tab) + step + tabs.length) % tabs.length];
    setActiveTab(next.dataset.tab);
    next.focus();
  });
});

document.querySelectorAll("[data-reveal-for]").forEach((toggle) => {
  toggle.addEventListener("click", () => {
    const input = $(toggle.dataset.revealFor);
    if (!input) return;
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    toggle.textContent = reveal ? "Hide" : "Show";
  });
});

let exportInFlight = false;

$("apiForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  // One export at a time: concurrent requests race and the stale response
  // would overwrite the newer dashboard.
  if (exportInFlight) return;
  const endpoint = currentEndpoint();
  if (!endpoint) {
    setStatus("Add a private export endpoint first.", "error");
    return;
  }
  if (!state.accessKey) {
    openKeyDialog("Enter the Board Room access key before fetching.");
    return;
  }
  exportInFlight = true;
  setFetchBusy(true);
  setStatus("Requesting export...", "busy");
  try {
    const response = await callBackend(
      endpoint,
      {
        board: "tension",
        username: $("usernameInput").value.trim(),
        password: $("passwordInput").value,
      },
      { "X-Board-Gate": state.gate, "X-Board-Room-Key": state.accessKey },
    );
    if (response.status === 403) {
      sessionStorage.removeItem("boardlog:key");
      throw new Error("Backend rejected the gate phrase or access key (403).");
    }
    if (response.status === 401) {
      throw new Error("Board login failed; check your username and password.");
    }
    if (!response.ok) throw new Error(`Export failed with HTTP ${response.status}`);
    // Only remember the key once the backend has accepted it.
    sessionStorage.setItem("boardlog:key", state.accessKey);
    const payload = await response.json();
    loadJsonPayload(payload, "private export endpoint");
  } catch (error) {
    setStatus(error.message, "error");
    if (String(error.message).includes("access key")) {
      state.accessKey = "";
      updateKeyStatus();
      openKeyDialog(error.message);
    }
  } finally {
    $("passwordInput").value = "";
    exportInFlight = false;
    setFetchBusy(false);
  }
});

$("csvForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const file = $("csvInput").files[0];
  if (!file) {
    setStatus("Choose a CSV file first.", "error");
    return;
  }
  try {
    loadCsvText(await file.text(), file.name);
  } catch (error) {
    setStatus(error.message, "error");
  }
});

$("csvInput").addEventListener("change", () => {
  const file = $("csvInput").files[0];
  if (file) setStatus(`${file.name} selected.`, "muted");
});

["dragenter", "dragover"].forEach((eventName) => {
  $("csvDropZone").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("csvDropZone").classList.add("is-dragging");
  });
});

["dragleave", "drop"].forEach((eventName) => {
  $("csvDropZone").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("csvDropZone").classList.remove("is-dragging");
  });
});

$("csvDropZone").addEventListener("drop", async (event) => {
  const file = event.dataTransfer.files[0];
  if (!file) return;
  try {
    loadCsvText(await file.text(), file.name);
  } catch (error) {
    setStatus(error.message, "error");
  }
});

$("sessionSelect").addEventListener("change", (event) => {
  state.selectedIndex = Number(event.target.value);
  render();
});

$("prevDay").addEventListener("click", () => {
  state.selectedIndex = Math.max(0, state.selectedIndex - 1);
  render();
});

$("nextDay").addEventListener("click", () => {
  state.selectedIndex = Math.min(state.sessions.length - 1, state.selectedIndex + 1);
  render();
});

$("copyLog").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("workoutLog").textContent);
  $("copyLog").textContent = "Copied";
  setTimeout(() => {
    $("copyLog").textContent = "Copy";
  }, 1200);
});

if (config.defaultEndpoint) {
  $("endpointInput").value = config.defaultEndpoint;
}

const storedKey = sessionStorage.getItem("boardlog:key");
if (storedKey) {
  state.accessKey = storedKey;
  updateKeyStatus();
}

updateStatusPill();

const storedGate = sessionStorage.getItem("boardlog:gate");
if (storedGate) {
  unlock(storedGate).catch(() => lock());
}
