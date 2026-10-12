/* Mink front end. Plain JavaScript, no build step.
 *
 * Sections:
 *   1. State and helpers
 *   2. API
 *   3. Sidebar and home
 *   4. Processing view
 *   5. Transcript view (lines, speakers, editing, search, replace)
 *   6. Audio player
 *   7. Modals (settings, voice library)
 *   8. Upload and drag and drop
 *   9. Event wiring
 */

// ============ 1. State and helpers ============

const state = {
  sessions: [],
  session: null, // The open session (full transcript)
  editing: false,
  search: { query: "", hits: [], index: -1 },
  playingLineId: null,
  progressStart: null, // {time, progress} for the ETA estimate
  info: null,
};

// Cool tones that sit next to the navy and teal theme. Each passes 4.5:1 with white initials.
const SPEAKER_COLORS = [
  "#1e6a74",
  "#2b5c9e",
  "#5b4ba6",
  "#2f6b3f",
  "#7a3e8e",
  "#1b6f95",
  "#3d6b5c",
  "#8a3b6e",
  "#0d3242",
  "#4a5a68",
];
const UNKNOWN_COLOR = "#5e6e78";
const SPEEDS = [1, 1.25, 1.5, 2];

const $ = (id) => document.getElementById(id);

/** One icon set for every button, so they all look alike. Stroke paths on a 24px grid. */
const ICON_PATHS = {
  check: "M4 12l5 5L20 6",
  close: "M6 6l12 12M18 6L6 18",
  download: "M12 4v12m0 0l-4-4m4 4l4-4M4 20h16",
  play: "M8 5v14l11-7z",
  refresh: "M20 11a8 8 0 1 0-2.3 5.7M20 4v7h-7",
  swap: "M4 7h11m0 0l-3-3m3 3l-3 3M20 17H9m0 0l3-3m-3 3l3 3",
  trash: "M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3",
  userPlus: "M15 19c0-3-2.7-5-6-5s-6 2-6 5M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7M19 8v6M16 11h6",
};

function icon(name) {
  return `<svg viewBox="0 0 24 24" class="icon" aria-hidden="true"><path d="${ICON_PATHS[name]}" /></svg>`;
}

function fillIconPlaceholders() {
  for (const placeholder of document.querySelectorAll("[data-icon]"))
    placeholder.outerHTML = icon(placeholder.dataset.icon);
}

