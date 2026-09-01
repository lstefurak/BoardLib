const config = window.BOARDLOG_CONFIG || {};
// Local dev overrides (gitignored docs/site.config.local.js, generated from
// .env by scripts/serve_local.py). Absent in production.
const local = window.BOARDLOG_LOCAL || {};
const IS_LOCAL = ["localhost", "127.0.0.1", "[::1]", ""].includes(location.hostname);
const state = {
  session: "",
  rows: [],
  sessions: [],
  gradeOrder: [],
  selectedIndex: 0,
  climbs: [],
  view: "sessions",
  climbFilter: "all",
  climbQuery: "",
  climbSort: "recent",
  climbSortDirection: "desc",
  // Selected angles (empty = any angle). Rebuilt from the data on every load.
  climbAngles: new Set(),
  groupClimbVariants: true,
};

const $ = (id) => document.getElementById(id);

function setStatus(message, tone = "muted") {
  const status = $("status");
  status.textContent = message;
  status.style.color = tone === "error" ? "#8a1f11" : tone === "good" ? "#0f766e" : "";
}

// The backend URL ships in site.config.js; it is public, not a secret.
function currentEndpoint() {
  // On localhost, don't silently fall back to the production Function URL — a
  // cross-origin call to it just CORS-fails. Use a local override only if one
  // was supplied; otherwise run backend-free (gate opens, CSV still works).
  if (IS_LOCAL) return (local.endpoint || "").trim();
  return (config.defaultEndpoint || "").trim();
}

// The Function URL is public. The knock (gate phrase) is sent once, as a
// header, and verified server-side by the Lambda, which answers with a
// short-lived session token. Only that token is kept — in sessionStorage for
// the lifetime of the tab, cleared by Lock — so a refresh doesn't force
// re-entry. The page never stores the phrase and never sees the backend
// access key at all.
const SESSION_STORAGE_KEY = "boardlog:session";
const KNOCK_PLACEHOLDER = "say the quiet part";

class SessionExpiredError extends Error {}

async function callBackend(endpoint, bodyObj, extraHeaders = {}) {
  return fetch(endpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...extraHeaders },
    body: JSON.stringify(bodyObj),
  });
}

// Ask the backend to verify the knock. Resolves to a session token, or "" when
// there is no backend to talk to (CSV-only use, or backend-free local dev).
async function verifyGate(phrase) {
  const endpoint = currentEndpoint();
  // CSV-only use has no backend and nothing to protect, so open the door locally.
  if (!endpoint) return "";
  try {
    const response = await callBackend(endpoint, { action: "unlock" }, { "X-Board-Gate": phrase });
    if (response.status === 403) throw new Error("The door stays shut.");
    if (!response.ok) throw new Error(`Gate check failed (HTTP ${response.status}).`);
    const payload = await response.json().catch(() => ({}));
    return typeof payload.session === "string" ? payload.session : "";
  } catch (error) {
    // From localhost the Function URL is cross-origin and the browser blocks the
    // response (CORS) — fetch rejects with a TypeError before we see a status.
    // That is expected in local dev, so open the door instead of trapping the
    // tester at the gate. A genuine 403 (handled above) still throws in prod.
    if (IS_LOCAL && error instanceof TypeError) return "";
    throw error;
  }
}

// Tokens are "<unix expiry>.<signature>". The expiry is readable so the page
// can drop a stale token without a round trip; only the backend checks the
// signature.
function sessionExpiresAt(token) {
  const seconds = Number.parseInt(String(token || "").split(".")[0], 10);
  return Number.isFinite(seconds) ? seconds * 1000 : 0;
}

function sessionIsLive(token) {
  return Boolean(token) && sessionExpiresAt(token) > Date.now();
}

function enterRoom() {
  $("gate").classList.add("is-hidden");
  $("app").classList.remove("is-hidden");
}

async function unlock(phrase) {
  const token = await verifyGate(phrase);
  state.session = token;
  if (token) sessionStorage.setItem(SESSION_STORAGE_KEY, token);
  else sessionStorage.removeItem(SESSION_STORAGE_KEY);
  // The phrase has done its job; don't leave it one "Show" click away.
  $("knockInput").value = "";
  $("knockInput").placeholder = KNOCK_PLACEHOLDER;
  enterRoom();
}

