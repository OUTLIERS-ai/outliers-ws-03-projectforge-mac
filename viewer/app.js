/* ProjectForge viewer: plain JavaScript, no dependencies, fully local, no AI.
   Create projects and cards, edit, comment, tag, drag between columns.
   Your moves are sent under your own name (config "human"); the board
   refuses moves from anyone who is not you or the orchestrator. */

const STATUS_LABEL = {
  backlog: "Backlog", ready: "Ready", in_progress: "In Progress",
  blocked: "Blocked", review: "Review",
  awaiting_you: "Awaiting You", done: "Done",
  tracking: "Tracking",
};

/* Awaiting You is first: it is the only column that needs you. */
const COLUMN_ORDER = ["awaiting_you", "backlog", "ready", "in_progress",
  "blocked", "review", "done", "tracking"];

const HOLD_LABEL = {
  wip_cap: "too many in progress", no_owner: "needs an owner",
  yours: "yours - not for an agent", out_of_scope: "outside its departments",
  cap_exhausted: "daily limit reached",
  unknown_agent: "not one of your agents - check the spelling",
};

let STATE = null;
let activeDept = "all";
let openTask = null; // task id of the open card window, to refresh in place
let viewMode = "board";
let searchQ = "";
let showRefused = false; // Activity shows only refusals
let dirty = null;        // the card window has edits not yet saved

function HUMAN() { return (STATE && STATE.config && STATE.config.human) || "you"; }
function ORCH() { return (STATE && STATE.config && STATE.config.orchestrator) || "orchestrator"; }

async function api(path, body) {
  const r = await fetch(path, {
    method: "POST", body: JSON.stringify(body),
    headers: { "Content-Type": "application/json" },
  });
  const j = await r.json();
  if (!r.ok || j.error) toast(j.error || ("error " + r.status));
  j._ok = r.ok && !j.error;
  return j;
}

/* True while you are typing somewhere the refresh would wipe: a box you
   have your cursor in, or a half-filled "+ Add card" form (the refresh
   redraws the columns, so that form would vanish mid-sentence).

   An OPEN CARD WINDOW is deliberately NOT in here any more. The window and
   its boxes live outside the board, so a refresh never touches them, and
   the board used to sit frozen behind an open card for as long as it stayed
   open - showing "4 refused today" while the real figure was 7, with no
   sign on screen that it had stopped. */
function typing() {
  const a = document.activeElement;
  if (a && a.id !== "search" &&
      ["INPUT", "TEXTAREA", "SELECT"].includes(a.tagName)) return true;
  if (document.querySelector(".add-card form")) return true;
  return false;
}

/* When a refresh IS skipped, the header says so, with the time of the last
   one. A board that quietly stops is worse than one that says it stopped. */
function showRefreshState(paused) {
  const el = document.getElementById("refresh-state");
  if (!paused) { el.classList.add("hidden"); return; }
  const t = (STATE && STATE.last_seen) || "";
  el.textContent = `Paused while you are typing${t ? ` - last checked ${t}` : ""}. It carries on when you click away.`;
  el.classList.remove("hidden");
}

function toast(msg) {
  let el = document.getElementById("toast");
  if (!el) {
    el = document.createElement("div");
    el.id = "toast";
    document.body.appendChild(el);
  }
  el.textContent = msg;
  el.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => el.classList.add("hidden"), 6000);
}

function crmLink(rel) {
  if (!rel) return "";
  const vault = (STATE.config.crm_vault || "").replace(/\\/g, "/")
    .replace(/\/+$/, "");
  if (!vault)
    return `<div class="crm-link">CRM person: ${esc(rel)}
      <span class="dimtext">(set crm_vault in config.json to make this a
      link that opens the note in Obsidian)</span></div>`;
  const full = vault + "/" + rel.replace(/\\/g, "/").replace(/^\/+/, "");
  return `<div class="crm-link">CRM person: <a href="obsidian://open?path=${
    encodeURIComponent(full)}">${esc(rel)}</a></div>`;
}

function pfRead(k) { try { return localStorage.getItem(k); } catch (e) { return null; } }
function pfWrite(k, v) { try { localStorage.setItem(k, v); } catch (e) { /* ignore */ } }

async function load() {
  STATE = await (await fetch("/api/state")).json();
  STATE.last_seen = new Date().toTimeString().slice(0, 5);
  showRefreshState(false);
  renderHeader();
  renderView();
  renderEvents();
  renderAlerts();
}

/* ---------- who may do what (shown on screen, from engine/rules.py) ---------- */
function roleOf(name) {
  const n = (name || "").toLowerCase();
  if (!n) return "";
  if (n === HUMAN().toLowerCase()) return "you";
  if (n === ORCH().toLowerCase()) return "orchestrator";
  if ((STATE.config.managers || []).map(x => x.toLowerCase()).includes(n))
    return "manager";
  return "worker";
}
function rulesHtml() {
  const mgr = (STATE.config.managers || []);
  return `
    <h2>Who may do what</h2>
    <p class="note">The board checks every change against these rules and
      refuses the rest. Every refusal is shown in red in the Activity list,
      with the agent's name.</p>
    <table class="rules">
      <tr><th>Who</th><th>May</th><th>May not</th></tr>
      <tr><td><span class="role r-you">you</span> ${esc(HUMAN())}</td>
        <td>anything: open, edit, drag, close cards</td>
        <td>nothing: there is no change you are refused</td></tr>
      <tr><td><span class="role r-manager">manager</span>
        ${mgr.length ? esc(mgr.join(", ")) : "<i>none chosen</i>"}</td>
        <td>open new cards, edit card details, add reports</td>
        <td>move a card between columns</td></tr>
      <tr><td><span class="role r-worker">worker</span> every other agent</td>
        <td>add a work report, a 5-field handover, a comment, an escalation
          (asking for a person: the board then marks the card NEEDS YOU and
          puts it in Awaiting You)</td>
        <td>open a card, choose a card's column, edit a card</td></tr>
      <tr><td><span class="role r-orchestrator">orchestrator</span>
        the /forge-run command</td>
        <td>take a Ready card, save the result and move the card; put a
          card into Awaiting You</td>
        <td>take a card out of Awaiting You</td></tr>
    </table>
    <p class="note">Nothing moves on its own. Type <code>/forge-run</code> in
      Claude Code to hand Ready cards to their agents
      (<code>/forge-run dry</code> previews and changes nothing).</p>
    <p class="note">An example refusal: <code>refused: writer-bot is a worker
      and may not move a card between columns.</code></p>`;
}
function openRules() {
  document.getElementById("modal-body").innerHTML = rulesHtml();
  showModal();
}

/* The board redraws itself every 10 seconds. An alerts panel you opened
   must still be open after that redraw, or the "Open this card ›" link
   goes out from under your hand while you are reaching for it. The state
   is kept here as well as in the browser, so a browser that refuses
   storage still keeps the panel open for as long as the board is on. */
let alertsOpen = null;   // null until it has been read once

function alertsWanted() {
  if (alertsOpen === null) {
    alertsOpen = false;   // default: closed until you open it yourself
    try { alertsOpen = localStorage.getItem("pf-alerts-open") === "1"; }
    catch (e) { /* no storage: fall back to closed */ }
  }
  return alertsOpen;
}

function setAlertsPanel(open) {
  alertsOpen = open;
  try { localStorage.setItem("pf-alerts-open", open ? "1" : "0"); } catch (e) { /* ignore */ }
  document.getElementById("alerts-panel").classList.toggle("hidden", !open);
  document.getElementById("alerts-reopen").classList.toggle("hidden", open);
}

function wireAlertsPanel() {
  const closeBtn = document.getElementById("alerts-close");
  const reopenBtn = document.getElementById("alerts-reopen");
  if (!closeBtn || !reopenBtn) return;
  if (!closeBtn.dataset.wired) {
    closeBtn.dataset.wired = "1";
    closeBtn.onclick = () => setAlertsPanel(false);
    reopenBtn.onclick = () => setAlertsPanel(true);
  }
  setAlertsPanel(alertsWanted());   // every refresh, not only the first
}

