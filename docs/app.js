const config = window.BOARDLOG_CONFIG || {};
// Local dev overrides (gitignored docs/site.config.local.js, generated from
// .env by scripts/serve_local.py). Absent in production.
const local = window.BOARDLOG_LOCAL || {};
const IS_LOCAL = ["localhost", "127.0.0.1", "[::1]", ""].includes(location.hostname);
const state = {
  gate: "",
  rows: [],
  sessions: [],
  gradeOrder: [],
  selectedIndex: 0,
};

const $ = (id) => document.getElementById(id);

function setStatus(message, tone = "muted") {
  const status = $("status");
  status.textContent = message;
  status.style.color = tone === "error" ? "#8a1f11" : tone === "good" ? "#0f766e" : "";
}

function currentEndpoint() {
  const typed = $("endpointInput").value.trim();
  if (typed) return typed;
  // On localhost, don't silently fall back to the production Function URL — a
  // cross-origin call to it just CORS-fails. Use a local override only if one
  // was supplied; otherwise run backend-free (gate opens, CSV still works).
  if (IS_LOCAL) return (local.endpoint || "").trim();
  return (config.defaultEndpoint || "").trim();
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
  try {
    const response = await callBackend(endpoint, { action: "unlock" }, { "X-Board-Gate": phrase });
    if (response.status === 403) throw new Error("The door stays shut.");
    if (!response.ok) throw new Error(`Gate check failed (HTTP ${response.status}).`);
  } catch (error) {
    // From localhost the Function URL is cross-origin and the browser blocks the
    // response (CORS) — fetch rejects with a TypeError before we see a status.
    // That is expected in local dev, so open the door instead of trapping the
    // tester at the gate. A genuine 403 (handled above) still throws in prod.
    if (IS_LOCAL && error instanceof TypeError) return;
    throw error;
  }
}

async function unlock(phrase) {
  await verifyGate(phrase);
  state.gate = phrase;
  sessionStorage.setItem("boardlog:gate", phrase);
  $("gate").classList.add("is-hidden");
  $("app").classList.remove("is-hidden");
}

function lock() {
  state.gate = "";
  sessionStorage.removeItem("boardlog:gate");
  sessionStorage.removeItem("boardlog:key");
  $("app").classList.add("is-hidden");
  $("gate").classList.remove("is-hidden");
  // Reset the app layout so the next unlock starts at the fetch form.
  $("connect").classList.remove("is-hidden");
  $("dashboard").classList.add("is-hidden");
  $("knockInput").value = "";
  // Clear the secrets from the DOM too, or they stay one "Show" click away,
  // and return any revealed field to dots.
  $("accessKeyInput").value = "";
  $("passwordInput").value = "";
  hideSecret($("accessKeyInput"));
  hideSecret($("passwordInput"));
  hideSecret($("knockInput"));
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
// API JSON rows carry real booleans — parseBool handles both.
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
  const rawRows = parseCsv(text);
  loadRows(normalizeRows(rawRows), sourceLabel, rawRows);
}

function loadJsonPayload(payload, sourceLabel) {
  const rows = Array.isArray(payload) ? payload : payload.rows;
  if (!Array.isArray(rows)) {
    throw new Error("Export response did not include a rows array.");
  }
  loadRows(normalizeRows(rows), sourceLabel, rows);
}