function escapeHtml(text) {
  return String(text).replace(
    /[&<>"']/g,
    (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
  );
}

function clock(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = String(seconds % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${s}` : `${m}:${s}`;
}

function longDuration(seconds) {
  const h = Math.floor(seconds / 3600);
  const m = Math.round((seconds % 3600) / 60);
  return h ? `${h} hr ${m} min` : `${m} min`;
}

function dateLabel(epochSeconds) {
  return new Date(epochSeconds * 1000).toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    year: "numeric",
  });
}

function speakerColor(speakerId) {
  return SPEAKER_COLORS[Number(speakerId) % SPEAKER_COLORS.length];
}

function initials(name) {
  return (name.trim()[0] || "?").toUpperCase();
}

function avatar(speakerId, name) {
  // Decorative: the name is always written next to it.
  if (name === "Unknown") return `<span class="avatar" aria-hidden="true" style="background:${UNKNOWN_COLOR}">?</span>`;
  const color = speakerColor(speakerId);
  return `<span class="avatar" aria-hidden="true" style="background:${color}">${escapeHtml(initials(name))}</span>`;
}

function readLocal(key, fallback) {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}

function writeLocal(key, value) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode: ignore */
  }
}

let toastTimer = null;
/** Short message at the bottom. Pass an action (usually Undo) to add a button. */
function toast(message, action, actionLabel = "Undo") {
  const el = $("toast");
  el.innerHTML =
    `<span>${escapeHtml(message)}</span>` +
    (action ? `<button data-action="toast-action">${actionLabel}</button>` : "");
  el.hidden = false;
  el.onclick = async (event) => {
    if (event.target.dataset.action !== "toast-action") return;
    el.hidden = true;
    await action();
  };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), action ? 10000 : 3000);
}

// ============ 2. API ============

async function api(path, options = {}) {
  const init = { ...options };
  if (options.json !== undefined) {
    init.body = JSON.stringify(options.json);
    init.headers = { "Content-Type": "application/json" };
  }
  const response = await fetch(path, init);
  if (!response.ok) {
    const message = await readableError(response);
    toast(message);
    throw new Error(message);
  }
  return response.json();
}

/** Turn a failed request into a sentence a person can act on. */
async function readableError(response) {
  const body = await response.text();
  let detail = body;
  try {
    detail = JSON.parse(body).detail ?? body;
  } catch {
    // Plain text error from Mink itself
  }
  const routeMissing = response.status === 404 && detail === "Not Found";
  if (routeMissing) {
    await checkForUpdate();
    return "This needs the latest version of Mink running. Quit Mink and open it again.";
  }
  return detail || `Something went wrong (error ${response.status}). Try again.`;
}

/** Mink's files can be updated while it runs. Tell the user when a restart is needed. */
async function checkForUpdate() {
  const info = await fetch("/api/info").then((response) => response.json());
  $("update-banner").hidden = !info.restart_needed;
}

const speakersUrl = (id) => `/api/sessions/${state.session.id}/speakers/${id}`;
const lineUrl = (id) => `/api/sessions/${state.session.id}/lines/${id}`;

/** Snapshot before a change so the toast can offer Undo. */
function snapshot() {
  return structuredClone({ speakers: state.session.speakers, lines: state.session.lines });
}

function undoTo(saved) {
  const sessionId = state.session.id;
  return async () => {
    const session = await api(`/api/sessions/${sessionId}/transcript`, { method: "PUT", json: saved });
    if (state.session?.id === sessionId) setSession(session);
    toast("Undone");
  };
}

// ============ 3. Sidebar and home ============

async function refreshSessionList() {
  state.sessions = await api("/api/sessions");
  renderSidebar();
  if (!state.session) renderHomeList();
}

function sessionSubtitle(summary) {
  if (summary.status === "error") return `<span class="status-dot error"></span>Failed`;
  if (summary.status === "stopped") return "Stopped";
  if (summary.status !== "done")
    return `<span class="status-dot"></span>${Math.round(summary.progress * 100)}% ${escapeHtml(summary.stage)}`;
  return `${dateLabel(summary.created_at)} · ${longDuration(summary.duration || 0)}`;
}

function renderSidebar() {
  $("session-list").innerHTML =
    state.sessions
      .map(
        (s) => `
    <li>
      <button class="session-item ${state.session?.id === s.id ? "active" : ""}" data-action="open-session" data-id="${s.id}"
        ${state.session?.id === s.id ? 'aria-current="page"' : ""}>
        <span class="session-item-title">${escapeHtml(s.title)}</span>
        <span class="session-item-sub">${sessionSubtitle(s)}</span>
      </button>
    </li>`,
      )
      .join("") || `<li class="hint" style="padding:8px 10px">No recordings yet</li>`;
}

function renderHomeList() {
  if (!state.sessions.length) {
    $("home-list").innerHTML = "";
    return;
  }
  $("home-list").innerHTML =
    `<h2>Recent</h2>` +
    state.sessions
      .map(
        (s) => `
    <button class="home-row" data-action="open-session" data-id="${s.id}">
      <span class="home-row-title">${escapeHtml(s.title)}</span>
      <span class="home-row-sub">${sessionSubtitle(s)}</span>
    </button>`,
      )
      .join("");
}

function showView(name) {
  for (const view of ["home", "processing", "transcript"]) $(`${view}-view`).hidden = view !== name;
  $("player").hidden = name !== "transcript";
}

function goHome() {
  state.session = null;
  pauseAudio();
  showView("home");
  renderSidebar();
  renderHomeList();
  history.replaceState(null, "", "/");
}

// ============ 4. Processing view ============

async function openSession(sessionId) {
  closeFloating();
  const session = await api(`/api/sessions/${sessionId}`);
  const isNewSession = state.session?.id !== session.id;
  if (isNewSession) {
    resetSearch();
    state.progressStart = null;
    setEditing(false);
  }
  setSession(session, isNewSession);
  history.replaceState(null, "", `#${session.id}`);
}

function setSession(session, loadAudio = false) {
  state.session = session;
  renderSidebar();
  if (session.status !== "done") {
    showView("processing");
    renderProcessing();
    return;
  }
  showView("transcript");
  if (loadAudio || !$("audio").src.includes(session.id)) loadAudioFor(session);
  renderTranscriptView();
}

function renderProcessing() {
  const s = state.session;
  const percent = Math.round(s.progress * 100);
  const running = s.status === "queued" || s.status === "processing";
  $("processing-title").textContent = s.title;
  $("progress-fill").style.width = `${percent}%`;
  $("progress").setAttribute("aria-valuenow", percent);
  $("processing-stage").textContent = running ? `${s.stage} · ${percent}%` : s.stage;
  $("processing-detail").textContent = running ? s.detail || "" : "";
  $("processing-eta").textContent = s.status === "processing" ? etaLabel(s.progress) : "";
  $("heartbeat").innerHTML = running ? heartbeatLabel(s) : "";
  $("processing-hint").hidden = !running;
  $("processing-actions").hidden = !running;
  $("stopped-box").hidden = s.status !== "stopped";
  $("error-box").hidden = s.status !== "error";
  $("error-text").textContent = s.error || "";
}

/** Proof of life: how long it has been working and when the last update arrived. */
function heartbeatLabel(s) {
  if (s.status === "queued") return `<span class="status-dot"></span>Waiting for the recording ahead of it to finish`;
  const now = Date.now() / 1000;
  const working = s.started_at ? Math.max(1, Math.round((now - s.started_at) / 60)) : null;
  const sinceUpdate = s.updated_at ? Math.round(now - s.updated_at) : null;
  const parts = [];
  if (working) parts.push(`Working for ${working} min`);
  if (sinceUpdate !== null) parts.push(sinceUpdate < 5 ? "updated just now" : `last update ${clock(sinceUpdate)} ago`);
  return `<span class="status-dot"></span>${parts.join(" · ") || "Starting up"}`;
}

function etaLabel(progress) {
  const now = Date.now() / 1000;
  // Restart the clock until real progress begins, so model download time doesn't skew the estimate.
  if (!state.progressStart || progress <= 0 || progress < state.progressStart.progress)
    state.progressStart = { time: now, progress };
  const done = progress - state.progressStart.progress;
  const elapsed = now - state.progressStart.time;
  if (done < 0.02 || elapsed < 20) return "Estimating time left...";
  const remaining = (elapsed / done) * (1 - progress);
  return remaining < 90 ? "About a minute left" : `About ${Math.round(remaining / 60)} min left`;
}

async function pollOpenSession() {
  const s = state.session;
  if (!s || ["done", "stopped", "error"].includes(s.status)) return;
  const fresh = await api(`/api/sessions/${s.id}`);
  if (state.session?.id === fresh.id) setSession(fresh, fresh.status === "done");
}

// ============ 5. Transcript view ============

function renderTranscriptView() {
  const s = state.session;
  $("session-title").textContent = s.title;
  const speakerCount = Object.keys(s.speakers).length;
  $("session-meta").textContent =
    `${dateLabel(s.created_at)} · ${longDuration(s.duration || 0)} · ${speakerCount} speakers · ${s.lines.length} lines`;
  renderVersionBar();
  renderSpeakerChips();
  renderLines();
  previewReplaceCount();
}

/** After a redo, offer the transcript from before it. This is the undo for redoing. */
function renderVersionBar() {
  const previous = state.session.previous_version;
  $("version-bar").hidden = !previous;
  if (!previous) return;
  const message = previous.swapped
    ? `You're looking at the version from before ${previous.label}.`
    : `${previous.label} replaced the earlier version of this transcript.`;
  const switchLabel = previous.swapped ? "Switch to the newer version" : "Switch back to the earlier version";
  $("version-bar").innerHTML = `
    ${icon("refresh")}<span>${escapeHtml(message)}</span>
    <button class="btn btn-ghost" data-action="switch-version" data-tip="Swap to the other version. You can swap back.">${icon("swap")}${switchLabel}</button>
    <button class="text-btn" data-action="keep-version" data-tip="Hide this and keep what you see">Keep this one</button>`;
}

function lineCounts() {
  const counts = {};
  for (const line of state.session.lines) counts[line.speaker] = (counts[line.speaker] || 0) + 1;
  return counts;
}

function renderSpeakerChips() {
  const counts = lineCounts();
  $("speaker-chips").innerHTML = Object.entries(state.session.speakers)
    .map(
      ([id, speaker]) => `
    <button class="chip" data-action="speaker-menu" data-speaker="${id}" data-tip="Rename everywhere">
      ${avatar(id, speaker.name)}${escapeHtml(speaker.name)}
      ${speaker.auto_named ? `<span class="badge" data-tip="Guessed from your voice library. Click to confirm.">auto</span>` : ""}
      <span class="count">${counts[id] || 0}</span>
    </button>`,
    )
    .join("");
}

function renderLines() {
  const { speakers, lines } = state.session;
  const query = state.search.query;
  const html = lines
    .map((line, index) => {
      const name = speakers[line.speaker]?.name || "Unknown";
      const previous = lines[index - 1];
      const sameSpeaker = previous && previous.speaker === line.speaker && line.start - previous.end < 3;
      const classes = [line.id === state.playingLineId && "playing", sameSpeaker && "same", line.unsure && "unsure"]
        .filter(Boolean)
        .join(" ");
      const hint = line.unsure ? ` title="Mink isn't sure who said this one"` : "";
      return `
      <div class="line ${classes}" data-line="${line.id}"${hint}>
        ${avatar(line.speaker, name)}
        <div class="line-head">
          <button class="speaker-name" data-action="speaker-menu" data-speaker="${line.speaker}" data-line="${line.id}" data-tip="Change who said this">${escapeHtml(name)}</button>
          <button class="stamp" data-action="seek" data-time="${line.start}" data-tip="Play from here">${clock(line.start)}</button>
        </div>
        <div class="line-text" data-line="${line.id}">${lineTextHtml(line, query)}</div>
      </div>`;
    })
    .join("");
  $("transcript").innerHTML = html || `<div class="empty">No speech was found in this recording.</div>`;
  $("transcript").classList.toggle("editing", state.editing);
  if (query) collectSearchHits();
}

function lineById(id) {
  return state.session.lines.find((line) => line.id === Number(id));
}

// ----- Speaker popover: rename everywhere, or move one line -----

function openSpeakerMenu(button) {
  const speakerId = button.dataset.speaker;
  const lineId = button.dataset.line;
  const speaker = state.session.speakers[speakerId];
  const count = lineCounts()[speakerId] || 0;
  const others = Object.entries(state.session.speakers).filter(([id]) => id !== speakerId);
  const sessionNames = Object.values(state.session.speakers).map((s) => s.name);
  const knownNames = [...new Set([...sessionNames, ...(state.voiceNames || [])])].filter((n) => n !== speaker.name);
  const savedVoices = knownNames.map((n) => `<option value="${escapeHtml(n)}">`).join("");

  const moveSection =
    lineId === undefined
      ? ""
      : `
    <hr>
    <h4>Only this line was said by</h4>
    ${others.map(([id, s]) => `<button class="popover-option" data-action="move-line" data-line="${lineId}" data-speaker="${id}">${avatar(id, s.name)}${escapeHtml(s.name)}</button>`).join("")}
    <form data-form="new-speaker" data-line="${lineId}">
      <input name="name" placeholder="Someone else..." aria-label="Name of who said this line" list="voice-names" autocomplete="off">
      <button class="btn btn-ghost">${icon("userPlus")}Set</button>
    </form>`;

  const popover = $("speaker-popover");
  focusBeforePopover = button;
  popover.setAttribute("aria-label", `Speaker: ${speaker.name}`);
  popover.innerHTML = `
    <h4>Rename everywhere</h4>
    <form data-form="rename-speaker" data-speaker="${speakerId}">
      <input name="name" value="${escapeHtml(speaker.name)}" aria-label="Speaker name" list="voice-names" autocomplete="off">
      <button class="btn btn-primary">${icon("check")}Save</button>
    </form>
    <div class="scope">Updates ${count === 1 ? "the 1 line" : `all ${count} lines`} by ${escapeHtml(speaker.name)}. Use a name that's already here to merge two speakers.</div>
    ${moveSection}
    <datalist id="voice-names">${savedVoices}</datalist>`;
  popover.hidden = false;
  positionNear(popover, button);
  const input = popover.querySelector("input");
  input.focus();
  input.select();
}

function positionNear(popover, anchor) {
  // Open under the whole line, not just its name, so the words being fixed stay visible.
  // If it won't fit below, scroll the line up first.
  const lineEl = anchor.closest(".line");
  const below = () => (lineEl || anchor).getBoundingClientRect();
  if (lineEl && below().bottom + popover.offsetHeight + 18 > window.innerHeight) {
    lineEl.scrollIntoView({ block: "start" });
  }
  const top = Math.min(below().bottom + 6, window.innerHeight - popover.offsetHeight - 12);
  const left = Math.min(anchor.getBoundingClientRect().left, window.innerWidth - popover.offsetWidth - 12);
  popover.style.top = `${Math.max(12, top)}px`;
  popover.style.left = `${Math.max(12, left)}px`;
}

async function renameSpeaker(speakerId, name) {
  const before = snapshot();
  const oldName = state.session.speakers[speakerId].name;
  if (!name.trim()) return;
  const session = await api(speakersUrl(speakerId), { method: "PATCH", json: { name } });
  closeFloating();
  setSession(session);
  loadVoiceNames();
  const merged = !session.speakers[speakerId];
  toast(merged ? `Merged ${oldName} into ${name}` : `Renamed ${oldName} to ${name} everywhere`, undoTo(before));
}

async function moveLine(lineId, speakerId, newSpeakerName) {
  const before = snapshot();
  const json = newSpeakerName ? { new_speaker_name: newSpeakerName } : { speaker_id: speakerId };
  const session = await api(lineUrl(lineId), { method: "PATCH", json });
  closeFloating();
  setSession(session);
  toast("Line moved", undoTo(before));
}

// ----- Edit mode -----

function setEditing(on) {
  state.editing = on;
  $("edit-toggle").classList.toggle("on", on);
  $("edit-toggle").querySelector("span").textContent = on ? "Done editing" : "Edit transcript";
  $("edit-banner").hidden = !on;
  $("transcript").classList.toggle("editing", on);
  if (!on) document.activeElement?.blur?.();
}

function startEditingLine(textEl) {
  if (textEl.isContentEditable) return;
  const line = lineById(textEl.dataset.line);
  textEl.textContent = line.text; // drop search highlights while editing
  textEl.contentEditable = "true";
  textEl.dataset.original = line.text;
  textEl.focus();
}

async function finishEditingLine(textEl) {
  if (textEl.dataset.splitting) return; // The split request already saved this text
  textEl.contentEditable = "false";
  const line = lineById(textEl.dataset.line);
  const newText = textEl.textContent.replace(/\s+/g, " ").trim();
  textEl.innerHTML = lineTextHtml({ ...line, text: newText || line.text }, state.search.query);
  if (!newText || newText === textEl.dataset.original) return;
  const before = snapshot();
  line.text = newText;
  await api(lineUrl(line.id), { method: "PATCH", json: { text: newText } });
  toast("Saved", undoTo(before));
}

function cancelEditingLine(textEl) {
  textEl.textContent = textEl.dataset.original;
  textEl.blur();
}

// ----- Click to seek, Enter to split -----

/** Whitespace handling must match how edited text is saved, so offsets line up. */
function normalizeText(text) {
  return text.replace(/\s+/g, " ").replace(/^ /, "");
}

/** Character offset of a DOM position inside a line's text element. */
function charOffset(textEl, node, offset) {
  const range = document.createRange();
  range.setStart(textEl, 0);
  range.setEnd(node, offset);
  return normalizeText(range.toString()).length;
}

/** {lineId, start, end} for the current selection or caret, if it sits inside one line. */
function selectionInLine() {
  const selection = window.getSelection();
  if (!selection.rangeCount) return null;
  const range = selection.getRangeAt(0);
  const textEl = range.startContainer.parentElement?.closest(".line-text");
  if (!textEl || !textEl.contains(range.endContainer)) return null;
  return {
    textEl,
    lineId: Number(textEl.dataset.line),
    start: charOffset(textEl, range.startContainer, range.startOffset),
    end: charOffset(textEl, range.endContainer, range.endOffset),
  };
}

/** Same logic as mink/timing.py: real word times while the text is unedited, otherwise an even spread. */
function timeAtOffset(line, offset) {
  const spans = wordSpans(line);
  if (spans) return (spans.find((span) => span.to > offset) || spans[spans.length - 1]).start;
  return line.start + (line.end - line.start) * (offset / Math.max(line.text.length, 1));
}

function wordSpans(line) {
  if (!line.words?.length) return null;
  const joined = line.words.map((w) => w[2]).join("");
  if (joined.trim() !== line.text) return null;
  const lead = joined.length - joined.trimStart().length;
  let position = 0;
  return line.words.map(([start, , text]) => {
    const span = { to: position + text.length - lead, start };
    position += text.length;
    return span;
  });
}

function seekToSelection() {
  if (state.editing) return;
  const spot = selectionInLine();
  if (!spot) return;
  highlightLine(spot.lineId); // The line you clicked, even if the word sits on a line boundary
  seek(timeAtOffset(lineById(spot.lineId), spot.start));
  if (window.getSelection().isCollapsed) window.getSelection().removeAllRanges(); // Let the word highlight show
  markCurrentWord();
}

async function splitAtSelection(event) {
  const spot = selectionInLine();
  if (!spot) return false;
  const line = lineById(spot.lineId);
  const editing = spot.textEl.isContentEditable;
  const text = editing ? normalizeText(spot.textEl.textContent).trimEnd() : line.text;
  const cuts = spot.start === spot.end ? [spot.start] : [spot.start, spot.end];
  if (!cuts.some((cut) => cut > 0 && cut < text.length)) return false; // At the very start or end: nothing to split

  event.preventDefault();
  if (editing) {
    spot.textEl.dataset.splitting = "1";
    spot.textEl.contentEditable = "false";
  }
  const before = snapshot();
  const result = await api(`/api/sessions/${state.session.id}/lines/${spot.lineId}/split`, {
    method: "POST",
    json: { cuts, text: editing ? text : null },
  });
  window.getSelection().removeAllRanges();
  setSession(result.session);
  toast("Split into a new voice. Rename it, or pick who said it.", undoTo(before));
  const nameButton = document.querySelector(`.speaker-name[data-line="${result.focus_line_id}"]`);
  if (nameButton) {
    nameButton.scrollIntoView({ block: "center" });
    openSpeakerMenu(nameButton);
  }
  return true;
}

// ----- Word by word -----

/** A line's text with each word in its own span, so playback can light up the word being said.
 * Search highlights take over while you search. Edited lines lose word timing and render plain. */
function lineTextHtml(line, query) {
  if (query || !wordSpans(line)) return highlight(line.text, query);
  return line.words
    .map(([, , text], index) => {
      const space = index === 0 ? "" : text.match(/^\s*/)[0];
      return `${space}<span class="w" data-w="${index}">${escapeHtml(text.trim())}</span>`;
    })
    .join("");
}

let currentWord = null;

/** Highlight the word at the current playback time inside the highlighted line. */
function markCurrentWord() {
  const line = state.playingLineId === null ? null : lineById(state.playingLineId);
  const time = audio().currentTime;
  let index = -1;
  if (line?.words) {
    for (let i = 0; i < line.words.length && line.words[i][0] <= time + 0.05; i++) index = i;
    if (index >= 0 && time > line.words[index][1] + 0.6) index = -1; // In a pause after the word
  }
  const el = index < 0 ? null : document.querySelector(`.line[data-line="${line.id}"] .w[data-w="${index}"]`);
  if (el === currentWord) return;
  currentWord?.classList.remove("now");
  el?.classList.add("now");
  currentWord = el;
}

function followWordsWhilePlaying() {
  markCurrentWord();
  if (!audio().paused) requestAnimationFrame(followWordsWhilePlaying);
}

// ----- Search -----

function highlight(text, query) {
  const safe = escapeHtml(text);
  if (!query) return safe;
  const pattern = new RegExp(escapeHtml(query).replace(/[.*+?^${}()|[\]\\]/g, "\\$&"), "gi");
  return safe.replace(pattern, (match) => `<mark>${match}</mark>`);
}

function resetSearch() {
  state.search = { query: "", hits: [], index: -1 };
  $("search-input").value = "";
  $("search-count").textContent = "";
}

function runSearch(query) {
  state.search.query = query.trim();
  state.search.index = -1;
  if (state.session?.status === "done") renderLines();
  if (!state.search.query) {
    $("search-count").textContent = "";
    return;
  }
  stepSearch(1);
}

function collectSearchHits() {
  state.search.hits = [...$("transcript").querySelectorAll("mark")];
  updateSearchCount();
}

function stepSearch(direction) {
  const hits = state.search.hits;
  if (!hits.length) {
    updateSearchCount();
    return;
  }
  hits[state.search.index]?.classList.remove("current");
  state.search.index = (state.search.index + direction + hits.length) % hits.length;
  const hit = hits[state.search.index];
  hit.classList.add("current");
  hit.scrollIntoView({ block: "center", behavior: "smooth" });
  updateSearchCount();
}

function updateSearchCount() {
  const { query, hits, index } = state.search;
  $("search-count").textContent = !query ? "" : hits.length ? `${index + 1} of ${hits.length}` : "No matches";
}

// ----- Find and replace -----

function previewReplaceCount() {
  const find = $("replace-find").value.trim();
  if (!find || !state.session) {
    $("replace-count").textContent = "";
    return;
  }
  let source = find.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  if ($("replace-word").checked) source = `(?<!\\w)${source}(?!\\w)`;
  const pattern = new RegExp(source, $("replace-case").checked ? "g" : "gi");
  const count = state.session.lines.reduce((sum, line) => sum + (line.text.match(pattern) || []).length, 0);
  $("replace-count").textContent = `${count} found`;
}

async function replaceAll() {
  const find = $("replace-find").value;
  if (!find.trim()) {
    $("replace-find").focus();
    return;
  }
  const before = snapshot();
  const result = await api(`/api/sessions/${state.session.id}/replace`, {
    method: "POST",
    json: {
      find,
      replace: $("replace-with").value,
      match_case: $("replace-case").checked,
      whole_word: $("replace-word").checked,
      remember: $("replace-remember").checked,
    },
  });
  setSession(result.session);
  previewReplaceCount();
  const remembered = $("replace-remember").checked ? " Saved for future sessions." : "";
  toast(`Replaced ${result.count} ${result.count === 1 ? "time" : "times"}.${remembered}`, undoTo(before));
}

// ============ 6. Audio player ============

const audio = () => $("audio");

function loadAudioFor(session) {
  audio().src = `/api/sessions/${session.id}/audio`;
  state.playingLineId = null;
}

function pauseAudio() {
  audio().pause();
}

function togglePlay() {
  if (audio().paused) audio().play();
  else audio().pause();
}

function seek(seconds, play = true) {
  audio().currentTime = Math.max(0, seconds);
  if (play) audio().play();
}

function lineAtTime(seconds) {
  const lines = state.session.lines;
  let low = 0,
    high = lines.length - 1,
    found = null;
  while (low <= high) {
    const mid = (low + high) >> 1;
    if (lines[mid].start <= seconds) {
      found = lines[mid];
      low = mid + 1;
    } else {
      high = mid - 1;
    }
  }
  return found;
}

function onTimeUpdate() {
  const a = audio();
  $("time-current").textContent = clock(a.currentTime);
  if (a.duration) $("scrubber").value = Math.round((a.currentTime / a.duration) * 1000);
  if (!state.session || state.session.status !== "done") return;

  const line = lineAtTime(a.currentTime);
  if (!line || line.id === state.playingLineId) return;
  const current = state.playingLineId === null ? null : lineById(state.playingLineId);
  if (current && a.currentTime >= current.start && a.currentTime < current.end) return; // Still inside the highlighted line
  const el = highlightLine(line.id);
  const editingNow = document.activeElement?.isContentEditable;
  if (el && $("follow-audio").checked && !a.paused && !editingNow)
    el.scrollIntoView({ block: "center", behavior: "smooth" });
}

function highlightLine(lineId) {
  document.querySelector(`.line[data-line="${state.playingLineId}"]`)?.classList.remove("playing");
  state.playingLineId = lineId;
  const el = document.querySelector(`.line[data-line="${lineId}"]`);
  el?.classList.add("playing");
  return el;
}

function cycleSpeed() {
  const next = SPEEDS[(SPEEDS.indexOf(audio().playbackRate) + 1) % SPEEDS.length];
  audio().playbackRate = next;
  $("speed-btn").textContent = `${next}x`;
  $("speed-btn").setAttribute("aria-label", `Playback speed ${next}x`);
}

function setPlayIcon(playing) {
  $("play-btn").setAttribute("aria-label", playing ? "Pause" : "Play");
  $("play-icon").innerHTML = playing ? `<path d="M7 5h4v14H7zM13 5h4v14h-4z"/>` : `<path d="M8 5v14l11-7z"/>`;
}

// ============ 7. Modals ============

let focusBeforeModal = null;

function openModal(title, bodyHtml) {
  if ($("modal").hidden) focusBeforeModal = document.activeElement;
  $("modal-title").textContent = title;
  $("modal-body").innerHTML = bodyHtml;
  fillIconPlaceholders();
  $("modal").hidden = false;
  const first =
    $("modal-body").querySelector("[autofocus], input, select, textarea, button") || $("modal").querySelector("button");
  first?.focus();
}

function closeModal() {
  if ($("modal").hidden) return;
  $("modal").hidden = true;
  focusBeforeModal?.focus();
  focusBeforeModal = null;
  if (speakerCountAnswer) {
    speakerCountAnswer(null);
    speakerCountAnswer = null;
  }
}

async function openSettings() {
  const info = await api("/api/info");
  state.info = info;
  const s = info.settings;
  const modelOptions = Object.entries(info.models)
    .map(([key, label]) => `<option value="${key}" ${key === s.model ? "selected" : ""}>${escapeHtml(label)}</option>`)
    .join("");
  const fixes =
    s.corrections
      .map(
        (c, i) => `
    <div class="list-row">
      <span class="grow">"${escapeHtml(c.find)}" &#8594; "${escapeHtml(c.replace)}"</span>
      <button class="btn btn-danger" data-action="remove-fix" data-index="${i}" data-tip="Stop applying this fix">${icon("trash")}Remove</button>
    </div>`,
      )
      .join("") ||
    `<div class="hint">None yet. Use Find &amp; replace with "Fix in future sessions too" checked.</div>`;

  openModal(
    "Settings",
    `
    <div class="field">
      <label for="set-model">Transcription model</label>
      <select id="set-model">${modelOptions}</select>
      <div class="hint">Engine: ${escapeHtml(info.engine)}. The first run downloads the model (about 1.5 to 3 GB).</div>
    </div>
    <div class="field">
      <label for="set-token">Better speaker labels (free)</label>
      <input type="password" id="set-token" class="text-input" placeholder="hf_..." value="${escapeHtml(s.hf_token)}" autocomplete="off">
      <div class="hint">
        Now using: <b>${escapeHtml(info.speaker_engine)}</b>. Paste a free Hugging Face token to switch to pyannote, which is much better at telling voices apart. One time setup:
        <ol class="steps">
          <li>Make a free account at <a href="https://huggingface.co/join" target="_blank" rel="noopener">huggingface.co/join</a></li>
          <li>Open <a href="https://huggingface.co/pyannote/speaker-diarization-community-1" target="_blank" rel="noopener">the speaker model page</a>, fill in the short form and click Agree</li>
          <li>Create a token at <a href="https://huggingface.co/settings/tokens/new?tokenType=read" target="_blank" rel="noopener">huggingface.co/settings/tokens</a> (type: Read) and paste it here</li>
        </ol>
        The token stays on this Mac. Your audio never leaves it.
      </div>
    </div>
    <div class="field">
      <label for="set-glossary">Names and words to expect</label>
      <textarea id="set-glossary" placeholder="One per line, e.g.&#10;Philip Druskenvald&#10;Waterdeep&#10;Eldritch Blast">${escapeHtml(s.glossary.join("\n"))}</textarea>
      <div class="hint">Whisper gets these as a hint before transcribing, so character, NPC, and place names come out spelled right more often.</div>
    </div>
    <div class="field">
      <h3 class="field-title">Saved fixes</h3>
      <div id="fix-list">${fixes}</div>
    </div>
    <div class="field">
      <label class="check-label"><input type="checkbox" id="set-autoname" ${s.auto_name_voices ? "checked" : ""}> Auto-name returning players</label>
      <div class="hint" style="margin-bottom:12px">Off is safer: new recordings start with Speaker 1, 2, 3 instead of guessing names that might be wrong.</div>
      <label for="set-threshold">Voice recognition strictness: <span id="threshold-value">${s.voice_match_threshold.toFixed(2)}</span></label>
      <input type="range" id="set-threshold" min="0.5" max="0.95" step="0.01" value="${s.voice_match_threshold}">
      <div class="hint">Higher means Mink only auto names a speaker when it's very sure. Lower it if returning players aren't being recognized.</div>
    </div>
    <div class="field">
      <label for="set-export-folder">Save exports to</label>
      <input id="set-export-folder" class="text-input" placeholder="Leave empty to download through your browser" value="${escapeHtml(s.export_folder)}" autocomplete="off">
      <div class="hint">Paste a folder path, for example ~/Documents/Transcripts. In Finder, right click a folder, hold Option, and choose Copy as Pathname.</div>
    </div>
    <div class="modal-actions">
      <button class="btn btn-ghost" data-action="close-modal">${icon("close")}Cancel</button>
      <button class="btn btn-primary" data-action="save-settings">${icon("check")}Save</button>
    </div>`,
  );
}

async function saveSettings(extra = {}) {
  const current = state.info.settings;
  const settings = {
    ...current,
    model: $("set-model")?.value ?? current.model,
    glossary: ($("set-glossary")?.value ?? current.glossary.join("\n"))
      .split("\n")
      .map((t) => t.trim())
      .filter(Boolean),
    voice_match_threshold: Number($("set-threshold")?.value ?? current.voice_match_threshold),
    auto_name_voices: $("set-autoname")?.checked ?? current.auto_name_voices,
    hf_token: ($("set-token")?.value ?? current.hf_token).trim(),
    export_folder: ($("set-export-folder")?.value ?? current.export_folder).trim(),
    ...extra,
  };
  state.info.settings = await api("/api/settings", { method: "PUT", json: settings });
}

async function removeFix(index) {
  const before = state.info.settings.corrections;
  await saveSettings({ corrections: before.filter((_, i) => i !== index) });
  await openSettings();
  toast("Fix removed", async () => {
    await saveSettings({ corrections: before });
    if (!$("modal").hidden) await openSettings();
    toast("Fix restored");
  });
}

async function openVoices() {
  const voices = await api("/api/voices");
  const rows = voices
    .map(
      (v, i) => `
    <div class="list-row">
      ${avatar(i, v.name)}
      <span class="grow"><b>${escapeHtml(v.name)}</b><div class="sub">Learned from ${v.sessions} ${v.sessions === 1 ? "session" : "sessions"}</div></span>
      <button class="btn btn-danger" data-action="delete-voice" data-name="${escapeHtml(v.name)}" data-tip="Stop recognizing this voice">${icon("trash")}Forget</button>
    </div>`,
    )
    .join("");
  openModal(
    "Voice library",
    `
    <p class="hint" style="margin-top:0">When you rename a speaker, Mink saves what they sound like. In future recordings it names them automatically and marks them <span class="badge">auto</span> so you can double check.</p>
    ${rows || `<div class="empty">No voices yet. Rename a speaker in any transcript to add one.</div>`}`,
  );
}

async function loadVoiceNames() {
  state.voiceNames = (await api("/api/voices")).map((v) => v.name);
}

// ============ 8. Upload and drag and drop ============

let speakerCountAnswer = null;

/** Ask how many people are talking. Resolves to a number, 0 for "not sure", or null if cancelled.
 *  This is the biggest single factor in speaker label accuracy, so we ask every time. */
function askSpeakerCount(title, buttonLabel, suggested) {
  const last = suggested ?? readLocal("mink-speaker-count", "");
  openModal(
    title,
    `
    <form data-form="speaker-count">
      <div class="field">
        <label for="speaker-count">How many voices should Mink look for?</label>
        <input type="number" id="speaker-count" name="count" class="text-input" min="1" max="40" value="${escapeHtml(last)}" placeholder="Number of people" autofocus>
        <div class="hint">Mink finds exactly this many voices. If people are getting lumped together, aim high: 10 to 15 for a table of 5 or 6. Extra voices are easy to fix afterward, just rename them to the right person and they merge. Leave it empty to let Mink guess.</div>
      </div>
      <div class="modal-actions">
        <button type="button" class="btn btn-ghost" data-action="close-modal">${icon("close")}Cancel</button>
        <button class="btn btn-primary">${icon("play")}${escapeHtml(buttonLabel)}</button>
      </div>
    </form>`,
  );
  setTimeout(() => $("speaker-count")?.select(), 0);
  return new Promise((resolve) => (speakerCountAnswer = resolve));
}

function answerSpeakerCount(value) {
  const count = Number(value) || 0;
  if (count) writeLocal("mink-speaker-count", String(count));
  const resolve = speakerCountAnswer;
  speakerCountAnswer = null; // Clear first so closeModal doesn't treat this as a cancel
  closeModal();
  resolve?.(count);
}

async function uploadFile(file) {
  const speakerCount = await askSpeakerCount(`Transcribe "${file.name}"`, "Start transcribing");
  if (speakerCount === null) return;
  const form = new FormData();
  form.append("file", file);
  if (speakerCount) form.append("num_speakers", speakerCount);

  const request = new XMLHttpRequest();
  request.open("POST", "/api/sessions");
  request.upload.onprogress = (event) => {
    if (event.lengthComputable) toast(`Importing ${file.name}: ${Math.round((event.loaded / event.total) * 100)}%`);
  };
  request.onload = async () => {
    if (request.status !== 200) {
      toast(request.responseText || "Upload failed");
      return;
    }
    const session = JSON.parse(request.responseText);
    toast("Imported. Transcription started.");
    await refreshSessionList();
    openSession(session.id);
  };
  request.onerror = () => toast("Upload failed. Is the Mink window still open?");
  request.send(form);
}

function setupDragAndDrop() {
  let depth = 0;
  const hasFiles = (event) => [...(event.dataTransfer?.types || [])].includes("Files");
  window.addEventListener("dragenter", (event) => {
    if (!hasFiles(event)) return;
    depth += 1;
    $("drop-overlay").hidden = false;
  });
  window.addEventListener("dragleave", () => {
    depth = Math.max(0, depth - 1);
    if (depth === 0) $("drop-overlay").hidden = true;
  });
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => {
    event.preventDefault();
    depth = 0;
    $("drop-overlay").hidden = true;
    const file = event.dataTransfer.files[0];
    if (file) uploadFile(file);
  });
}

async function redo(speakersOnly) {
  closeFloating();
  const what = speakersOnly ? "Redo speakers" : "Transcribe again";
  const count = await askSpeakerCount(
    what,
    speakersOnly ? "Redo speakers" : "Transcribe again",
    speakersOnly ? "12" : undefined,
  );
  if (count === null) return;
  state.progressStart = null;
  setSession(
    await api(`/api/sessions/${state.session.id}/retry`, {
      method: "POST",
      json: { num_speakers: count, speakers_only: speakersOnly },
    }),
  );
  refreshSessionList();
}

async function relabelFromFixes() {
  const before = snapshot();
  toast("Comparing every line to your fixes...");
  const result = await api(`/api/sessions/${state.session.id}/relabel`, { method: "POST" });
  setSession(result.session);
  const unsure = result.session.lines.filter((line) => line.unsure).length;
  toast(
    `Moved ${result.moved} lines using fixes for ${result.people} people. ${unsure} lines marked for review.`,
    undoTo(before),
  );
}

// ============ 8b. Tooltips ============
// One shared tooltip for every element with data-tip. Shows on hover and keyboard focus,
// hides on Esc or click, and is linked with aria-describedby for screen readers.

let tooltipTarget = null;
let tooltipTimer = null;

function showTooltip(target) {
  const tip = $("tooltip");
  tooltipTarget = target;
  tip.textContent = target.dataset.tip;
  tip.hidden = false;
  target.setAttribute("aria-describedby", "tooltip");
  const rect = target.getBoundingClientRect();
  const below = rect.bottom + 8 + tip.offsetHeight < window.innerHeight;
  const top = below ? rect.bottom + 8 : rect.top - tip.offsetHeight - 8;
  const left = Math.min(
    Math.max(8, rect.left + rect.width / 2 - tip.offsetWidth / 2),
    window.innerWidth - tip.offsetWidth - 8,
  );
  tip.style.top = `${top}px`;
  tip.style.left = `${left}px`;
}

function hideTooltip() {
  clearTimeout(tooltipTimer);
  $("tooltip").hidden = true;
  tooltipTarget?.removeAttribute("aria-describedby");
  tooltipTarget = null;
}

function wireTooltips() {
  document.addEventListener("pointerover", (event) => {
    const target = event.target.closest?.("[data-tip]");
    if (target === tooltipTarget) return;
    hideTooltip();
    if (target) tooltipTimer = setTimeout(() => showTooltip(target), 400);
  });
  document.addEventListener("focusin", (event) => {
    const target = event.target.closest?.("[data-tip]");
    hideTooltip();
    if (target?.matches(":focus-visible")) showTooltip(target);
  });
  document.addEventListener("focusout", hideTooltip);
  document.addEventListener("pointerdown", hideTooltip);
  document.addEventListener("scroll", hideTooltip, true);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") hideTooltip();
  });
}