async function renderAlerts() {
  wireAlertsPanel();
  const alerts = await (await fetch("/api/alerts")).json();
  noteRows("al", alerts);
  document.getElementById("stat-alerts").textContent = alerts.length;
  countLabel("stat-alerts", alerts.length, "alert", "alerts");
  document.getElementById("alerts-reopen-count").textContent =
    alerts.length ? `(${alerts.length})` : "";
  const box = document.getElementById("alerts");
  const last = STATE && STATE.last_check;
  /* a health check that has stopped is said out loud, here, in red */
  const broken = STATE && STATE.health_error;
  document.getElementById("alerts-last").textContent = broken
    ? "THE CHECK HAS STOPPED"
    : (last ? `last check ${last.slice(11, 16)}` : "not checked yet");
  document.getElementById("alerts-last").classList.toggle("broken", !!broken);
  box.innerHTML = alerts.length ? "" : (last
    ? `<div class="dimtext">All clear at the last check.</div>`
    : `<div class="dimtext">The health check has not run yet.</div>`);
  for (const a of alerts) {
    const el = document.createElement("div");
    el.className = `alert-item ${a.level}`;
    /* an alert that names a card now gives you a way to reach it, and the
       button says what it does instead of showing a bare × */
    const onCard = a.task_id && a.task_id.indexOf("lane:") !== 0 &&
      STATE.tasks.some(t => t.id === a.task_id);
    el.innerHTML = `
      <div class="al-body">
        <div class="al-kind">${esc(a.kind.replace(/-/g, " "))}</div>
        ${esc(a.message)}
      </div>
      <div class="al-foot">
        ${onCard ? `<a href="#" class="al-open">Open this card ›</a>` : `<span></span>`}
        <button class="al-x" title="Put this alert away until the next check">Hide for now</button>
      </div>`;
    if (onCard) {
      el.querySelector(".al-body").onclick = () => openDetail(a.task_id);
      el.querySelector(".al-open").onclick = e => {
        e.preventDefault(); e.stopPropagation(); openDetail(a.task_id);
      };
    }
    el.querySelector(".al-x").onclick = async e => {
      e.stopPropagation();
      await api("/api/alert/dismiss", { id: a.id, actor: HUMAN() });
      toast("Hidden. The next health check puts it back if it is still true.");
      renderAlerts();
    };
    box.appendChild(el);
  }
}
function renderView() {
  document.getElementById("board").dataset.view = viewMode;
  if (viewMode === "agents") renderAgents();
  else if (viewMode === "queue") renderQueue();
  else renderBoard();
}

/* ---------- helpers ---------- */
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g,
    c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function projOf(id) { return STATE.projects.find(p => p.id === id); }
function deptColor(deptId) {
  const d = STATE.config.departments.find(d => d.id === deptId);
  return d ? d.color : "#8a90a3";
}
function tagsOf(t) { try { return JSON.parse(t.tags || "[]"); } catch { return []; } }
function confBadge(t) {
  const m = /confidence=(\d+)/.exec(t.context_ref || "");
  if (!m) return "";
  const n = +m[1];
  const band = n >= 70 ? "hi" : n >= 55 ? "mid" : "lo";
  return `<div class="conf ${band}" title="forecast confidence">
    <span class="conf-bar"><span style="width:${n}%"></span></span>
    <span class="conf-n">${n}%</span></div>`;
}
/* the owner picker: you, then your real agents. A name typed by hand
   elsewhere that is not one of them is shown with a warning. */
function knownAgents() {
  const set = new Set(STATE.config.departments.map(d => d.lead).filter(Boolean));
  (STATE.config.agents || []).forEach(a => set.add(a));
  (STATE.config.managers || []).forEach(a => set.add(a));
  (STATE.config.outward_owners || []).forEach(a => set.add(a));
  return [...set].sort();
}
function isKnownOwner(name) {
  if (!name) return true;
  const ks = knownAgents();
  if (!ks.length) return true; // no agent list: nothing to check against
  return name === HUMAN() || ks.includes(name);
}
function ownerOptions(current) {
  const names = [HUMAN(), ...knownAgents().filter(a => a !== HUMAN())];
  if (!knownAgents().length)
    STATE.tasks.forEach(t => t.assignee_agent && !names.includes(t.assignee_agent)
      && names.push(t.assignee_agent));
  let html = names.map(a => `<option value="${esc(a)}" ${a === current ? "selected" : ""}>${
    esc(a)}${a === HUMAN() && a.toLowerCase() !== "you" ? " (you)" : ""}${
    roleOf(a) === "manager" ? " - manager" : ""}</option>`).join("");
  if (current && !names.includes(current))
    html += `<option value="${esc(current)}" selected>${esc(current)} - not one of your agents</option>`;
  return html;
}
function allTags() {
  const set = new Set();
  STATE.tasks.forEach(t => tagsOf(t).forEach(x => set.add(x)));
  return [...set].sort();
}
function ts2date(ts) { return new Date(String(ts).replace(" ", "T")); }
function checklistOf(t) {
  try { return JSON.parse(t.checklist || "[]"); } catch { return []; }
}
function dueClass(due, status) {
  if (!due || status === "done") return "";
  const days = (ts2date(due + " 23:59:59") - Date.now()) / 864e5;
  if (days < 0) return "over";
  if (days <= 2) return "soon";
  return "ok";
}
function matches(t) {
  if (t.archived) return false;
  const proj = projOf(t.project_id) || {};
  if (activeDept !== "all" && proj.department !== activeDept) return false;
  if (!searchQ) return true;
  const hay = [t.title, t.assignee_agent, proj.title,
    ...tagsOf(t)].join(" ").toLowerCase();
  return searchQ.toLowerCase().split(/\s+/).every(w => hay.includes(w));
}
function ago(ts) {
  const d = (Date.now() - ts2date(ts)) / 864e5;
  if (d < 1 / 24) return "just now";
  if (d < 1) return `${Math.round(d * 24)}h ago`;
  return `${Math.floor(d)}d ago`;
}
const FIELD_LABEL = { assignee_agent: "owner", context_ref: "link or file",
  crm_person: "CRM person", project_id: "project" };
function lab(s) { return STATUS_LABEL[s] || s; }
function countLabel(id, n, singular, plural) {
  const stat = document.getElementById(id);
  const el = stat && stat.parentElement.querySelector("label");
  if (el) el.textContent = n === 1 ? singular : plural;
}
/* What the change happened to, in words: "the card Write 3 posts (Content
   week 39)". Before this every row read "created this" and the list was a
   wall of pronouns with nothing in it you could act on. */