function loadRows(rows, sourceLabel, rawRows) {
  // Validate before touching state, so a failed load leaves the previously
  // rendered dashboard fully working instead of pointing at empty sessions.
  const summary = summarize(rows);
  if (!summary.sessions.length) {
    throw new Error("No sessions found in that CSV.");
  }
  state.rows = rows;
  // Keep the source rows verbatim so Export CSV round-trips faithfully.
  state.rawRows = rawRows || rows;
  state.sessions = summary.sessions;
  state.gradeOrder = summary.gradeOrder;
  state.selectedIndex = state.sessions.length - 1;
  renderSessionOptions();
  render();

  // Data is in: focus the dashboard and tuck the fetch form away. The header's
  // "Load different data" brings it back.
  $("sourceLabel").textContent = `Loaded ${state.rows.length} rows from ${sourceLabel}.`;
  $("dashboard").classList.remove("is-hidden");
  $("connect").classList.add("is-hidden");
  setStatus(`Loaded ${state.rows.length} rows from ${sourceLabel}.`, "good");
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function csvCell(value) {
  if (value === null || value === undefined) return "";
  // Source rows from the API carry real booleans; BoardLib CSVs use True/False.
  let text = typeof value === "boolean" ? (value ? "True" : "False") : String(value);
  // Neutralize CSV/formula injection: climb names and comments are board/user-
  // authored, so a cell starting with = + - @ (or tab/CR) could be run as a
  // formula when the export is opened in Excel/Sheets. Prefix a single quote to
  // force the spreadsheet to treat it as literal text.
  if (/^[=+\-@\t\r]/.test(text)) text = `'${text}`;
  return /[",\r\n]/.test(text) ? `"${text.replace(/"/g, '""')}"` : text;
}

// Re-serialize the verbatim source rows so the export round-trips to a real
// BoardLib CSV (including columns the dashboard itself doesn't use).
function rowsToCsv(rows) {
  const headers = [];
  const seen = new Set();
  rows.forEach((row) => Object.keys(row).forEach((key) => {
    if (!seen.has(key)) {
      seen.add(key);
      headers.push(key);
    }
  }));
  const lines = [headers.map(csvCell).join(",")];
  rows.forEach((row) => lines.push(headers.map((key) => csvCell(row[key])).join(",")));
  return lines.join("\r\n");
}

function exportCsv() {
  if (!state.rawRows || !state.rawRows.length) return;
  const board = (state.rows[0] && state.rows[0].board) || "boardlog";
  const blob = new Blob([rowsToCsv(state.rawRows)], { type: "text/csv;charset=utf-8" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = `${board}-logbook.csv`;
  document.body.appendChild(link);
  link.click();
  link.remove();
  setTimeout(() => URL.revokeObjectURL(url), 0);
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

function render() {
  const session = state.sessions[state.selectedIndex];
  $("sessionSelect").value = String(state.selectedIndex);

  const stats = [
    ["Ascents", session.ascents],
    ["Unique climbs", session.unique_climbs],
    ["Total tries", session.total_tries],
    ["Benchmarks", session.benchmarks],
    ["Repeats", session.repeats],
  ];
  $("stats").innerHTML = stats.map(([label, value]) => (
    `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`
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
    }).join("") || "<p class='hint'>No ascents logged for this day.</p>";

  $("workoutLog").textContent = workoutText(session);
  $("climbRows").innerHTML = currentSessionRows().map((row) => (
    `<tr>
      <td>${formatTime(row.date)}</td>
      <td>${escapeHtml(row.climb_name)}${row.is_mirror ? " (mirror)" : ""}</td>
      <td>${escapeHtml(row.logged_grade)}</td>
      <td>${row.angle}</td>
      <td>${row.tries}</td>
      <td>${row.is_ascent ? "send" : "attempt"}${row.is_benchmark ? ", benchmark" : ""}</td>
    </tr>`
  )).join("");
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

// Return a password field to dots and reset its reveal toggle (the toggle is
// inserted as the input's next sibling further down).
function hideSecret(input) {
  if (!input) return;
  input.type = "password";
  const toggle = input.nextElementSibling;
  if (toggle && toggle.classList.contains("reveal-toggle")) toggle.textContent = "Show";
}

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
  const accessKey = $("accessKeyInput").value.trim();
  const submitButton = $("apiForm").querySelector('button[type="submit"]');
  const submitLabel = submitButton && submitButton.querySelector(".btn-label");
  exportInFlight = true;
  if (submitButton) {
    submitButton.disabled = true;
    submitButton.classList.add("is-busy");
  }
  if (submitLabel) submitLabel.textContent = "Loading";
  // Tick a live elapsed counter so a slow board export still feels alive.
  const startedAt = Date.now();
  const ticker = setInterval(() => {
    const seconds = Math.round((Date.now() - startedAt) / 1000);
    setStatus(`Requesting export... ${seconds}s`);
  }, 1000);
  setStatus("Requesting export... 0s");
  try {
    const response = await callBackend(
      endpoint,
      {
        board: "tension",
        username: $("usernameInput").value.trim(),
        password: $("passwordInput").value,
      },
      { "X-Board-Gate": state.gate, "X-Board-Room-Key": accessKey },
    );
    if (response.status === 403) {
      throw new Error("Backend rejected the gate phrase or access key (403).");
    }
    if (response.status === 401) {
      throw new Error("Board login failed; check your username and password.");
    }
    if (!response.ok) throw new Error(`Export failed with HTTP ${response.status}`);
    // Only remember the key once the backend has accepted it.
    sessionStorage.setItem("boardlog:key", accessKey);
    const payload = await response.json();
    loadJsonPayload(payload, "remote storage");
    // Clear the password only after a successful load. On failure, keep it so
    // the user can retry without retyping.
    $("passwordInput").value = "";
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    clearInterval(ticker);
    // Whatever happened, return the password field to dots (it may have been
    // revealed via "Show"); the typed value is preserved unless we cleared it.
    hideSecret($("passwordInput"));
    exportInFlight = false;
    if (submitButton) {
      submitButton.disabled = false;
      submitButton.classList.remove("is-busy");
    }
    if (submitLabel) submitLabel.textContent = "Fetch Logs";
  }
});

// Make the Load CSV button look armed once a file is picked.
$("csvInput").addEventListener("change", () => {
  const button = $("csvForm").querySelector('button[type="submit"]');
  if (button) button.classList.toggle("is-ready", $("csvInput").files.length > 0);
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

$("exportCsvButton").addEventListener("click", exportCsv);

$("loadDifferentButton").addEventListener("click", () => {
  $("connect").classList.remove("is-hidden");
  $("connect").scrollIntoView({ behavior: "smooth", block: "start" });
});

$("copyLog").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("workoutLog").textContent);
  $("copyLog").textContent = "Copied";
  setTimeout(() => {
    $("copyLog").textContent = "Copy";
  }, 1200);
});

// Prefill the endpoint: a local override on localhost, the shipped default in
// production. Leaving it blank locally keeps the page from calling production.
const prefillEndpoint = IS_LOCAL ? local.endpoint : config.defaultEndpoint;
if (prefillEndpoint) {
  $("endpointInput").value = prefillEndpoint;
}

// Prefill the gate phrase (a cached one from this tab, or the .env knock in
// local dev) so a returning visitor only presses Enter. We deliberately do NOT
// auto-unlock below: entering the room is always an explicit action.
const cachedGate = sessionStorage.getItem("boardlog:gate");
if (cachedGate) {
  $("knockInput").value = cachedGate;
} else if (IS_LOCAL && local.knock) {
  $("knockInput").value = local.knock;
}

const storedKey = sessionStorage.getItem("boardlog:key");
if (storedKey) {
  $("accessKeyInput").value = storedKey;
} else {
  // First visit (no remembered key): open Room Access so the endpoint and key
  // fields are visible. Once a key sticks, this stays collapsed and the user
  // just types username/password.
  $("roomAccess").open = true;
}

// Add a show/hide toggle to every password field.
document.querySelectorAll('input[type="password"]').forEach((input) => {
  const toggle = document.createElement("button");
  toggle.type = "button";
  toggle.className = "reveal-toggle";
  toggle.textContent = "Show";
  toggle.setAttribute("aria-label", "Show or hide the value");
  toggle.addEventListener("click", () => {
    const reveal = input.type === "password";
    input.type = reveal ? "text" : "password";
    toggle.textContent = reveal ? "Hide" : "Show";
  });
  input.insertAdjacentElement("afterend", toggle);
});