// ============ 9. Event wiring ============

let focusBeforePopover = null;

function closeFloating() {
  const popoverWasOpen = !$("speaker-popover").hidden;
  $("speaker-popover").hidden = true;
  $("export-menu").hidden = true;
  document.querySelector('[data-action="toggle-export"]').setAttribute("aria-expanded", "false");
  if (popoverWasOpen && $("speaker-popover").contains(document.activeElement)) focusBeforePopover?.focus();
}

function toggleMenu(id) {
  const menu = $(id);
  const wasHidden = menu.hidden;
  closeFloating();
  menu.hidden = !wasHidden;
  document.querySelector(`[aria-controls="${id}"]`)?.setAttribute("aria-expanded", String(wasHidden));
  if (wasHidden) menu.querySelector("button")?.focus();
}

/** Keep Tab inside an open dialog so keyboard users can't wander behind it. */
function trapFocus(event) {
  const container = !$("modal").hidden ? $("modal") : null;
  if (!container || event.key !== "Tab") return;
  const focusable = [...container.querySelectorAll("button, input, select, textarea, a[href], [tabindex='0']")].filter(
    (el) => !el.disabled && el.offsetParent !== null,
  );
  if (!focusable.length) return;
  const first = focusable[0];
  const last = focusable[focusable.length - 1];
  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

const actions = {
  "go-home": goHome,
  "pick-file": () => $("file-input").click(),
  "open-session": (el) => openSession(el.dataset.id),
  "open-settings": openSettings,
  "open-voices": openVoices,
  "close-modal": closeModal,
  "save-settings": async () => {
    await saveSettings();
    closeModal();
    state.info = await api("/api/info");
    $("engine-label").textContent = `Runs 100% on this computer · ${state.info.engine} · ${state.info.speaker_engine}`;
    toast("Settings saved. They apply to your next import.");
  },
  "remove-fix": (el) => removeFix(Number(el.dataset.index)),
  "delete-voice": async (el) => {
    const url = `/api/voices/${encodeURIComponent(el.dataset.name)}`;
    const { removed } = await api(url, { method: "DELETE" });
    loadVoiceNames();
    openVoices();
    toast(`Forgot ${el.dataset.name}'s voice. Transcripts keep the name.`, async () => {
      await api(url, { method: "PUT", json: removed });
      loadVoiceNames();
      if (!$("modal").hidden) openVoices();
      toast("Voice restored");
    });
  },
  "toggle-export": () => toggleMenu("export-menu"),
  export: async (el) => {
    const url = `/api/sessions/${state.session.id}/export?format=${el.dataset.format}&timestamps=${$("export-timestamps").checked}`;
    closeFloating();
    if (!state.info?.settings.export_folder) {
      window.location = url;
      return;
    }
    const saved = await api(url, { method: "POST" });
    toast(
      `Saved ${saved.name} to ${saved.folder}`,
      () => api(`/api/reveal?path=${encodeURIComponent(saved.path)}`, { method: "POST" }),
      "Show in Finder",
    );
  },
  "rename-session": async () => {
    closeFloating();
    const oldTitle = state.session.title;
    const title = prompt("Rename recording", oldTitle);
    if (!title || title === oldTitle) return;
    const url = `/api/sessions/${state.session.id}`;
    setSession(await api(url, { method: "PATCH", json: { title } }));
    refreshSessionList();
    toast(`Renamed to ${title}`, async () => {
      setSession(await api(url, { method: "PATCH", json: { title: oldTitle } }));
      refreshSessionList();
      toast("Undone");
    });
  },
  "switch-version": async () => {
    setSession(await api(`/api/sessions/${state.session.id}/previous-version/switch`, { method: "POST" }));
    toast("Switched versions. You can switch back any time.");
  },
  "keep-version": async () => {
    setSession(await api(`/api/sessions/${state.session.id}/previous-version`, { method: "DELETE" }));
  },
  stop: async () => {
    if (!confirm("Stop transcribing this recording? You can start it again later.")) return;
    setSession(await api(`/api/sessions/${state.session.id}/stop`, { method: "POST" }));
    refreshSessionList();
  },
  retry: () => redo(false),
  "redo-speakers": () => redo(true),
  relabel: relabelFromFixes,
  "delete-session": async () => {
    closeFloating();
    if (!confirm(`Delete "${state.session.title}" and its audio? This can't be undone.`)) return;
    await api(`/api/sessions/${state.session.id}`, { method: "DELETE" });
    goHome();
    refreshSessionList();
  },
  "speaker-menu": (el) => openSpeakerMenu(el),
  "move-line": (el) => moveLine(el.dataset.line, el.dataset.speaker),
  seek: (el) => seek(Number(el.dataset.time)),
  "toggle-edit": () => setEditing(!state.editing),
  "toggle-replace": () => {
    $("replace-bar").hidden = !$("replace-bar").hidden;
    if (!$("replace-bar").hidden) $("replace-find").focus();
  },
  "replace-all": replaceAll,
  "play-pause": togglePlay,
  skip: (el) => seek(audio().currentTime + Number(el.dataset.seconds), !audio().paused),
  speed: cycleSpeed,
  "search-next": () => stepSearch(1),
  "search-prev": () => stepSearch(-1),
};

document.addEventListener("click", (event) => {
  const target = event.target.closest("[data-action]");
  const insideFloating = event.target.closest(".popover, .menu");
  if (!insideFloating && !target?.dataset.action?.startsWith("toggle-") && target?.dataset.action !== "speaker-menu")
    closeFloating();
  if (event.target === $("modal")) closeModal();

  const textEl = event.target.closest(".line-text");
  if (state.editing && textEl) {
    startEditingLine(textEl);
    return;
  }

  if (!target) return;
  const handler = actions[target.dataset.action];
  if (handler) {
    event.preventDefault();
    handler(target);
  }
});

document.addEventListener("submit", (event) => {
  event.preventDefault();
  const form = event.target;
  if (form.dataset.form === "speaker-count") {
    answerSpeakerCount(new FormData(form).get("count"));
    return;
  }
  const name = new FormData(form).get("name")?.toString().trim();
  if (!name) return;
  if (form.dataset.form === "rename-speaker") renameSpeaker(form.dataset.speaker, name);
  if (form.dataset.form === "new-speaker") moveLine(form.dataset.line, null, name);
});

document.addEventListener("focusout", (event) => {
  if (event.target.classList?.contains("line-text") && event.target.isContentEditable) finishEditingLine(event.target);
});

document.addEventListener("keydown", (event) => {
  trapFocus(event);
  const el = document.activeElement;
  const typing = el && (el.isContentEditable || ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName));

  if (el?.isContentEditable && el.classList.contains("line-text")) {
    if (event.key === "Enter") {
      event.preventDefault();
      splitAtSelection(event).then((didSplit) => {
        if (!didSplit) el.blur();
      });
    }
    if (event.key === "Escape") {
      event.preventDefault();
      cancelEditingLine(el);
    }
    return;
  }
  if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
    event.preventDefault();
    $("search-input").focus();
    $("search-input").select();
    return;
  }
  if (event.key === "Escape") {
    closeFloating();
    closeModal();
    return;
  }
  if (event.key === "Enter" && !typing && !window.getSelection().isCollapsed) {
    splitAtSelection(event);
    return;
  }
  if (event.key === " " && !typing && state.session?.status === "done") {
    event.preventDefault();
    togglePlay();
  }
});