function evWhat(ev, withProject) {
  if (!ev.entity_title)
    return ev.entity_type === "project" ? "a project" : "a card";
  const kind = ev.entity_type === "project" ? "the project" : "the card";
  const proj = withProject && ev.parent_title
    ? ` <span class="ev-proj">(${esc(ev.parent_title)})</span>` : "";
  return `${kind} <b>${esc(ev.entity_title)}</b>${proj}`;
}
function evText(ev) {
  let d = {};
  try { d = JSON.parse(ev.detail || "{}"); } catch { d = {}; }
  const what = evWhat(ev, true);
  const it = evWhat(ev, false);
  switch (ev.action) {
    case "moved": return d.reason
      ? `<b>${esc(ev.actor)}</b> asked for a person on ${what}, so it moved ${esc(lab(d.from))} → <b>Awaiting You</b>`
      : `<b>${esc(ev.actor)}</b> moved ${it} ${esc(lab(d.from))} → ${esc(lab(d.to))}`;
    case "handoff": return `<b>${esc(d.from)}</b> handed ${it} to <b>${esc(d.to)}</b> — ${esc(d.summary || "")}`;
    case "created": return ev.entity_type === "project"
      ? `<b>${esc(ev.actor)}</b> opened ${what}`
      : `<b>${esc(ev.actor)}</b> added ${what}`;
    case "comment": return `<b>${esc(ev.actor)}</b> on ${it}: ${esc(d.text || "")}`;
    case "tagged": return `<b>${esc(ev.actor)}</b> tagged ${it}: ${esc((d.tags || []).join(", ") || "none")}`;
    case "pass": return `<b>${esc(ev.actor)}</b> reported on ${it} (${esc(d.result)}) — ${esc(d.summary || "")}`;
    case "updated": return `<b>${esc(ev.actor)}</b> edited ${esc((d.fields || []).map(f => FIELD_LABEL[f] || f).join(", "))} on ${it}`;
    case "dispatched": return `<b>${esc(ev.actor)}</b> handed ${it} to <b>${esc(d.agent)}</b>`;
    case "refused": return `<span class="refused-text">REFUSED</span> ${
      esc((d.message || "").replace(/^refused:\s*/i, ""))}`;
    case "escalation": return `<span class="refused-text">NEEDS YOU</span> <b>${esc(ev.actor)}</b> on ${what}: ${esc(d.text || "")}`;
    case "checklist": return `<b>${esc(ev.actor)}</b> ticked the checklist on ${it} (${esc(d.done)}/${esc(d.total)} done)`;
    case "auto-assigned": return `<b>${esc(ev.actor)}</b> gave ${it} to <b>${esc(d.agent)}</b>`;
    case "federated": return `<b>${esc(ev.actor)}</b> sent ${what} in from ${esc(d.source_app || "another program")}`;
    case "archived": return `<b>${esc(ev.actor)}</b> archived ${it}`;
    case "auto-queued": return `<b>${esc(ev.actor)}</b> moved ${it} Backlog → Ready`;
    case "reaped": return `<b>${esc(ev.actor)}</b> sent ${it} back to Ready: no report within ${esc(d.ttl_min)} minutes`;
    default: return `<b>${esc(ev.actor)}</b> ${esc(ev.action)} ${it}`;
  }
}

/* ---------- header ---------- */
function renderHeader() {
  document.getElementById("workspace").textContent = STATE.config.workspace;
  const open = STATE.tasks.filter(
    t => t.status !== "done" && t.status !== "tracking");
  document.getElementById("stat-projects").textContent =
    STATE.projects.filter(p => p.status === "active").length;
  document.getElementById("stat-open").textContent = open.length;
  document.getElementById("stat-waiting").textContent =
    open.filter(t => t.status === "awaiting_you" && !t.archived).length;
  document.getElementById("stat-refused").textContent =
    STATE.refused_today ?? 0;
  /* cards where an agent has asked for a person: the one change that needs
     you, and the one that used to leave no mark anywhere */
  const needs = STATE.tasks.filter(t => t.escalated && !t.archived).length;
  const nBtn = document.getElementById("stat-needs-btn");
  document.getElementById("stat-needs").textContent = needs;
  nBtn.classList.toggle("lit", needs > 0);
  /* "1 projects" read wrong every time you had 1 project */
  countLabel("stat-projects",
    STATE.projects.filter(p => p.status === "active").length,
    "project", "projects");

  const pills = document.getElementById("dept-pills");
  pills.innerHTML = "";
  const mk = (id, name, color) => {
    const el = document.createElement("button");
    el.className = "pill" + (activeDept === id ? " active" : "");
    el.style.setProperty("--pc", color);
    el.textContent = name;
    el.onclick = () => { activeDept = id; renderHeader(); renderView(); };
    pills.appendChild(el);
  };
  mk("all", "All", "#ff3c00");
  STATE.config.departments.forEach(d => mk(d.id, d.name, d.color));
}

function jumpToAwaiting() {
  if (viewMode !== "board") setView("board");
  const col = document.querySelector('.col[data-status="awaiting_you"]');
  if (!col) return;
  col.scrollIntoView({ behavior: "smooth", inline: "start", block: "nearest" });
  col.classList.add("flash");
  setTimeout(() => col.classList.remove("flash"), 1400);
}

/* ---------- board ---------- */
function firstRunPanel() {
  const live = STATE.tasks.filter(t => !t.archived).length;
  if (live) return null;
  const el = document.createElement("div");
  el.className = "first-run";
  el.innerHTML = `
    <h2>Your board is empty. 3 steps to your first card:</h2>
    <ol>
      <li><b>Make a project.</b> Every card lives inside one.
        <button id="fr-project">+ Project</button></li>
      <li><b>Add a card.</b> "+ Add card" sits at the TOP of every column,
        under its heading. Use the one under Ready, and pick the agent who
        owns it.</li>
      <li><b>In Claude Code, type <code>/forge-run dry</code></b> to see who
        would get it. Nothing changes until you run <code>/forge-run</code>.</li>
    </ol>
    <p class="dimtext">Want to look round a full board first? In a terminal in
      this folder: <code>${(STATE.config && STATE.config.python) || "python"} tools/demo_board.py --out demo --serve</code>,
      then open http://127.0.0.1:3029.</p>`;
  el.querySelector("#fr-project").onclick = openNewProject;
  return el;
}

function renderBoard() {
  const board = document.getElementById("board");
  board.innerHTML = "";
  const fr = firstRunPanel();
  if (fr) board.appendChild(fr);

  /* a search that finds nothing used to look exactly like an empty board */
  if (searchQ && !STATE.tasks.some(t => matches(t))) {
    const none = document.createElement("div");
    none.className = "no-hits";
    none.innerHTML = `<div>No card matches <b>${esc(searchQ)}</b>.</div>
      <button id="clear-search">Clear the search</button>`;
    none.querySelector("#clear-search").onclick = () => {
      searchBox.value = ""; searchQ = ""; renderView();
    };
    board.appendChild(none);
    renderColJump();
    return;
  }

  for (const status of COLUMN_ORDER.filter(s => STATE.statuses.includes(s))) {
    const tasks = STATE.tasks.filter(t => t.status === status && matches(t));
    // Tracking only matters once another program sends facts to it
    if (status === "tracking" &&
        !STATE.tasks.some(t => t.status === "tracking" && !t.archived))
      continue;
    const col = document.createElement("div");
    col.className = "col" + (status === "awaiting_you" ? " yours" : "");
    col.dataset.status = status;

    col.innerHTML = `<div class="col-head"><h3>${STATUS_LABEL[status]}</h3>
      <span class="count">${tasks.length}</span></div>`;

    /* "+ Add card" sits under the heading, not at the foot of the column.
       At the foot it went off the bottom of the screen on a busy board -
       measured at 2,800 px below the fold on a 1366 x 768 laptop - and
       nothing on screen said it was down there. */
    const add = document.createElement("div");
    add.className = "add-card";
    add.textContent = "+ Add card";
    add.onclick = () => addCardForm(add, status);
    col.appendChild(add);

    const zone = document.createElement("div");
    zone.className = "col-cards";
    zone.ondragover = e => { e.preventDefault(); zone.classList.add("drag-over"); };
    zone.ondragleave = () => zone.classList.remove("drag-over");
    zone.ondrop = async e => {
      e.preventDefault();
      zone.classList.remove("drag-over");
      const id = e.dataTransfer.getData("text/plain");
      const t = STATE.tasks.find(x => x.id === id);
      if (t && t.status === "in_progress" && status === "done" &&
          !confirm(`${t.assignee_agent || "An agent"} is still working on ` +
                   `this. Close it anyway?`)) return;
      const r = await api("/api/task/move", { task_id: id, status, actor: HUMAN() });
      if (r._ok && t && t.status === "in_progress" && status === "done")
        await api("/api/task/comment", { task_id: id, actor: HUMAN(),
          text: "Closed by hand while the agent was still working." });
      load();
    };

    for (const t of tasks) zone.appendChild(cardEl(t));
    col.appendChild(zone);
    board.appendChild(col);
  }
  renderColJump();
}