function lock() {
  state.session = "";
  sessionStorage.removeItem(SESSION_STORAGE_KEY);
  $("app").classList.add("is-hidden");
  $("gate").classList.remove("is-hidden");
  // Reset the app layout so the next unlock starts at the fetch form.
  $("connect").classList.remove("is-hidden");
  $("dashboard").classList.add("is-hidden");
  $("knockInput").value = "";
  $("knockInput").placeholder = KNOCK_PLACEHOLDER;
  // Clear the password from the DOM too, or it stays one "Show" click away,
  // and return any revealed field to dots.
  $("passwordInput").value = "";
  hideSecret($("passwordInput"));
  hideSecret($("knockInput"));
}

// The backend refused the session (it expired, or a secret was rotated). Send
// the user back to the gate with a hint rather than a dead-end error.
function expireSession() {
  lock();
  $("knockInput").placeholder = "session expired — knock again";
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

// Optional numeric columns (community stats) may be absent in older CSVs, so
// missing/unparseable values become null rather than 0.
function parseNumOrNull(value) {
  const parsed = Number.parseFloat(String(value));
  return Number.isFinite(parsed) ? parsed : null;
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
      climb_uuid: cleanText(row.climb_uuid),
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
      ascensionist_count: parseNumOrNull(row.ascensionist_count),
      quality_average: parseNumOrNull(row.quality_average),
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
      unique_climbs: new Set(ascents.map(climbGroupKey)).size,
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

function climbGroupKey(row) {
  return row.climb_uuid || row.climb_name.toLowerCase();
}

function climbVariantKey(row) {
  return `${row.is_mirror ? "mirror" : "original"}|${row.angle}`;
}

function summarizeVariant(entries) {
  entries.sort((a, b) => a.date.localeCompare(b.date));
  const sends = entries.filter((entry) => entry.is_ascent);
  let triesToFirstSend = 0;
  let firstSend = null;
  for (const entry of entries) {
    triesToFirstSend += entry.tries;
    if (entry.is_ascent) {
      firstSend = entry;
      break;
    }
  }
  const latest = entries[entries.length - 1];
  const gradeSource = sends.length ? sends[sends.length - 1] : latest;
  return {
    name: entries[0].climb_name,
    mirror: entries[0].is_mirror,
    angle: entries[0].angle,
    grade: gradeSource.logged_grade,
    benchmark: entries.some((entry) => entry.is_benchmark),
    sends: sends.length,
    totalTries: entries.reduce((sum, entry) => sum + entry.tries, 0),
    triesToFirstSend: firstSend ? triesToFirstSend : null,
    sessions: new Set(entries.map((entry) => entry.session_date)).size,
    firstSendDate: firstSend ? firstSend.session_date : null,
    lastClimbed: latest.session_date,
    flashed: entries[0].is_ascent && entries[0].tries === 1,
    repeated: sends.length >= 2,
    communitySends: latest.ascensionist_count,
    quality: latest.quality_average,
    entries,
  };
}

function summarizeClimbGroup(entries) {
  entries.sort((a, b) => a.date.localeCompare(b.date));
  const byVariant = new Map();
  entries.forEach((entry) => {
    const key = climbVariantKey(entry);
    if (!byVariant.has(key)) byVariant.set(key, []);
    byVariant.get(key).push(entry);
  });
  const variants = [...byVariant.values()].map(summarizeVariant).sort((a, b) => (
    a.angle - b.angle || Number(a.mirror) - Number(b.mirror)
  ));
  const sends = entries.filter((entry) => entry.is_ascent);
  let triesToFirstSend = 0;
  let firstGroupSend = null;
  for (const entry of entries) {
    triesToFirstSend += entry.tries;
    if (entry.is_ascent) {
      firstGroupSend = entry;
      break;
    }
  }
  const latest = entries[entries.length - 1];
  const sentVariants = variants.filter((variant) => variant.sends > 0);
  const bestVariant = sentVariants
    .filter((variant) => gradeRank(variant.grade) >= 0)
    .sort((a, b) => gradeRank(b.grade) - gradeRank(a.grade))[0]
    || sentVariants[sentVariants.length - 1]
    || variants[variants.length - 1];
  const communityValues = variants
    .map((variant) => variant.communitySends)
    .filter((value) => value !== null);
  const qualityValues = variants
    .map((variant) => variant.quality)
    .filter((value) => value !== null);

  return {
    name: entries[0].climb_name,
    mirror: variants.some((variant) => variant.mirror),
    angle: Math.min(...variants.map((variant) => variant.angle)),
    angles: [...new Set(variants.map((variant) => variant.angle))],
    grade: bestVariant.grade,
    benchmark: variants.some((variant) => variant.benchmark),
    sends: sends.length,
    totalTries: entries.reduce((sum, entry) => sum + entry.tries, 0),
    triesToFirstSend: firstGroupSend ? triesToFirstSend : null,
    sessions: new Set(entries.map((entry) => entry.session_date)).size,
    firstSendDate: firstGroupSend ? firstGroupSend.session_date : null,
    lastClimbed: latest.session_date,
    flashed: variants.some((variant) => variant.flashed),
    repeated: sends.length >= 2,
    communitySends: communityValues.length ? Math.max(...communityValues) : null,
    quality: qualityValues.length ? qualityValues.reduce((sum, value) => sum + value, 0) / qualityValues.length : null,
    variants,
    entries,
  };
}

// Per-climb send history. The default groups mirror and angle variants under a
// stable board climb id, falling back to name for older CSV exports.
function summarizeClimbs(rows) {
  const byClimb = new Map();
  rows.forEach((row) => {
    const key = state.groupClimbVariants ? climbGroupKey(row) : `${climbGroupKey(row)}|${climbVariantKey(row)}`;
    if (!byClimb.has(key)) byClimb.set(key, []);
    byClimb.get(key).push(row);
  });

  return [...byClimb.values()].map(summarizeClimbGroup);
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
  state.climbs = summarizeClimbs(rows);
  state.climbAngles = new Set();
  renderSessionOptions();
  render();
  renderAngleFilters();
  renderClimbs();

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

function climbMatchesFilter(climb, filter) {
  switch (filter) {
    case "sent": return climb.sends > 0;
    case "project": return climb.sends === 0;
    case "repeated": return climb.repeated;
    case "flashed": return climb.flashed;
    case "benchmark": return climb.benchmark;
    default: return true;
  }
}

// gradeKey returns 999 for grades it can't parse; keep those at the bottom of
// a hardest-first sort instead of letting them win it.
function gradeRank(grade) {
  const key = gradeKey(grade);
  return key === 999 ? -1 : key;
}

// Every column header sorts by its own key (the th's data-sort). The first
// click uses the column's natural direction; a second click reverses it. A null
// key (no send yet, no community data, unparseable grade) sinks to the bottom
// in both directions, and ties fall back to name, then angle.
const CLIMB_COLUMNS = {
  name: { key: (climb) => climb.name, defaultDirection: "asc" },
  grade: { key: (climb) => (gradeRank(climb.grade) < 0 ? null : gradeRank(climb.grade)), defaultDirection: "desc" },
  // With variants grouped this is the lowest angle in the group.
  angle: { key: (climb) => climb.angle, defaultDirection: "asc" },
  sends: { key: (climb) => climb.sends, defaultDirection: "desc" },
  tries: { key: (climb) => climb.totalTries, defaultDirection: "desc" },
  sessions: { key: (climb) => climb.sessions, defaultDirection: "desc" },
  firstSend: { key: (climb) => climb.firstSendDate, defaultDirection: "desc" },
  recent: { key: (climb) => climb.lastClimbed, defaultDirection: "desc" },
  communitySends: { key: (climb) => climb.communitySends, defaultDirection: "desc" },
  quality: { key: (climb) => climb.quality, defaultDirection: "desc" },
};

function compareValues(a, b) {
  return typeof a === "string" ? a.localeCompare(b) : a - b;
}

function climbComparator(column, direction) {
  const spec = CLIMB_COLUMNS[column] || CLIMB_COLUMNS.recent;
  const sign = direction === "asc" ? 1 : -1;
  const tiebreak = (a, b) => a.name.localeCompare(b.name) || a.angle - b.angle;
  return (a, b) => {
    const left = spec.key(a);
    const right = spec.key(b);
    if (left === null || right === null) {
      if (left === right) return tiebreak(a, b);
      return left === null ? 1 : -1;
    }
    return sign * compareValues(left, right) || tiebreak(a, b);
  };
}

// Angle is a filter, not a sort option. With variants grouped a climb stays
// visible when any of its variants sits at a selected angle; the chips for its
// other angles are dimmed so the match is visible. Empty selection = any angle.
function climbMatchesAngles(climb) {
  return state.climbAngles.size === 0 || climb.angles.some((angle) => state.climbAngles.has(angle));
}

function loadedAngles() {
  return [...new Set(state.rows.map((row) => row.angle))].sort((a, b) => a - b);
}

function renderAngleFilters() {
  const chips = loadedAngles().map((angle) => (
    `<button class="chip" type="button" data-angle="${angle}" aria-pressed="false">${angle}&deg;</button>`
  ));
  $("climbAngles").innerHTML = [
    '<button class="chip" type="button" data-angle="any" aria-pressed="false">Any</button>',
    ...chips,
  ].join("");
  syncAngleChips();
}

// Update the chips in place (rather than re-rendering) so keyboard focus stays
// on the chip that was just toggled.
function syncAngleChips() {
  const anyAngle = state.climbAngles.size === 0;
  $("climbAngles").querySelectorAll(".chip").forEach((chip) => {
    const active = chip.dataset.angle === "any" ? anyAngle : state.climbAngles.has(Number(chip.dataset.angle));
    chip.classList.toggle("is-active", active);
    chip.setAttribute("aria-pressed", String(active));
  });
  $("angleLegendDimmed").classList.toggle("is-hidden", anyAngle);
}

function syncSortHeaders() {
  $("climbHistoryTable").querySelectorAll("th[data-sort]").forEach((header) => {
    if (header.dataset.sort === state.climbSort) {
      header.setAttribute("aria-sort", state.climbSortDirection === "asc" ? "ascending" : "descending");
    } else {
      header.removeAttribute("aria-sort");
    }
  });
}

function climbBadges(climb) {
  const badges = [];
  if (climb.variants.length > 1) badges.push(`<span class="badge badge-variants">${climb.variants.length} variants</span>`);
  if (climb.sends === 0) badges.push('<span class="badge badge-project">project</span>');
  if (climb.flashed) badges.push('<span class="badge badge-flash">flash</span>');
  if (climb.repeated) badges.push('<span class="badge badge-repeat">repeated</span>');
  if (climb.benchmark) badges.push('<span class="badge badge-benchmark">benchmark</span>');
  return badges.join("");
}

// The check is the Tension app's own mark, built from two strokes: the short
// left "\" lights up for a send in the original orientation, the long right
// "/" for a mirrored send, both together for a climb sent both ways. A faint
// stroke is an orientation that was tried but not sent; a missing stroke was
// never tried. Inline SVG, no external assets.
function checkMarkSvg(strokes) {
  return '<svg class="check-mark" viewBox="0 0 12 12" aria-hidden="true" focusable="false">'
    + `<path class="stroke-original is-${strokes.original}" d="M2 6.5 5 9.5"></path>`
    + `<path class="stroke-mirror is-${strokes.mirror}" d="M5 9.5 10.5 2.5"></path>`
    + "</svg>";
}

function strokeState(variant) {
  if (!variant) return "off";
  return variant.sends > 0 ? "lit" : "ghost";
}

// One chip per angle. With variants grouped, the original and mirror variants
// at the same angle share a chip; an ungrouped row carries a single orientation.
function angleGroups(variants) {
  const byAngle = new Map();
  variants.forEach((variant) => {
    if (!byAngle.has(variant.angle)) byAngle.set(variant.angle, { angle: variant.angle, original: null, mirror: null });
    byAngle.get(variant.angle)[variant.mirror ? "mirror" : "original"] = variant;
  });
  return [...byAngle.values()].sort((a, b) => a.angle - b.angle);
}

function variantGroup(variant) {
  return { angle: variant.angle, original: variant.mirror ? null : variant, mirror: variant.mirror ? variant : null };
}

function orientationText(name, variant) {
  if (!variant) return null;
  if (variant.sends === 0) return `${name} tried, not sent`;
  return `${name} sent ${variant.sends === 1 ? "once" : `${variant.sends} times`}`;
}

// A sent chip is filled; a project chip (no send at that angle in either
// orientation) stays outlined. The aria-label/title spell the marks out in
// words. Decorative chips (next to a heading that already says it) are hidden
// from screen readers.
function angleChipHtml(group, { dimmed = false, decorative = false } = {}) {
  const sent = Boolean((group.original && group.original.sends > 0) || (group.mirror && group.mirror.sends > 0));
  const parts = [orientationText("original", group.original), orientationText("mirror", group.mirror)].filter(Boolean);
  const label = `${group.angle} degrees: ${parts.join("; ")}`
    + (sent ? "" : " (project)")
    + (dimmed ? "; outside the angle filter" : "");
  const classes = ["angle-chip", sent ? "is-sent" : "is-project"];
  if (dimmed) classes.push("is-dimmed");
  const naming = decorative
    ? 'aria-hidden="true"'
    : `role="img" aria-label="${escapeHtml(label)}" title="${escapeHtml(label)}"`;
  return `<span class="${classes.join(" ")}" ${naming}>`
    + checkMarkSvg({ original: strokeState(group.original), mirror: strokeState(group.mirror) })
    + `<span class="angle-value">${group.angle}&deg;</span>`
    + "</span>";
}

function variantChipHtml(variant, options) {
  return angleChipHtml(variantGroup(variant), options);
}

function angleVariantsHtml(climb) {
  const filtering = state.climbAngles.size > 0;
  return angleGroups(climb.variants).map((group) => angleChipHtml(group, {
    dimmed: filtering && !state.climbAngles.has(group.angle),
  })).join("");
}

// The legend is built from the same glyph helper as the table, so the two can
// never drift apart. Samples are decorative; the words carry the meaning.
function angleLegendHtml() {
  const sample = (original, mirror, sent, extra = "") => (
    `<span class="angle-chip ${sent ? "is-sent" : "is-project"}${extra}">`
    + checkMarkSvg({ original, mirror })
    + '<span class="angle-value">40&deg;</span></span>'
  );
  const items = [
    [sample("lit", "off", true), "original sent"],
    [sample("off", "lit", true), "mirror sent"],
    [sample("lit", "lit", true), "sent both ways"],
    [sample("ghost", "off", false), "project (tried, not sent)"],
  ];
  return '<span class="legend-title">Angle chips</span>'
    + items.map(([chip, meaning]) => `<span class="legend-item">${chip}${meaning}</span>`).join("")
    + `<span class="legend-item is-hidden" id="angleLegendDimmed">${sample("lit", "off", true, " is-dimmed")}outside the angle filter</span>`;
}

// Community quality is a 1-3 star average, shown the way the app shows it:
// three stars with a fractional fill, plus the number for sorting by eye.
function starsHtml(quality) {
  const width = Math.round((Math.max(0, Math.min(3, quality)) / 3) * 100);
  const value = quality.toFixed(1);
  return `<span class="stars" title="${value} of 3 stars">`
    + `<span class="stars-track" aria-hidden="true">&#9733;&#9733;&#9733;<span class="stars-fill" style="width:${width}%">&#9733;&#9733;&#9733;</span></span>`
    + `<span class="stars-value">${value}</span>`
    + "</span>";
}

function climbDetailHtml(climb) {
  return climb.variants.map((variant) => `
    <div class="variant-detail">
      <div class="variant-heading">
        ${variantChipHtml(variant, { decorative: true })}
        <strong>${variant.mirror ? "Mirror" : "Original"} at ${variant.angle}&deg;</strong>
        <span>${variant.sends} sends, ${variant.totalTries} tries</span>
      </div>
      ${variant.entries.map((entry) => `
        <div class="attempt-line">
          <span class="attempt-date">${formatDate(entry.session_date)}</span>
          <span class="attempt-kind ${entry.is_ascent ? "is-send" : ""}">${entry.is_ascent ? "send" : "attempt"}</span>
          <span class="attempt-tries">${entry.tries} ${entry.tries === 1 ? "try" : "tries"}</span>
          <span class="attempt-grade">${escapeHtml(entry.logged_grade)}</span>
          ${entry.comment ? `<span class="attempt-comment">${escapeHtml(entry.comment)}</span>` : ""}
        </div>
      `).join("")}
    </div>
  `).join("");
}

function filteredClimbs() {
  const query = state.climbQuery.trim().toLowerCase();
  const climbs = state.climbs.filter((climb) => (
    climbMatchesFilter(climb, state.climbFilter)
    && climbMatchesAngles(climb)
    && (!query || climb.name.toLowerCase().includes(query))
  ));
  climbs.sort(climbComparator(state.climbSort, state.climbSortDirection));
  return climbs;
}

function renderClimbs() {
  const sent = state.climbs.filter((climb) => climb.sends > 0);
  const hardest = sent
    .filter((climb) => gradeRank(climb.grade) >= 0)
    .reduce((best, climb) => (
      !best || gradeRank(climb.grade) > gradeRank(best.grade) ? climb : best
    ), null);
  const stats = [
    ["Climbs sent", sent.length],
    ["Projects", state.climbs.length - sent.length],
    ["Flashes", state.climbs.filter((climb) => climb.flashed).length],
    ["Repeated", state.climbs.filter((climb) => climb.repeated).length],
    ["Hardest send", hardest ? escapeHtml(hardest.grade) : "&mdash;"],
  ];
  $("climbStats").innerHTML = stats.map(([label, value]) => (
    `<div class="stat"><strong>${value}</strong><span>${label}</span></div>`
  )).join("");

  const climbs = filteredClimbs();
  $("climbCount").textContent = `${climbs.length} of ${state.climbs.length} climbs`;

  // Old CSVs predate the community columns; drop both columns instead of
  // showing a dash for every row.
  const hasCommunity = state.climbs.some((climb) => climb.communitySends !== null || climb.quality !== null);
  $("climbHistoryTable").classList.toggle("no-community", !hasCommunity);
  const columnCount = hasCommunity ? 10 : 8;
  syncSortHeaders();

  $("climbHistoryRows").innerHTML = climbs.map((climb, index) => {
    const communitySends = climb.communitySends === null ? "&mdash;" : climb.communitySends.toLocaleString();
    const stars = climb.quality === null ? "&mdash;" : starsHtml(climb.quality);
    const tries = climb.triesToFirstSend === null
      ? `${climb.totalTries}`
      : `${climb.triesToFirstSend} <span class="tries-total">/ ${climb.totalTries} total</span>`;
    return `<tr class="climb-row" data-climb-index="${index}" aria-expanded="false" tabindex="0">
      <td>
        <span class="climb-name">${escapeHtml(climb.name)}</span>
        ${climbBadges(climb)}
      </td>
      <td>${escapeHtml(climb.grade)}</td>
      <td><div class="angle-variants">${angleVariantsHtml(climb)}</div></td>
      <td>${climb.sends}</td>
      <td class="climb-tries" title="Tries to first send / total tries">${tries}</td>
      <td>${climb.sessions}</td>
      <td>${climb.firstSendDate ? formatDate(climb.firstSendDate) : "&mdash;"}</td>
      <td>${formatDate(climb.lastClimbed)}</td>
      <td class="is-community is-community-start">${communitySends}</td>
      <td class="is-community">${stars}</td>
    </tr>
    <tr class="climb-detail is-hidden">
      <td colspan="${columnCount}">${climbDetailHtml(climb)}</td>
    </tr>`;
  }).join("") || `<tr><td colspan="${columnCount}" class="hint">No climbs match the current filters.</td></tr>`;
}

function setView(view) {
  state.view = view;
  const showSessions = view === "sessions";
  $("sessionsView").classList.toggle("is-hidden", !showSessions);
  $("climbsView").classList.toggle("is-hidden", showSessions);
  $("viewSessionsTab").classList.toggle("is-active", showSessions);
  $("viewClimbsTab").classList.toggle("is-active", !showSessions);
  $("viewSessionsTab").setAttribute("aria-selected", String(showSessions));
  $("viewClimbsTab").setAttribute("aria-selected", String(!showSessions));
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
    setStatus("No backend is configured for this page; load a CSV file instead.", "error");
    return;
  }
  if (!sessionIsLive(state.session)) {
    setStatus("Session expired. Knock again to continue.", "error");
    expireSession();
    return;
  }
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
      { "X-Board-Session": state.session },
    );
    if (response.status === 403) {
      throw new SessionExpiredError("Session expired. Knock again to continue.");
    }
    if (response.status === 401) {
      throw new Error("Board login failed; check your username and password.");
    }
    if (!response.ok) throw new Error(`Export failed with HTTP ${response.status}`);
    const payload = await response.json();
    loadJsonPayload(payload, "remote storage");
    // Clear the password only after a successful load. On failure, keep it so
    // the user can retry without retyping.
    $("passwordInput").value = "";
  } catch (error) {
    setStatus(error.message, "error");
    if (error instanceof SessionExpiredError) expireSession();
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

$("viewSessionsTab").addEventListener("click", () => setView("sessions"));
$("viewClimbsTab").addEventListener("click", () => setView("climbs"));

$("climbSearch").addEventListener("input", (event) => {
  state.climbQuery = event.target.value;
  renderClimbs();
});

$("climbFilters").addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  state.climbFilter = chip.dataset.filter;
  $("climbFilters").querySelectorAll(".chip").forEach((button) => {
    button.classList.toggle("is-active", button === chip);
  });
  renderClimbs();
});

