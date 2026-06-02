const config = window.BOARDLOG_CONFIG || {};
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
  return ($("endpointInput").value || config.defaultEndpoint || "").trim();
}

// The Function URL is public; the gate phrase and access key are sent as
// headers and verified server-side by the Lambda. The page stores no secrets.
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
}

function lock() {
  state.gate = "";
  sessionStorage.removeItem("boardlog:gate");
  sessionStorage.removeItem("boardlog:key");
  $("app").classList.add("is-hidden");
  $("gate").classList.remove("is-hidden");
  $("knockInput").value = "";
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

function normalizeRows(csvRows) {
  return csvRows.map((row) => {
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

function normalizeJsonRows(jsonRows) {
  return jsonRows.map((row) => {
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
      is_benchmark: Boolean(row.is_benchmark),
      tries: parseIntSafe(row.tries, 1),
      is_mirror: Boolean(row.is_mirror),
      is_repeat: Boolean(row.is_repeat),
      is_ascent: Boolean(row.is_ascent),
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
  state.rows = normalizeRows(parseCsv(text));
  loadRows(sourceLabel);
}

function loadJsonPayload(payload, sourceLabel) {
  const rows = Array.isArray(payload) ? payload : payload.rows;
  if (!Array.isArray(rows)) {
    throw new Error("Export response did not include a rows array.");
  }
  state.rows = normalizeJsonRows(rows);
  loadRows(sourceLabel);
}

function loadRows(sourceLabel) {
  const summary = summarize(state.rows);
  state.sessions = summary.sessions;
  state.gradeOrder = summary.gradeOrder;
  state.selectedIndex = Math.max(0, state.sessions.length - 1);
  if (!state.sessions.length) {
    throw new Error("No sessions found in that CSV.");
  }
  $("dashboard").classList.remove("is-hidden");
  setStatus(`Loaded ${state.rows.length} rows from ${sourceLabel}.`, "good");
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

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((item) => item.classList.remove("is-active"));
    tab.classList.add("is-active");
    document.querySelectorAll("[data-panel]").forEach((panel) => {
      panel.classList.toggle("is-hidden", panel.dataset.panel !== tab.dataset.tab);
    });
  });
});

$("apiForm").addEventListener("submit", async (event) => {
  event.preventDefault();
  const endpoint = currentEndpoint();
  if (!endpoint) {
    setStatus("Add a private export endpoint first.", "error");
    return;
  }
  const accessKey = $("accessKeyInput").value.trim();
  sessionStorage.setItem("boardlog:key", accessKey);
  setStatus("Requesting export...");
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
    if (!response.ok) throw new Error(`Export failed with HTTP ${response.status}`);
    const payload = await response.json();
    loadJsonPayload(payload, "private export endpoint");
  } catch (error) {
    setStatus(error.message, "error");
  } finally {
    $("passwordInput").value = "";
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
  $("accessKeyInput").value = storedKey;
}

const storedGate = sessionStorage.getItem("boardlog:gate");
if (storedGate) {
  unlock(storedGate).catch(() => lock());
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