/* The bar under the header. It appears only when the columns are wider
   than the window, names every column with its number of cards, and
   scrolls to the one you click - so DONE, which sits off the right-hand
   edge on a 1366-wide laptop, can always be reached. */
/* the sticky panels need to know how tall the header actually is: it is
   2 rows on a laptop and 1 on a wide screen */
function syncStickyTops() {
  const r = document.documentElement.style;
  r.setProperty("--hdr",
    Math.round(document.querySelector("header").getBoundingClientRect().height)
    + "px");
  const jump = document.getElementById("col-jump");
  r.setProperty("--jump", jump.classList.contains("hidden") ? "0px"
    : Math.round(jump.getBoundingClientRect().height) + "px");
}

function renderColJump() {
  const bar = document.getElementById("col-jump");
  const board = document.getElementById("board");
  const cols = viewMode === "board" ? [...board.querySelectorAll(".col")] : [];
  const over = cols.length && board.scrollWidth > board.clientWidth + 4;
  bar.classList.toggle("hidden", !over);
  if (!over) { syncStickyTops(); return; }
  bar.innerHTML = `<span class="cj-label">Not every column fits on screen.
    Jump to:</span>`;
  for (const col of cols) {
    const b = document.createElement("button");
    b.textContent = `${col.querySelector("h3").textContent} ` +
      `${col.querySelector(".count").textContent}`;
    b.onclick = () => {
      col.scrollIntoView({ behavior: "smooth", inline: "start",
        block: "nearest" });
      col.classList.add("flash");
      setTimeout(() => col.classList.remove("flash"), 1400);
    };
    bar.appendChild(b);
  }
  syncStickyTops();
}

function cardEl(t) {
  const proj = projOf(t.project_id) || {};
  const card = document.createElement("div");
  card.className = "card";
  card.draggable = true;
  card.style.setProperty("--dc", deptColor(proj.department));
  const tags = tagsOf(t);
  const cl = checklistOf(t);
  const clDone = cl.filter(i => i.done).length;
  const dc = dueClass(t.due, t.status);
  card.innerHTML = `
    ${t.escalated ? `<div class="needs-you" title="An agent has asked for a person on this card">NEEDS YOU</div>` : ""}
    <div class="title">${t.priority !== "normal" && t.priority ?
      `<span class="prio p-${esc(t.priority)}" title="${esc(t.priority)}"></span>` : ""}${esc(t.title)}</div>
    ${t.status === "in_progress" ? `<div class="working" title="dispatched to an agent">
      <span class="wdot"></span>${esc(t.assignee_agent || "agent")} working…</div>` : ""}
    ${tags.length ? `<div class="card-tags">${tags.slice(0, 4).map(x =>
      `<span class="chip mini">${esc(x)}</span>`).join("")}${
      tags.length > 4 ? `<span class="chip mini">+${tags.length - 4}</span>` : ""}</div>` : ""}
    ${confBadge(t)}
    ${(t.due || cl.length) ? `<div class="card-meta">
      ${t.due ? `<span class="due ${dc}">${dc === "over" ? "⚠ " : ""}${esc(t.due)}</span>` : ""}
      ${cl.length ? `<span class="cl-progress">
        <span class="cl-count">${clDone}/${cl.length}</span>
        <span class="bar"><span class="fill" style="width:${
          Math.round(100 * clDone / cl.length)}%"></span></span></span>` : ""}
    </div>` : ""}
    <div class="proj">${esc(proj.title || "")}</div>
    <div class="foot">
      <span class="agent ${t.assignee_agent ? "" : "none"}">
        ${esc(t.assignee_agent || "unassigned")}</span>
      <span class="src">${t.pass_count ? `⟳${t.pass_count} · ` : ""}${esc(ago(t.updated))}</span>
    </div>`;
  card.tabIndex = 0;
  card.setAttribute("role", "button");
  card.ondragstart = e => {
    e.dataTransfer.setData("text/plain", t.id);
    card.classList.add("dragging");
  };
  card.ondragend = () => card.classList.remove("dragging");
  card.onclick = () => openDetail(t.id);
  card.onkeydown = e => { if (e.key === "Enter") openDetail(t.id); };
  return card;
}

function addCardForm(anchor, status) {
  if (anchor.querySelector("form")) return;
  const projs = STATE.projects.filter(p => p.status === "active" &&
    (activeDept === "all" || p.department === activeDept));
  anchor.innerHTML = "";
  const f = document.createElement("form");
  const noProject = !projs.length;
  f.innerHTML = `
    <label class="mini-label">Card title<input name="title"
      placeholder="e.g. Draft the January newsletter" autocomplete="off"
      required></label>
    ${noProject ? `<div class="form-hint">Cards live inside a project. Name
        your first project:</div>
      <input name="newproj" placeholder="Project name, e.g. Content week 39"
        autocomplete="off" required>
      <label class="mini-label">Department<select name="dept">${STATE.config.departments.map(d =>
        `<option value="${esc(d.id)}" ${d.id === activeDept ? "selected" : ""}>${
        esc(d.name)}</option>`).join("")}</select></label>`
    : `<label class="mini-label">Project<select name="project">${projs.map(p =>
      `<option value="${esc(p.id)}">${esc(p.title)}</option>`).join("")}</select></label>`}
    <label class="mini-label">Owner<select name="agent" title="Who owns this card">
      <option value="">no owner yet</option>
      ${ownerOptions("")}</select></label>
    <div class="form-row">
      <button type="submit">Add</button>
      <button type="button" class="ghost" data-x>Cancel</button>
    </div>`;
  f.onsubmit = async e => {
    e.preventDefault();
    let pid = noProject ? "" : f.project.value;
    if (noProject) {
      const r = await api("/api/project/add", {
        title: f.newproj.value.trim(), department: f.dept.value,
        actor: HUMAN() });
      if (!r._ok) return;
      pid = r.id;
    }
    await api("/api/task/add", {
      project_id: pid, title: f.title.value.trim(),
      assignee_agent: f.agent.value, status, actor: HUMAN(),
    });
    load();
  };
  f.querySelector("[data-x]").onclick = e => { e.stopPropagation(); load(); };
  anchor.onclick = null;
  anchor.appendChild(f);
  f.title.focus();
}

function openNewProject() {
  const body = document.getElementById("modal-body");
  body.innerHTML = `
    <h2>New project</h2>
    <form id="np-form" class="stack">
      <label>Title <input name="title" required autocomplete="off"
        placeholder="e.g. Content week 39"></label>
      <label>Department <select name="department">${
        STATE.config.departments.map(d =>
          `<option value="${esc(d.id)}">${esc(d.name)}</option>`).join("")
      }</select></label>
      <label>Summary <textarea name="summary" rows="3"
        placeholder="What this project is for (you can leave this empty)"></textarea></label>
      <div class="form-row"><button type="submit">Create</button>
        <button type="button" class="ghost" id="np-cancel">Cancel</button></div>
    </form>`;
  document.getElementById("np-cancel").onclick = () => closeModal(true);
  document.getElementById("np-form").onsubmit = async e => {
    e.preventDefault();
    const f = e.target;
    const r = await api("/api/project/add", {
      title: f.title.value.trim(), department: f.department.value,
      summary: f.summary.value.trim(), actor: HUMAN(),
    });
    if (!r._ok) return;
    closeModal(true);
    load();
  };
  showModal();
}