$("climbAngles").addEventListener("click", (event) => {
  const chip = event.target.closest(".chip");
  if (!chip) return;
  if (chip.dataset.angle === "any") {
    state.climbAngles.clear();
  } else {
    const angle = Number(chip.dataset.angle);
    if (state.climbAngles.has(angle)) state.climbAngles.delete(angle);
    else state.climbAngles.add(angle);
  }
  syncAngleChips();
  renderClimbs();
});

// Click a column header to sort by it; click it again to reverse.
$("climbHistoryTable").querySelector("thead").addEventListener("click", (event) => {
  const button = event.target.closest(".sort-button");
  const header = button && button.closest("th[data-sort]");
  if (!header) return;
  const column = header.dataset.sort;
  if (state.climbSort === column) {
    state.climbSortDirection = state.climbSortDirection === "asc" ? "desc" : "asc";
  } else {
    state.climbSort = column;
    state.climbSortDirection = (CLIMB_COLUMNS[column] || CLIMB_COLUMNS.recent).defaultDirection;
  }
  renderClimbs();
});

$("climbGroupVariants").addEventListener("change", (event) => {
  state.groupClimbVariants = event.target.checked;
  state.climbs = summarizeClimbs(state.rows);
  renderClimbs();
});

// Expand/collapse a climb's attempt history. Rows are keyboard-focusable
// (tabindex=0) so Enter/Space works too.
function toggleClimbRow(row) {
  const detail = row.nextElementSibling;
  if (!detail || !detail.classList.contains("climb-detail")) return;
  const expanded = detail.classList.toggle("is-hidden");
  row.setAttribute("aria-expanded", String(!expanded));
}

$("climbHistoryRows").addEventListener("click", (event) => {
  const row = event.target.closest(".climb-row");
  if (row) toggleClimbRow(row);
});

$("climbHistoryRows").addEventListener("keydown", (event) => {
  if (event.key !== "Enter" && event.key !== " ") return;
  const row = event.target.closest(".climb-row");
  if (row) {
    event.preventDefault();
    toggleClimbRow(row);
  }
});

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

// A refresh resumes the tab's session: the backend already verified the knock
// for this token, and sessionStorage dies with the tab. Otherwise show the
// gate, prefilling the .env knock in local dev so a tester only presses Enter.
const storedSession = sessionStorage.getItem(SESSION_STORAGE_KEY) || "";
if (sessionIsLive(storedSession)) {
  state.session = storedSession;
  enterRoom();
} else {
  sessionStorage.removeItem(SESSION_STORAGE_KEY);
  if (IS_LOCAL && local.knock) $("knockInput").value = local.knock;
}

$("angleLegend").innerHTML = angleLegendHtml();

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