function wireInputs() {
  // Click or select words to jump the audio there. Wait a tick so the selection is final.
  $("transcript").addEventListener("mouseup", (event) => {
    if (event.target.closest(".line-text")) setTimeout(seekToSelection, 0);
  });

  $("file-input").addEventListener("change", (event) => {
    const file = event.target.files[0];
    if (file) uploadFile(file);
    event.target.value = "";
  });

  let searchTimer;
  $("search-input").addEventListener("input", (event) => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => runSearch(event.target.value), 200);
  });
  $("search-input").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      stepSearch(event.shiftKey ? -1 : 1);
    }
    if (event.key === "Escape") {
      resetSearch();
      runSearch("");
      event.target.blur();
    }
  });

  for (const id of ["replace-find", "replace-case", "replace-word"])
    $(id).addEventListener("input", previewReplaceCount);
  $("replace-with").addEventListener("keydown", (event) => {
    if (event.key === "Enter") replaceAll();
  });

  $("dropzone").addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") $("file-input").click();
  });

  $("scrubber").addEventListener("input", (event) => {
    if (audio().duration) audio().currentTime = (event.target.value / 1000) * audio().duration;
  });
  audio().addEventListener("timeupdate", onTimeUpdate);
  audio().addEventListener("play", followWordsWhilePlaying);
  audio().addEventListener("seeked", markCurrentWord);
  audio().addEventListener("loadedmetadata", () => ($("time-total").textContent = clock(audio().duration)));
  audio().addEventListener("play", () => setPlayIcon(true));
  audio().addEventListener("pause", () => setPlayIcon(false));

  document.addEventListener("input", (event) => {
    if (event.target.id === "set-threshold") $("threshold-value").textContent = Number(event.target.value).toFixed(2);
  });
}

async function start() {
  fillIconPlaceholders();
  wireInputs();
  wireTooltips();
  setupDragAndDrop();
  state.info = await api("/api/info");
  $("engine-label").textContent = `Runs 100% on this computer · ${state.info.engine} · ${state.info.speaker_engine}`;
  await refreshSessionList();
  loadVoiceNames();

  const sessionFromUrl = location.hash.slice(1);
  if (sessionFromUrl && state.sessions.some((s) => s.id === sessionFromUrl)) openSession(sessionFromUrl);
  else showView("home");

  checkForUpdate();
  setInterval(checkForUpdate, 60_000);
  setInterval(async () => {
    const anyRunning = state.sessions.some((s) => s.status === "queued" || s.status === "processing");
    if (anyRunning) await refreshSessionList();
    await pollOpenSession();
  }, 2500);
}

start();