/* ---------- project editor ---------- */
function openProject(projectId) {
  const p = projOf(projectId);
  if (!p) return;
  const pTasks = STATE.tasks.filter(t => t.project_id === p.id && !t.archived);
  const openT = pTasks.filter(t => t.status !== "done");
  const archived = STATE.tasks.filter(t =>
    t.project_id === p.id && t.archived).length;
  const body = document.getElementById("modal-body");
  body.innerHTML = `
    <input id="p-title" class="title-edit" value="${esc(p.title)}">
    <div class="mono">${esc(p.id)} · via ${esc(p.source_app)} ·
      ${openT.length} open / ${pTasks.length} cards${
      archived ? ` · ${archived} archived` : ""}</div>
    <div class="grid2">
      <label>Department
        <select id="p-dept">${STATE.config.departments.map(d =>
          `<option value="${esc(d.id)}" ${d.id === p.department ? "selected" : ""}>
           ${esc(d.name)}</option>`).join("")}</select>
      </label>
      <label>Status
        <select id="p-status">${["active", "paused", "done", "archived"].map(s =>
          `<option value="${s}" ${s === p.status ? "selected" : ""}>${s}</option>`).join("")}
        </select>
      </label>
    </div>
    <h4>Summary</h4>
    <textarea id="p-summary" rows="3">${esc(p.summary)}</textarea>
    <div class="form-row"><button id="p-save">Save changes</button>
      <span id="p-saved" class="saved"></span></div>
    <h4>Cards</h4>
    <div class="proj-tasks">${pTasks.length ? pTasks.map(t => `
      <div class="proj-task" data-id="${esc(t.id)}">
        <span class="badge-lane">${STATUS_LABEL[t.status]}</span>
        <span class="pt-title">${esc(t.title)}</span>
        <span class="agent ${t.assignee_agent ? "" : "none"}">
          ${esc(t.assignee_agent || "—")}</span>
      </div>`).join("") : `<div class="dimtext">No cards yet.</div>`}</div>`;
  document.getElementById("p-save").onclick = async () => {
    await api("/api/project/update", {
      project_id: p.id, actor: HUMAN(),
      title: document.getElementById("p-title").value.trim(),
      department: document.getElementById("p-dept").value,
      status: document.getElementById("p-status").value,
      summary: document.getElementById("p-summary").value.trim(),
    });
    document.getElementById("p-saved").textContent = "saved ✓";
    await load();
    setTimeout(() => openProject(p.id), 300);
  };
  document.querySelectorAll(".proj-task").forEach(el =>
    el.onclick = () => openDetail(el.dataset.id));
  showModal();
}

/* ---------- agents view ---------- */
async function renderAgents() {
  const ag = await (await fetch("/api/agents")).json();
  const board = document.getElementById("board");
  board.innerHTML = "";
  // every configured agent gets a column, managers first; you last
  const order = { manager: 0, worker: 1, orchestrator: 2, you: 3 };
  const names = [...new Set([...knownAgents(), ...Object.keys(ag)])]
    .sort((a, b) => order[roleOf(a)] - order[roleOf(b)] ||
      ((ag[b] || {}).open || []).length - ((ag[a] || {}).open || []).length ||
      a.localeCompare(b));
  const head = document.createElement("div");
  head.className = "agents-key";
  head.innerHTML = `<b>Managers</b> open cards. <b>Workers</b> add reports.
    Only <b>/forge-run</b> and <b>you</b> move cards.
    <a href="#" id="ak-rules">Who may do what</a>
    <span class="key-dots">Last 8 results, newest first:
      <span class="s-dot r-completed"></span> completed
      <span class="s-dot r-progressed"></span> progressed
      <span class="s-dot r-needs-review"></span> needs review
      <span class="s-dot r-blocked"></span> blocked
      <span class="s-dot r-failed"></span> failed</span>`;
  head.querySelector("#ak-rules").onclick = e => { e.preventDefault(); openRules(); };
  board.appendChild(head);
  if (!names.length) {
    const d = document.createElement("div");
    d.className = "dimtext"; d.style.padding = "30px";
    d.textContent = "No agents yet. Re-run install.py after adding agent files.";
    board.appendChild(d);
    return;
  }
  const row = document.createElement("div");
  row.className = "agents-row";
  board.appendChild(row);
  for (const name of names) {
    const info = ag[name] || { open: [], results: [], last_pass: null };
    const openCards = info.open.filter(o => {
      const t = STATE.tasks.find(x => x.id === o.id);
      return !t || matches(t);
    });
    const role = roleOf(name);
    const col = document.createElement("div");
    col.className = "col agent-col";
    col.innerHTML = `
      <div class="col-head">
        <h3>${esc(name)}</h3>
        <span class="count">${openCards.length}</span>
      </div>
      <div class="role-line"><span class="role r-${role}">${role}</span></div>
      <div class="agent-meta">
        ${info.results.length ? `<div class="streak" title="last 8 work report results, newest first">
          ${info.results.map(r => `<span class="s-dot r-${esc(r)}" title="${esc(r)}"></span>`).join("")}
        </div>` : ""}
        ${info.last_pass ? `<div class="lastpass">
          <span class="badge r-${esc(info.last_pass.result)}">${esc(info.last_pass.result)}</span>
          <span class="ts">${esc(ago(info.last_pass.ts))}</span>
          <div class="lp-sum">${esc(info.last_pass.summary)}</div>
          <div class="lp-task">on: ${esc(info.last_pass.task_title || info.last_pass.task_id)}</div>
        </div>` : `<div class="dimtext">No work reports yet.</div>`}
      </div>
      <div class="col-cards"></div>`;
    const zone = col.querySelector(".col-cards");
    for (const o of openCards) {
      const mini = document.createElement("div");
      mini.className = "card mini-card";
      const dc = dueClass(o.due, o.status);
      mini.innerHTML = `
        <div class="title">${o.priority !== "normal" && o.priority ?
          `<span class="prio p-${esc(o.priority)}"></span>` : ""}${esc(o.title)}</div>
        <div class="foot">
          <span class="src">${esc(STATUS_LABEL[o.status] || o.status)}</span>
          ${o.due ? `<span class="due ${dc}">${esc(o.due)}</span>` : ""}
        </div>`;
      mini.onclick = () => openDetail(o.id);
      zone.appendChild(mini);
    }
    row.appendChild(col);
  }
}

/* ---------- orchestrator dispatch queue (Layer 5, read-only) ---------- */
async function renderQueue() {
  const board = document.getElementById("board");
  board.innerHTML = "";
  const q = await (await fetch("/api/next?limit=25")).json();
  const w = q.wip || {};
  const cap = w.cap ? ` (limit ${w.cap})` : "";

  const wrap = document.createElement("div");
  wrap.className = "queue-wrap";
  wrap.innerHTML = `
    <div class="queue-head">
      <div>
        <h2 class="q-title">Ready for agents</h2>
        <div class="q-sub">The Ready cards in the order /forge-run would
          hand them out: priority, then due date, then oldest first.</div>
      </div>
      <div class="q-wip ${w.at_cap ? "at-cap" : ""}">
        <div class="q-wip-num">${w.in_progress ?? "–"}</div>
        <label>in progress${cap}${w.at_cap ? " · limit reached" : ""}</label>
      </div>
      <div class="q-wip">
        <div class="q-wip-num">${q.ready_count ?? 0}</div>
        <label>ready</label>
      </div>
    </div>
    <div class="q-explain">Nothing here moves on its own. Type
      <code>/forge-run</code> in Claude Code to hand these cards to their
      agents (<code>/forge-run dry</code> only previews). Only /forge-run and
      you move cards; agents can only add reports.
      <a href="#" id="q-rules">Who may do what</a></div>
    <div class="queue-list"></div>`;
  wrap.querySelector("#q-rules").onclick = e => { e.preventDefault(); openRules(); };
  const list = wrap.querySelector(".queue-list");

  const items = (q.next || []).filter(n => {
    const t = STATE.tasks.find(x => x.id === n.task_id);
    return !t || matches(t);
  });
  if (!items.length) {
    list.innerHTML = `<div class="dimtext" style="padding:24px">
      Nothing in Ready. A card reaches Ready when you drag it there or add it
      there, or when /forge-run picks up a Backlog card that has an owner.</div>`;
  }
  let rank = 0;
  for (const n of items) {
    rank++;
    const flag = n.dispatchable
      ? `<span class="q-flag go">▶ can start now</span>`
      : `<span class="q-flag hold">⏸ ${esc(HOLD_LABEL[n.hold_reason] || "waiting")}</span>`;
    const dc = dueClass(n.due, "ready");
    const item = document.createElement("div");
    item.className = "q-item" + (n.dispatchable ? " is-go" : "");
    item.style.setProperty("--dc", deptColor(n.department));
    item.innerHTML = `
      <div class="q-rank">${rank}</div>
      <div class="q-main">
        <div class="q-line">
          ${n.priority && n.priority !== "normal"
            ? `<span class="prio p-${esc(n.priority)}"></span>` : ""}
          <span class="q-name">${esc(n.title)}</span>
        </div>
        <div class="q-meta">
          <span class="proj">${esc(n.project || "—")}</span>
          ${n.due ? `<span class="due ${dc}">${esc(n.due)}</span>` : ""}
          ${n.source_app && n.source_app !== "manual"
            ? `<span class="src">via ${esc(n.source_app)}</span>` : ""}
        </div>
      </div>
      <div class="q-right">
        <span class="agent ${n.assignee_agent ? "" : "none"}">${
          esc(n.assignee_agent || "no owner")}</span>
        ${flag}
      </div>`;
    item.onclick = () => openDetail(n.task_id);
    list.appendChild(item);
  }
  board.appendChild(wrap);
}

/* ---------- activity feed ---------- */
async function renderEvents() {
  let events = await (await fetch("/api/events")).json();
  noteRows("ev", events);
  const box = document.getElementById("events");
  box.innerHTML = "";
  const btn = document.getElementById("refused-filter");
  btn.textContent = showRefused ? "show everything" : "show refusals only";
  if (showRefused) events = events.filter(e => e.action === "refused");
  /* Making your first card writes 2 rows: the project, then the card. They
     read as the same line twice, so the project row is folded into the card
     row when the same person did both within a minute. */
  if (!showRefused) events = events.filter((e, i) => {
    if (e.action !== "created" || e.entity_type !== "project") return true;
    const next = events[i - 1];   // newest first, so the card is BEFORE it
    if (!next || next.action !== "created" || next.entity_type !== "task")
      return true;
    if (next.actor !== e.actor) return true;
    if (Math.abs(ts2date(next.ts) - ts2date(e.ts)) > 60000) return true;
    next._alsoProject = e.entity_title;
    return false;
  });
  if (!events.length)
    box.innerHTML = `<div class="dimtext">${showRefused
      ? "No refusals in the last 40 changes." : "Nothing has happened yet."}</div>`;
  for (const ev of events) {
    const el = document.createElement("div");
    el.className = "ev" + (ev.action === "refused" || ev.action === "escalation"
      ? " ev-bad" : "");
    const extra = ev._alsoProject
      ? ` <span class="ev-proj">(and opened the project ${
          esc(ev._alsoProject)})</span>` : "";
    el.innerHTML = `<div class="dot ev-${esc(ev.action)}"></div>
      <div class="body">${evText(ev)}${extra}<div class="ts">${esc(ev.ts)}</div></div>`;
    if (ev.entity_type === "task" && ev.entity_id && ev.entity_id !== "-" &&
        STATE.tasks.some(t => t.id === ev.entity_id)) {
      el.classList.add("clickable");
      el.onclick = () => openDetail(ev.entity_id);
    }
    box.appendChild(el);
  }
}

/* ---------- dossier modal ---------- */
async function openDetail(taskId) {
  openTask = taskId;
  const d = await (await fetch("/api/task/" + taskId)).json();
  const t = d.task, proj = d.project || {};
  const tags = tagsOf(t);
  const events = d.events; // oldest first

  /* health flags — spot what could be going wrong */
  const flags = [];
  const idleDays = (Date.now() - ts2date(t.updated)) / 864e5;
  if (t.status !== "done" && idleDays >= 5)
    flags.push(`Stale — no activity for ${Math.floor(idleDays)} days`);
  if (t.status === "blocked") flags.push("Blocked — needs unblocking");
  if (!t.assignee_agent && ["ready", "in_progress", "review"].includes(t.status))
    flags.push("In an active lane with no agent assigned");
  let backMoves = 0;
  for (const ev of events.filter(e => e.action === "moved")) {
    const det = JSON.parse(ev.detail || "{}");
    if (STATE.statuses.indexOf(det.to) < STATE.statuses.indexOf(det.from)) backMoves++;
  }
  if (backMoves)
    flags.push(`Moved backwards ${backMoves}× — possible rework loop`);

  /* agent track — origination → every handoff → current owner */
  const created = events.find(e => e.action === "created");
  const track = [{
    agent: created ? created.actor : "?", note: "opened the card",
    ts: created ? created.ts : t.created,
  }];
  for (const h of d.handoffs)
    track.push({ agent: h.to_agent, note: h.summary, ts: h.ts, ctx: h.context });
  if (t.assignee_agent && track[track.length - 1].agent !== t.assignee_agent)
    track.push({ agent: t.assignee_agent, note: "current owner", ts: t.updated });

  /* time in current lane */
  const lastMove = [...events].reverse().find(e => e.action === "moved");
  const laneSince = lastMove ? lastMove.ts : t.created;

  const comments = events.filter(e => e.action === "comment");

  const body = document.getElementById("modal-body");
  body.innerHTML = `
    <input id="d-title" class="title-edit" value="${esc(t.title)}">
    <div class="mono">${esc(t.id)} · ${STATUS_LABEL[t.status]} ·
      <a href="#" id="d-projlink" title="Open the project">${esc(proj.title || "?")}</a>
      · in this column ${esc(ago(laneSince))}</div>

    ${crmLink(t.crm_person)}
    ${flags.length ? `<div class="flagbox">${flags.map(f =>
      `<div>⚠ ${esc(f)}</div>`).join("")}</div>` : ""}

    <div class="grid2">
      <label>Owner
        <select id="d-agent"><option value="">no owner</option>${
          ownerOptions(t.assignee_agent)}</select>
        ${isKnownOwner(t.assignee_agent) ? "" : `<span class="warn-text">Not
          one of your agents: /forge-run will not hand this card out.</span>`}
      </label>
      <label>Link or file
        <input id="d-ctx" value="${esc(t.context_ref)}"
          placeholder="path / url / inbox entry">
      </label>
      <label>CRM person note
        <input id="d-crm" value="${esc(t.crm_person || "")}"
          placeholder="People/Name.md (inside your CRM vault)">
      </label>
      <label>Due date
        <input id="d-due" type="date" value="${esc(t.due || "")}">
      </label>
      <label>Priority
        <select id="d-prio">${["low", "normal", "high", "urgent"].map(p =>
          `<option value="${p}" ${p === (t.priority || "normal") ? "selected" : ""}>${p}</option>`).join("")}
        </select>
      </label>
      <label>Project
        <select id="d-proj">${STATE.projects.filter(p =>
          p.status !== "archived" || p.id === t.project_id).map(p =>
          `<option value="${esc(p.id)}" ${p.id === t.project_id ? "selected" : ""}>
           ${esc(p.title)}</option>`).join("")}</select>
      </label>
      <label>Column
        <select id="d-lane">${STATE.statuses.map(s =>
          `<option value="${s}" ${s === t.status ? "selected" : ""}>${STATUS_LABEL[s]}</option>`).join("")}
        </select>
      </label>
    </div>

    <h4>Tags</h4>
    <div class="chips" id="d-chips">
      ${tags.map(x => `<span class="chip">${esc(x)}
        <button data-tag="${esc(x)}">×</button></span>`).join("")}
      <input id="d-newtag" list="taglist" placeholder="+ tag, Enter">
      <datalist id="taglist">${allTags().map(x =>
        `<option value="${esc(x)}">`).join("")}</datalist>
    </div>

    <h4>Notes</h4>
    <textarea id="d-notes" rows="4"
      placeholder="What this card is about, decisions, anything worth keeping…">${esc(t.notes)}</textarea>
    <div class="form-row"><button id="d-save">Save changes</button>
      <button id="d-archive" class="ghost danger">
        ${t.archived ? "Restore" : "Archive"}</button>
      <span id="d-saved" class="saved"></span></div>

    <h4>Checklist${(() => {
      const cl = checklistOf(t);
      return cl.length ? ` — ${cl.filter(i => i.done).length}/${cl.length}` : "";
    })()}</h4>
    <div class="checklist" id="d-checklist">
      ${checklistOf(t).map((i, n) => `
        <div class="check-item">
          <input type="checkbox" data-n="${n}" ${i.done ? "checked" : ""}>
          <span class="${i.done ? "done" : ""}">${esc(i.text)}</span>
          <button class="rm" data-rm="${n}">×</button>
        </div>`).join("")}
      <input id="d-newcheck" placeholder="+ checklist item, Enter">
    </div>

    <h4>Work reports: what each agent did</h4>
    <div class="passes">${d.passes.length ? d.passes.map(p => {
      let outs = []; try { outs = JSON.parse(p.outputs || "[]"); } catch {}
      return `<div class="pass">
        <div class="pass-head">
          <span class="agent">${esc(p.agent)}</span>
          <span class="badge r-${esc(p.result)}">${esc(p.result)}</span>
          <span class="ts">${esc(p.ts)}</span>
        </div>
        <div class="pass-sum">${esc(p.summary)}</div>
        ${outs.length ? `<div class="pass-outs">${outs.map(o =>
          `<code>${esc(o)}</code>`).join("")}</div>` : ""}
        ${p.next_step ? `<div class="pass-next">Next step: ${esc(p.next_step)}</div>` : ""}
      </div>`;
    }).join("") : `<div class="dimtext">No work reports yet. Agents log one
      per piece of work: what was done, files made, result, what's next.</div>`}</div>

    <h4>Handovers: 5 fields each</h4>
    <div class="handovers">${d.handoffs.length ? d.handoffs.map(h => `
      <div class="handover">
        <div class="ho-head"><span class="agent">${esc(h.from_agent)}</span> →
          <span class="agent">${esc(h.to_agent)}</span>
          <span class="ts">${esc(h.ts)}</span></div>
        <dl>
          <dt>Done</dt><dd>${esc(h.done)}</dd>
          <dt>Decisions</dt><dd>${esc(h.decisions)}</dd>
          <dt>Where it stands</dt><dd>${esc(h.state)}</dd>
          <dt>Do first</dt><dd>${esc(h.next_first)}</dd>
          <dt>Warnings</dt><dd>${esc(h.warnings)}</dd>
        </dl>
      </div>`).join("") : `<div class="dimtext">No handovers yet. A handover
      is refused unless it says what was done, the decisions and why, where
      it stands, what to do first, and any warnings.</div>`}</div>

    <h4>Every agent that has owned this card</h4>
    <div class="track">${track.map((s, i) => `
      <div class="hop">
        ${i ? `<div class="arrow">↓</div>` : ""}
        <div class="hop-body">
          <span class="agent">${esc(s.agent || "—")}</span>
          <span class="hop-note">${esc(s.note || "")}</span>
          <span class="ts">${esc(s.ts)}</span>
          ${s.ctx ? `<div class="hop-ctx">${esc(s.ctx)}</div>` : ""}
        </div>
      </div>`).join("")}</div>

    <h4>Comments</h4>
    <div class="comments">${comments.length ? comments.map(c => {
      const det = JSON.parse(c.detail || "{}");
      return `<div class="comment"><b>${esc(c.actor)}</b>
        <span class="ts">${esc(c.ts)}</span>
        <div>${esc(det.text || "")}</div></div>`;
    }).join("") : `<div class="dimtext">No comments yet.</div>`}</div>
    <div class="comment-box">
      <textarea id="d-comment" rows="2" placeholder="Write a comment…"></textarea>
      <button id="d-comment-btn">Comment</button>
    </div>

    <h4>Timeline</h4>
    <div class="timeline">${events.map(ev =>
      `<div class="tl"><span class="ts">${esc(ev.ts)}</span>
       <span>${evText(ev)}</span></div>`).join("")}</div>`;

  /* wiring: the top fields are saved by "Save changes", and also before
     any tag, checklist or comment change redraws the window, so nothing
     typed is lost. Escape or a click outside asks first. */
  const FIELDS = { title: "d-title", assignee_agent: "d-agent",
    context_ref: "d-ctx", crm_person: "d-crm", notes: "d-notes",
    due: "d-due", priority: "d-prio", project_id: "d-proj" };
  const val = id => {
    const el = document.getElementById(id);
    return el.tagName === "TEXTAREA" ? el.value : el.value.trim();
  };
  const orig = {};
  for (const [k, id] of Object.entries(FIELDS)) orig[k] = val(id);
  orig.lane = t.status;
  dirty = null;
  const changed = () => {
    const out = {};
    for (const [k, id] of Object.entries(FIELDS))
      if (val(id) !== orig[k]) out[k] = val(id);
    return out;
  };
  const markDirty = () => {
    const c = changed();
    const laneChanged = document.getElementById("d-lane").value !== orig.lane;
    dirty = (Object.keys(c).length || laneChanged) ? c : null;
    document.getElementById("d-saved").textContent = dirty ? "unsaved changes" : "";
  };
  [...Object.values(FIELDS), "d-lane"].forEach(id => {
    const el = document.getElementById(id);
    el.addEventListener("input", markDirty);
    el.addEventListener("change", markDirty);
  });
  const saveTop = async () => {
    const c = changed();
    if ("title" in c && !c.title) {
      toast("A card needs a title - the old title is kept.");
      document.getElementById("d-title").value = orig.title;
      delete c.title;
    }
    let ok = true;
    if (Object.keys(c).length) {
      const r = await api("/api/task/update", { task_id: t.id, actor: HUMAN(), ...c });
      ok = r._ok;
    }
    const lane = document.getElementById("d-lane").value;
    if (ok && lane !== t.status) {
      const r = await api("/api/task/move", { task_id: t.id, status: lane, actor: HUMAN() });
      ok = r._ok;
    }
    if (ok) dirty = null;
    return ok;
  };
  document.getElementById("d-save").onclick = async () => {
    if (!(await saveTop())) return;
    document.getElementById("d-saved").textContent = "saved ✓";
    setTimeout(() => openDetail(t.id), 350);
    load();
  };
  document.getElementById("d-projlink").onclick = e => {
    e.preventDefault();
    if (!proj.id) return;
    closeModal();
    if (!openTask) openProject(proj.id);
  };
  document.getElementById("d-archive").onclick = async () => {
    await api("/api/task/archive", {
      task_id: t.id, archived: !t.archived, actor: HUMAN() });
    closeModal(true);
    load();
  };
  /* checklist wiring */
  const clItems = checklistOf(t);
  const saveChecklist = async items => {
    if (dirty && !(await saveTop())) return;
    await api("/api/task/checklist", { task_id: t.id, items, actor: HUMAN() });
    openDetail(t.id);
    load();
  };
  document.querySelectorAll("#d-checklist input[type=checkbox]").forEach(cb =>
    cb.onchange = () => {
      clItems[+cb.dataset.n].done = cb.checked;
      saveChecklist(clItems);
    });
  document.querySelectorAll("#d-checklist .rm").forEach(b =>
    b.onclick = () => saveChecklist(
      clItems.filter((_, n) => n !== +b.dataset.rm)));
  const newCheck = document.getElementById("d-newcheck");
  newCheck.onkeydown = e => {
    if (e.key === "Enter" && newCheck.value.trim())
      saveChecklist([...clItems, { text: newCheck.value.trim(), done: false }]);
  };
  const setTags = async next => {
    if (dirty && !(await saveTop())) return;
    await api("/api/task/tags", { task_id: t.id, tags: next, actor: HUMAN() });
    openDetail(t.id);
    load();
  };
  document.querySelectorAll("#d-chips .chip button").forEach(b =>
    b.onclick = () => setTags(tags.filter(x => x !== b.dataset.tag)));
  const newtag = document.getElementById("d-newtag");
  newtag.onkeydown = e => {
    if (e.key === "Enter" && newtag.value.trim())
      setTags([...tags, newtag.value.trim()]);
  };
  document.getElementById("d-comment-btn").onclick = async () => {
    const text = document.getElementById("d-comment").value.trim();
    if (!text) return;
    if (dirty && !(await saveTop())) return;
    await api("/api/task/comment", { task_id: t.id, text, actor: HUMAN() });
    openDetail(t.id);
    load();
  };
  showModal();
}

/* ---------- modal plumbing ---------- */
function showModal() {
  document.getElementById("modal-backdrop").classList.remove("hidden");
}
function closeModal(force) {
  if (dirty && force !== true &&
      !confirm("You have changes on this card that are not saved. Discard them?"))
    return;
  dirty = null;
  openTask = null;
  document.getElementById("modal-backdrop").classList.add("hidden");
}
document.getElementById("modal-close").onclick = () => closeModal();
document.getElementById("modal-backdrop").onclick = e => {
  if (e.target.id === "modal-backdrop") closeModal();
};
document.addEventListener("keydown", e => {
  if (e.key === "Escape") closeModal();
});

/* ---------- header controls ---------- */
const searchBox = document.getElementById("search");
searchBox.oninput = () => { searchQ = searchBox.value.trim(); renderView(); };
function setView(v) {
  viewMode = v;
  document.querySelectorAll(".toggle button").forEach(x =>
    x.classList.toggle("active", x.dataset.v === v));
  renderView();
}
document.querySelectorAll(".toggle button").forEach(b =>
  b.onclick = () => setView(b.dataset.v));
document.getElementById("new-project").onclick = openNewProject;
document.getElementById("help").onclick = openRules;
document.getElementById("stat-waiting-btn").onclick = jumpToAwaiting;
document.getElementById("stat-needs-btn").onclick = jumpToAwaiting;

/* ---------- folding the alerts and activity panel away ---------- */
/* One control does the job: a "Hide" button at the top of the panel, and a
   "Show alerts and activity" tab on the edge of the screen while it is
   folded. Folded, the panel is out of the layout altogether, so the board
   columns take the whole width — 270 px back, which is most of what DONE
   needs on a 1366-wide laptop. Both are buttons, so Tab reaches them and
   Enter or Space works them, and the focus moves with the control.

   The choice is kept in the browser AND in a variable here. The board
   redraws itself every 10 seconds, and that redraw must never unfold the
   panel under your hand, so the redraw only ever updates the count on the
   tab. Whether it is folded is read from the browser once, when the page
   opens. A browser that refuses to remember settings still works: the
   variable carries the choice for as long as the board is open.

   While it is folded, anything that arrives is counted and said on the tab
   itself — "Show alerts and activity (2 new)" — so nothing is hidden
   without saying so. The count is the number of alerts and activity rows
   whose id is higher than the newest id at the moment you folded it. */
let activityOpen = null;       /* null until it has been read once */
let activityBaseline = null;   /* newest ids at the moment it was folded */
let newestEventId = 0, newestAlertId = 0;
let newEventCount = 0, newAlertCount = 0;

function updateActivityCount() {
  const span = document.getElementById("activity-new");
  const show = document.getElementById("activity-show");
  if (!span || !show) return;
  const n = activityOpen === false ? newEventCount + newAlertCount : 0;
  span.textContent = n ? "(" + n + " new)" : "";
  show.title = n
    ? "Show the alerts and activity panel \u2014 " + n + " new since you hid it"
    : "Show the alerts and activity panel";
}

/* Called by the alerts and the activity renders on every refresh. It counts.
   It never folds and never unfolds. */
function noteRows(kind, rows) {
  let max = 0;
  for (const r of rows) { const n = Number(r.id); if (n > max) max = n; }
  let fresh = 0;
  if (activityBaseline) {
    const base = kind === "ev" ? activityBaseline.ev : activityBaseline.al;
    for (const r of rows) if (Number(r.id) > base) fresh++;
  }
  if (kind === "ev") { newestEventId = Math.max(newestEventId, max); newEventCount = fresh; }
  else { newestAlertId = Math.max(newestAlertId, max); newAlertCount = fresh; }
  updateActivityCount();
}

function setActivityFold(open, keepBaseline) {
  activityOpen = open;
  pfWrite("pf-activity-open", open ? "1" : "0");
  const aside = document.getElementById("activity");
  const fold = document.getElementById("activity-fold");
  const show = document.getElementById("activity-show");
  if (!aside || !fold || !show) return;
  aside.classList.toggle("folded", !open);
  show.classList.toggle("hidden", open);
  fold.setAttribute("aria-expanded", open ? "true" : "false");
  show.setAttribute("aria-expanded", open ? "true" : "false");
  if (!keepBaseline) {
    newEventCount = 0; newAlertCount = 0;
    if (open) { activityBaseline = null; pfWrite("pf-activity-seen", ""); }
    else {
      activityBaseline = { ev: newestEventId, al: newestAlertId };
      pfWrite("pf-activity-seen", JSON.stringify(activityBaseline));
    }
  }
  if (typeof renderColJump === "function") renderColJump();
  updateActivityCount();
}

(function initActivityFold() {
  const fold = document.getElementById("activity-fold");
  const show = document.getElementById("activity-show");
  if (!fold || !show) return;
  fold.onclick = () => { setActivityFold(false); show.focus(); };
  show.onclick = () => { setActivityFold(true); fold.focus(); };
  const open = pfRead("pf-activity-open") !== "0";   /* shown unless you hid it */
  if (!open) {
    const seen = pfRead("pf-activity-seen");
    if (seen) { try { activityBaseline = JSON.parse(seen); } catch (e) { activityBaseline = null; } }
  }
  setActivityFold(open, true);
})();
document.getElementById("stat-refused-btn").onclick = () => {
  showRefused = true; renderEvents();
  document.getElementById("activity").scrollIntoView({ behavior: "smooth" });
};
document.getElementById("refused-filter").onclick = () => {
  showRefused = !showRefused; renderEvents();
};

/* ---------- theme switcher (persisted) ---------- */
const THEMES = ["command-deck", "synthwave", "terminal", "paper", "minimal"];
function applyTheme(t) {
  if (!THEMES.includes(t)) t = "command-deck";
  document.documentElement.dataset.theme = t;
  try { localStorage.setItem("pf-theme", t); } catch (e) { /* ignore */ }
  const sel = document.getElementById("theme");
  if (sel) sel.value = t;
}
(function initTheme() {
  let saved = "command-deck";
  try { saved = localStorage.getItem("pf-theme") || saved; } catch (e) { /* ignore */ }
  applyTheme(saved);
  const sel = document.getElementById("theme");
  if (sel) sel.onchange = () => applyTheme(sel.value);
})();

load();
/* Refresh every 10 seconds. It pauses only while you are typing, and says
   so in the line under the header when it does. */
setInterval(() => { if (typing()) showRefreshState(true); else load(); }, 10000);
window.addEventListener("resize", () => {
  clearTimeout(window._pfRs);
  window._pfRs = setTimeout(() => { renderColJump(); syncStickyTops(); }, 200);
});
syncStickyTops();
