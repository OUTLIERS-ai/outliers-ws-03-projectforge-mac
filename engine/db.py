"""ProjectForge store: one SQLite file, and the rules for writing to it.

The board holds no AI. It stores work and enforces who may change what:
  * the single-writer rule (see rules.py) is checked on every write
  * a handover missing any of its 5 fields is refused
  * every write names who made it; there is no silent default author
"""
import json
import re
import secrets
import sqlite3
import threading
import time
from pathlib import Path

from .config import PY
from .rules import (NotAllowed, Roles, check_handover,
                    handover_refusal_for_the_board, handover_summary)

STATUSES = [
    "backlog",
    "ready",
    "in_progress",
    "blocked",
    "review",
    "awaiting_you",
    "done",
    # outside the work lifecycle: a fact mirrored from another program that
    # no agent works on. Never dispatched, never counted against limits.
    "tracking",
]

PASS_RESULTS = ["completed", "progressed", "blocked", "failed", "needs-review"]

# the intent of a message, so later rules can act on it
PERFORMATIVES = ["DELEGATE", "REQUEST", "INFORM", "PROPOSE", "ACCEPT",
                 "REFUSE", "FAILURE", "QUERY", "ESCALATE"]
RESULT_INTENT = {"completed": "INFORM", "progressed": "INFORM",
                 "needs-review": "PROPOSE", "blocked": "REFUSE",
                 "failed": "FAILURE"}

PRIORITIES = ["low", "normal", "high", "urgent"]
PRIORITY_RANK = {"urgent": 0, "high": 1, "normal": 2, "low": 3}
PROJECT_STATUSES = ["active", "paused", "done", "archived"]

# a work report's result decides the card's next column. Only the
# orchestrator applies it. awaiting_you is your hold and is never overridden.
RESULT_TRANSITION = {
    "completed": "done",
    "needs-review": "review",
    "blocked": "blocked",
    "failed": "blocked",
    # nobody is working on it once the agent has replied, so it goes back
    # to Ready and the next /forge-run carries it on
    "progressed": "ready",
}

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    department TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    summary TEXT DEFAULT '',
    source_app TEXT DEFAULT 'manual',
    visibility TEXT DEFAULT 'normal',
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    title TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'backlog',
    assignee_agent TEXT DEFAULT '',
    blocked_by TEXT DEFAULT '[]',
    context_ref TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    created TEXT NOT NULL,
    updated TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS handoffs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    from_agent TEXT NOT NULL,
    to_agent TEXT NOT NULL,
    summary TEXT NOT NULL,
    context TEXT DEFAULT '',
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS passes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id TEXT NOT NULL REFERENCES tasks(id),
    agent TEXT NOT NULL,
    summary TEXT NOT NULL,
    outputs TEXT DEFAULT '[]',
    result TEXT NOT NULL DEFAULT 'progressed',
    next_step TEXT DEFAULT '',
    ts TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL,
    task_id TEXT DEFAULT '',
    message TEXT NOT NULL,
    level TEXT NOT NULL DEFAULT 'warn',
    created TEXT NOT NULL,
    resolved TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    actor TEXT NOT NULL,
    entity_type TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT DEFAULT '{}'
);
"""

MIGRATIONS = (
    "ALTER TABLE tasks ADD COLUMN tags TEXT DEFAULT '[]'",
    "ALTER TABLE tasks ADD COLUMN due TEXT DEFAULT ''",
    "ALTER TABLE tasks ADD COLUMN priority TEXT DEFAULT 'normal'",
    "ALTER TABLE tasks ADD COLUMN checklist TEXT DEFAULT '[]'",
    "ALTER TABLE tasks ADD COLUMN archived INTEGER DEFAULT 0",
    # federation: another program's own id for a card, so re-sending the
    # same card updates it instead of making a copy
    "ALTER TABLE projects ADD COLUMN external_ref TEXT DEFAULT ''",
    "ALTER TABLE tasks ADD COLUMN external_ref TEXT DEFAULT ''",
    "ALTER TABLE tasks ADD COLUMN source_app TEXT DEFAULT 'manual'",
    "ALTER TABLE passes ADD COLUMN dedup_key TEXT DEFAULT ''",
    "ALTER TABLE tasks ADD COLUMN position INTEGER DEFAULT 1000000",
    # set when the orchestrator claims a card, so a re-sync from another
    # program cannot drag it back to an older column
    "ALTER TABLE tasks ADD COLUMN orch_claimed INTEGER DEFAULT 0",
    "ALTER TABLE passes ADD COLUMN intent TEXT DEFAULT 'INFORM'",
    "ALTER TABLE handoffs ADD COLUMN intent TEXT DEFAULT 'DELEGATE'",
    # a re-sync that changes nothing writes `synced`, never `updated`, so
    # "no activity for 5 days" stays true
    "ALTER TABLE projects ADD COLUMN synced TEXT DEFAULT ''",
    "ALTER TABLE tasks ADD COLUMN synced TEXT DEFAULT ''",
    # link to a person note in your CRM vault (path inside that vault)
    "ALTER TABLE tasks ADD COLUMN crm_person TEXT DEFAULT ''",
    # the 5-field handover standard
    "ALTER TABLE handoffs ADD COLUMN done TEXT DEFAULT ''",
    "ALTER TABLE handoffs ADD COLUMN decisions TEXT DEFAULT ''",
    "ALTER TABLE handoffs ADD COLUMN state TEXT DEFAULT ''",
    "ALTER TABLE handoffs ADD COLUMN next_first TEXT DEFAULT ''",
    "ALTER TABLE handoffs ADD COLUMN warnings TEXT DEFAULT ''",
    # set when an agent asks for a person, so the card can carry a mark you
    # can see instead of one line in a list that scrolls away
    "ALTER TABLE tasks ADD COLUMN escalated INTEGER DEFAULT 0",
)

DUE_SHAPE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def check_due(value):
    """A due date is YYYY-MM-DD, or empty for no due date.

    Refused here, where it is typed. A due date in words used to be saved
    happily and then stopped the board's health check every time it ran, so
    alerts, the 45-minute return of stuck cards and auto-archive all stopped
    with nothing on screen to say why.
    """
    v = (value or "").strip()
    if not v:
        return ""
    if not DUE_SHAPE.match(v):
        raise ValueError(
            f"a due date must be written as YYYY-MM-DD, for example "
            f"2026-10-02. '{v}' is not, so nothing was changed.")
    try:
        time.strptime(v, "%Y-%m-%d")
    except ValueError:
        raise ValueError(
            f"there is no such date as '{v}'. Write the due date as "
            f"YYYY-MM-DD, for example 2026-10-02. Nothing was changed.")
    return v


def now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def new_id(prefix: str) -> str:
    return f"{prefix}-{secrets.token_hex(3)}"


def _locked(method):
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return method(self, *args, **kwargs)
    wrapper.__name__ = method.__name__
    wrapper.__doc__ = method.__doc__
    return wrapper


class Store:
    def __init__(self, db_path, roles=None, departments=None):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.roles = roles or Roles()
        # the department ids from config; empty = any department accepted
        self.departments = [d for d in (departments or []) if d]
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False,
                                    timeout=15)
        self.conn.row_factory = sqlite3.Row
        self._lock = threading.RLock()
        # WAL lets other programs read while the board writes
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self.conn.commit()
        for ddl in MIGRATIONS:
            try:
                self.conn.execute(ddl)
                self.conn.commit()
            except sqlite3.OperationalError:
                pass  # column already there

    def close(self):
        try:
            self.conn.close()
        except sqlite3.Error:
            pass

    # -- helpers ----------------------------------------------------------
    def _task_row(self, task_id):
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE id=?", (task_id,)).fetchone()
        if row is None:
            raise KeyError(task_id)
        return row

    def _need(self, ok, actor, what, entity_id=""):
        try:
            self.roles.require(ok, actor, what)
        except NotAllowed as e:
            self._refused(actor, entity_id, str(e))
            raise

    def _refused(self, actor, entity_id, message):
        """Write a refusal to the activity list, so you can see which agent
        tried to break the rules, on which card, and how often."""
        try:
            self.conn.rollback()  # drop any half-made change first
            self.log((actor or "").strip() or "(no name)", "task",
                     entity_id or "-", "refused", {"message": message})
        except sqlite3.Error:
            pass  # never let the record of a refusal hide the refusal

    @_locked
    def refusals_today(self):
        return self.conn.execute(
            "SELECT COUNT(*) FROM events WHERE action='refused' AND ts>=?",
            (time.strftime("%Y-%m-%d") + " 00:00:00",)).fetchone()[0]

    def _check_department(self, department):
        if self.departments and department not in self.departments:
            raise ValueError(
                f"unknown department '{department}'. Use one of: "
                f"{', '.join(self.departments)} (they are listed in "
                f"config.json under departments)")

    @staticmethod
    def _check_title(title):
        if not (title or "").strip():
            raise ValueError("a card needs a title")

    # -- events -----------------------------------------------------------
    @_locked
    def log(self, actor, entity_type, entity_id, action, detail=None):
        self.conn.execute(
            "INSERT INTO events (ts, actor, entity_type, entity_id, action,"
            " detail) VALUES (?,?,?,?,?,?)",
            (now(), actor, entity_type, entity_id, action,
             json.dumps(detail or {})))
        self.conn.commit()

    # -- projects ---------------------------------------------------------
    @_locked
    def add_project(self, title, department, summary="", source_app="manual",
                    visibility="normal", actor=None, status="active",
                    external_ref=""):
        self._need(self.roles.can_open(actor), actor, "open a project")
        if not (title or "").strip():
            raise ValueError("a project needs a title")
        self._check_department(department)
        pid = new_id("pf-p")
        ts = now()
        self.conn.execute(
            "INSERT INTO projects (id, title, department, status, summary,"
            " source_app, visibility, created, updated, external_ref)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (pid, title, department, status, summary, source_app,
             visibility, ts, ts, external_ref))
        self.conn.commit()
        self.log(actor, "project", pid, "created", {"title": title})
        return pid

    @_locked
    def update_project(self, project_id, fields, actor=None):
        self._need(self.roles.can_edit(actor), actor, "edit a project",
                   project_id)
        allowed = ["title", "summary", "department", "status"]
        sets = {k: fields[k] for k in allowed if k in fields}
        if not sets:
            return
        if sets.get("status") and sets["status"] not in PROJECT_STATUSES:
            raise ValueError(f"unknown project status: {sets['status']}")
        if "title" in sets and not (sets["title"] or "").strip():
            raise ValueError("a project needs a title")
        if "department" in sets:
            self._check_department(sets["department"])
        if self.conn.execute("SELECT id FROM projects WHERE id=?",
                             (project_id,)).fetchone() is None:
            raise KeyError(project_id)
        cols = ", ".join(f"{k}=?" for k in sets)
        self.conn.execute(f"UPDATE projects SET {cols}, updated=? WHERE id=?",
                          (*sets.values(), now(), project_id))
        self.conn.commit()
        self.log(actor, "project", project_id, "updated",
                 {"fields": list(sets)})

    @_locked
    def find_or_add_project(self, title, department, actor=None, summary=""):
        row = self.conn.execute(
            "SELECT id FROM projects WHERE lower(title)=lower(?) AND "
            "department=? AND status!='archived'",
            (title, department)).fetchone()
        if row:
            return row["id"]
        return self.add_project(title, department, summary=summary,
                                actor=actor)

    # -- tasks (cards) ----------------------------------------------------
    @_locked
    def add_task(self, project_id, title, status="backlog", assignee_agent="",
                 context_ref="", notes="", actor=None, crm_person=""):
        """Open a card. Only you or a manager may do this."""
        self._need(self.roles.can_open(actor), actor, "open a card")
        self._check_title(title)
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status}")
        if self.conn.execute("SELECT id FROM projects WHERE id=?",
                             (project_id,)).fetchone() is None:
            raise ValueError(
                f"no project with the id '{project_id}'. See the ids with: "
                f"{PY} forge.py projects")
        tid = new_id("pf-t")
        ts = now()
        self.conn.execute(
            "INSERT INTO tasks (id, project_id, title, status, assignee_agent,"
            " context_ref, notes, created, updated, crm_person)"
            " VALUES (?,?,?,?,?,?,?,?,?,?)",
            (tid, project_id, title, status, assignee_agent, context_ref,
             notes, ts, ts, crm_person))
        self.conn.commit()
        self.log(actor, "task", tid, "created",
                 {"title": title, "status": status})
        return tid

    @_locked
    def open_card(self, actor, project_title, department, title,
                  assignee_agent="", status="backlog", context_ref="",
                  notes="", crm_person=""):
        """A manager's one-call way to open a card under a project (the
        project is found by title, or created)."""
        self._need(self.roles.can_open(actor), actor, "open a card")
        if status not in ("backlog", "ready"):
            raise ValueError("a new card starts in backlog or ready")
        pid = self.find_or_add_project(project_title, department, actor=actor)
        return self.add_task(pid, title, status=status,
                             assignee_agent=assignee_agent,
                             context_ref=context_ref, notes=notes,
                             actor=actor, crm_person=crm_person)

    @_locked
    def move_task(self, task_id, status, actor=None):
        """Move a card to another column. Only the orchestrator (or you)."""
        self._need(self.roles.can_move(actor), actor,
                   "move a card between columns", task_id)
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status}")
        row = self._task_row(task_id)
        self._guard_awaiting_you(row, status, actor)
        # moving the card on is how you answer an agent that asked for a
        # person, so the NEEDS YOU mark comes off here
        keep = 1 if status == "awaiting_you" else 0
        self.conn.execute(
            "UPDATE tasks SET status=?, updated=?, escalated=escalated*?"
            " WHERE id=?", (status, now(), keep, task_id))
        self.conn.commit()
        self.log(actor, "task", task_id, "moved",
                 {"from": row["status"], "to": status})

    def _guard_awaiting_you(self, row, status, actor):
        """Awaiting You is yours: nobody but you takes a card out of it."""
        if row["status"] == "awaiting_you" and status != "awaiting_you" \
                and self.roles.kind(actor) != "you":
            self._need(False, actor,
                       "take a card out of Awaiting You", row["id"])

    @_locked
    def reorder(self, status, ids, actor=None):
        """Set the order of a column (and move any card dragged into it)."""
        if status not in STATUSES:
            raise ValueError(f"unknown status: {status}")
        for i, tid in enumerate(ids):
            row = self.conn.execute("SELECT id, status FROM tasks WHERE id=?",
                                    (tid,)).fetchone()
            if row is None:
                continue
            if row["status"] != status:
                self._need(self.roles.can_move(actor), actor,
                           "move a card between columns", tid)
                self._guard_awaiting_you(row, status, actor)
            else:
                self._need(self.roles.can_edit(actor), actor,
                           "reorder a column", tid)
            self.conn.execute(
                "UPDATE tasks SET status=?, position=?, updated=? WHERE id=?",
                (status, i + 1, now(), tid))
            if row["status"] != status:
                self.log(actor, "task", tid, "moved",
                         {"from": row["status"], "to": status})
        self.conn.commit()

    @_locked
    def set_tags(self, task_id, tags, actor=None):
        self._need(self.roles.can_edit(actor), actor, "tag a card", task_id)
        clean = sorted({t.strip().lower().replace(" ", "-")
                        for t in tags if t and t.strip()})
        self._task_row(task_id)
        self.conn.execute("UPDATE tasks SET tags=?, updated=? WHERE id=?",
                          (json.dumps(clean), now(), task_id))
        self.conn.commit()
        self.log(actor, "task", task_id, "tagged", {"tags": clean})
        return clean

    @_locked
    def update_task(self, task_id, fields, actor=None):
        self._need(self.roles.can_edit(actor), actor, "edit a card", task_id)
        allowed = ["title", "notes", "assignee_agent", "context_ref",
                   "due", "priority", "project_id", "crm_person"]
        sets = {k: fields[k] for k in allowed if k in fields}
        if not sets:
            return
        if sets.get("priority") and sets["priority"] not in PRIORITIES:
            raise ValueError(f"unknown priority: {sets['priority']}")
        if "due" in sets:
            sets["due"] = check_due(sets["due"])
        if "title" in sets:
            self._check_title(sets["title"])
            sets["title"] = sets["title"].strip()
        row = self._task_row(task_id)
        # only log the fields that really changed
        sets = {k: v for k, v in sets.items() if row[k] != v}
        if not sets:
            return
        if sets.get("project_id") and self.conn.execute(
                "SELECT id FROM projects WHERE id=?",
                (sets["project_id"],)).fetchone() is None:
            raise KeyError(sets["project_id"])
        self._task_row(task_id)
        cols = ", ".join(f"{k}=?" for k in sets)
        self.conn.execute(f"UPDATE tasks SET {cols}, updated=? WHERE id=?",
                          (*sets.values(), now(), task_id))
        self.conn.commit()
        self.log(actor, "task", task_id, "updated", {"fields": list(sets)})

    @_locked
    def set_archived(self, task_id, archived=True, actor=None):
        self._need(self.roles.can_edit(actor) or
                   self.roles.kind(actor) == "system", actor,
                   "archive a card", task_id)
        self._task_row(task_id)
        self.conn.execute("UPDATE tasks SET archived=?, updated=? WHERE id=?",
                          (1 if archived else 0, now(), task_id))
        self.conn.commit()
        self.log(actor, "task", task_id,
                 "archived" if archived else "restored", {})

    @_locked
    def set_checklist(self, task_id, items, actor=None):
        self._need(self.roles.can_edit(actor), actor, "edit a checklist", task_id)
        self._task_row(task_id)
        clean = [{"text": str(i.get("text", "")).strip(),
                  "done": bool(i.get("done"))}
                 for i in items if str(i.get("text", "")).strip()]
        self.conn.execute("UPDATE tasks SET checklist=?, updated=? WHERE id=?",
                          (json.dumps(clean), now(), task_id))
        self.conn.commit()
        self.log(actor, "task", task_id, "checklist",
                 {"done": sum(1 for i in clean if i["done"]),
                  "total": len(clean)})
        return clean

    @_locked
    def set_blockers(self, task_id, blocker_ids, actor=None):
        """Say which cards must finish first. Refuses a loop (A waits on B
        waits on A)."""
        self._need(self.roles.can_edit(actor), actor, "set blockers", task_id)
        self._task_row(task_id)
        ids = [b for b in blocker_ids if b]
        graph = {}
        for r in self.conn.execute("SELECT id, blocked_by FROM tasks"):
            try:
                graph[r["id"]] = list(json.loads(r["blocked_by"] or "[]"))
            except (ValueError, TypeError):
                graph[r["id"]] = []
        graph[task_id] = ids
        seen, stack = set(), list(ids)
        while stack:
            n = stack.pop()
            if n == task_id:
                raise ValueError("dependency cycle detected")
            if n in seen:
                continue
            seen.add(n)
            stack.extend(graph.get(n, []))
        self.conn.execute("UPDATE tasks SET blocked_by=?, updated=? WHERE id=?",
                          (json.dumps(ids), now(), task_id))
        self.conn.commit()
        self.log(actor, "task", task_id, "blockers", {"blocked_by": ids})
        return ids

    # -- appends: anyone who names themselves -----------------------------
    @_locked
    def add_pass(self, task_id, agent, summary, outputs=None,
                 result="progressed", next_step="", dedup_key="", intent=None):
        """One work report: what was done, what it produced, the result, and
        what is needed next. Records only; never moves the card."""
        if not (agent or "").strip():
            raise NotAllowed("refused: a work report needs the agent's name")
        if not (summary or "").strip():
            raise ValueError("a work report needs a summary")
        if result not in PASS_RESULTS:
            raise ValueError(f"unknown result: {result}")
        if intent is None:
            intent = RESULT_INTENT.get(result, "INFORM")
        if intent not in PERFORMATIVES:
            raise ValueError(f"unknown intent: {intent}")
        self._task_row(task_id)
        if dedup_key and self.conn.execute(
                "SELECT id FROM passes WHERE task_id=? AND dedup_key=?",
                (task_id, dedup_key)).fetchone():
            return False
        self.conn.execute(
            "INSERT INTO passes (task_id, agent, summary, outputs, result,"
            " next_step, ts, dedup_key, intent) VALUES (?,?,?,?,?,?,?,?,?)",
            (task_id, agent, summary, json.dumps(outputs or []), result,
             next_step, now(), dedup_key, intent))
        self.conn.execute("UPDATE tasks SET updated=? WHERE id=?",
                          (now(), task_id))
        self.conn.commit()
        self.log(agent, "task", task_id, "pass",
                 {"result": result, "intent": intent, "summary": summary})
        return True

    @_locked
    def comment(self, task_id, text, actor=None):
        if not (actor or "").strip():
            raise NotAllowed("refused: a comment needs the writer's name")
        if not (text or "").strip():
            raise ValueError("empty comment")
        self._task_row(task_id)
        self.conn.execute("UPDATE tasks SET updated=? WHERE id=?",
                          (now(), task_id))
        self.conn.commit()
        self.log(actor, "task", task_id, "comment", {"text": text})

    @_locked
    def escalate(self, task_id, note, actor=None):
        """A worker asks for a person.

        3 marks, because this is the one change that needs YOU and it used
        to leave no sign at all: a red NEEDS YOU chip on the card face, a
        count in the header, and the card joins Awaiting You, your own
        column. The health check also raises an alert for it, so it survives
        the activity list scrolling away.
        """
        if not (actor or "").strip():
            raise NotAllowed("refused: an escalation needs the agent's name")
        if not (note or "").strip():
            raise ValueError("an escalation needs a note saying what you need")
        row = self._task_row(task_id)
        ts = now()
        self.conn.execute("UPDATE tasks SET escalated=1, updated=? WHERE id=?",
                          (ts, task_id))
        self.conn.commit()
        self.log(actor, "task", task_id, "escalation", {"text": note})
        if row["status"] not in ("awaiting_you", "done"):
            self.conn.execute(
                "UPDATE tasks SET status='awaiting_you', updated=?"
                " WHERE id=?", (ts, task_id))
            self.conn.commit()
            self.log(actor, "task", task_id, "moved",
                     {"from": row["status"], "to": "awaiting_you",
                      "reason": "asked for a person"})

    @_locked
    def open_escalations(self):
        """Cards where an agent has asked for a person and you have not yet
        moved the card on."""
        return [dict(r) for r in self.conn.execute(
            "SELECT id, title, assignee_agent FROM tasks"
            " WHERE escalated=1 AND archived=0")]

    @_locked
    def handoff(self, task_id, from_agent, to_agent, done="", decisions="",
                state="", next_first="", warnings="", context="",
                intent="DELEGATE", actor=None):
        """Hand a card to the next agent. Refused unless all 5 fields of the
        handover standard are filled in: done, decisions (with why), state,
        next_first, warnings."""
        if not (from_agent or "").strip() or not (to_agent or "").strip():
            raise NotAllowed("refused: a handover needs --from and --to")
        if intent not in PERFORMATIVES:
            raise ValueError(f"unknown intent: {intent}")
        fields = {"done": done, "decisions": decisions, "state": state,
                  "next_first": next_first, "warnings": warnings}
        problems = check_handover(fields)
        if problems:
            # 2 copies of the same refusal: the board gets the plain one,
            # the agent's terminal gets the one with the options to fill in
            self._refused(from_agent, task_id,
                          handover_refusal_for_the_board(from_agent, fields))
            raise ValueError("handover refused - a handover must carry all 5 "
                             "fields: " + "; ".join(problems))
        self._task_row(task_id)
        summary = handover_summary(fields)
        self.conn.execute(
            "INSERT INTO handoffs (task_id, from_agent, to_agent, summary,"
            " context, ts, intent, done, decisions, state, next_first,"
            " warnings) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (task_id, from_agent, to_agent, summary, context, now(), intent,
             done.strip(), decisions.strip(), state.strip(),
             next_first.strip(), warnings.strip()))
        self.conn.execute(
            "UPDATE tasks SET assignee_agent=?, updated=? WHERE id=?",
            (to_agent, now(), task_id))
        self.conn.commit()
        self.log(actor or from_agent, "task", task_id, "handoff",
                 {"from": from_agent, "to": to_agent, "intent": intent,
                  "summary": summary})

    # -- federation: other programs push cards in ------------------------
    # A source program (for example the CRM "Today" reader) owns its own
    # cards. It sends them keyed by its own ids; re-sending updates, never
    # copies. It counts as the manager of its own cards.
    @_locked
    def upsert_project(self, source_app, external_ref, title, department,
                       summary=None, status=None, visibility=None, actor=None):
        if not source_app or not external_ref:
            raise ValueError("source_app and external_ref are required")
        actor = actor or source_app
        row = self.conn.execute(
            "SELECT * FROM projects WHERE source_app=? AND external_ref=?",
            (source_app, external_ref)).fetchone()
        ts = now()
        if row is None:
            pid = new_id("pf-p")
            self.conn.execute(
                "INSERT INTO projects (id, title, department, status, summary,"
                " source_app, visibility, created, updated, external_ref,"
                " synced) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (pid, title, department, status or "active", summary or "",
                 source_app, visibility or "normal", ts, ts, external_ref, ts))
            self.conn.commit()
            self.log(actor, "project", pid, "federated",
                     {"source_app": source_app, "ref": external_ref,
                      "title": title})
            return {"id": pid, "created": True}
        pid = row["id"]
        sets = {"title": title, "department": department}
        for k, v in (("summary", summary), ("status", status),
                     ("visibility", visibility)):
            if v is not None:
                sets[k] = v
        changed = {k: v for k, v in sets.items() if row[k] != v}
        if changed:
            cols = ", ".join(f"{k}=?" for k in changed)
            self.conn.execute(
                f"UPDATE projects SET {cols}, updated=?, synced=? WHERE id=?",
                (*changed.values(), ts, ts, pid))
            self.conn.commit()
            self.log(actor, "project", pid, "federated",
                     {"source_app": source_app, "ref": external_ref,
                      "fields": list(changed)})
        else:
            self.conn.execute("UPDATE projects SET synced=? WHERE id=?",
                              (ts, pid))
            self.conn.commit()
        return {"id": pid, "created": False}

    @_locked
    def upsert_task(self, source_app, external_ref, project_id, title,
                    status=None, assignee_agent=None, context_ref=None,
                    notes=None, priority=None, due=None, crm_person=None,
                    actor=None):
        if not source_app or not external_ref:
            raise ValueError("source_app and external_ref are required")
        if status is not None and status not in STATUSES:
            raise ValueError(f"unknown status: {status}")
        if priority is not None and priority not in PRIORITIES:
            raise ValueError(f"unknown priority: {priority}")
        if due is not None:
            due = check_due(due)
        if self.conn.execute("SELECT id FROM projects WHERE id=?",
                             (project_id,)).fetchone() is None:
            raise KeyError(project_id)
        actor = actor or source_app
        row = self.conn.execute(
            "SELECT * FROM tasks WHERE source_app=? AND external_ref=?",
            (source_app, external_ref)).fetchone()
        ts = now()
        if row is None:
            tid = new_id("pf-t")
            self.conn.execute(
                "INSERT INTO tasks (id, project_id, title, status,"
                " assignee_agent, context_ref, notes, created, updated,"
                " priority, due, external_ref, source_app, synced, crm_person)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tid, project_id, title, status or "backlog",
                 assignee_agent or "", context_ref or "", notes or "",
                 ts, ts, priority or "normal", due or "", external_ref,
                 source_app, ts, crm_person or ""))
            self.conn.commit()
            self.log(actor, "task", tid, "federated",
                     {"source_app": source_app, "ref": external_ref,
                      "title": title, "status": status or "backlog"})
            return {"id": tid, "created": True}
        tid = row["id"]
        sets = {"title": title, "project_id": project_id}
        status_held = False
        for k, v in (("status", status), ("assignee_agent", assignee_agent),
                     ("context_ref", context_ref), ("notes", notes),
                     ("priority", priority), ("due", due),
                     ("crm_person", crm_person)):
            if v is None:
                continue
            if k == "status" and v != row["status"] and (
                    row["orch_claimed"] or row["status"] == "awaiting_you"):
                # the orchestrator owns it now, or it is waiting for you:
                # a program re-sending its old column cannot move it
                status_held = True
                continue
            sets[k] = v
        changed = {k: v for k, v in sets.items() if row[k] != v}
        if not changed:
            self.conn.execute("UPDATE tasks SET synced=? WHERE id=?",
                              (ts, tid))
            self.conn.commit()
            return {"id": tid, "created": False}
        cols = ", ".join(f"{k}=?" for k in changed)
        self.conn.execute(
            f"UPDATE tasks SET {cols}, updated=?, synced=? WHERE id=?",
            (*changed.values(), ts, ts, tid))
        self.conn.commit()
        moved = "status" in changed
        detail = {"source_app": source_app, "ref": external_ref}
        if moved:
            detail.update({"from": row["status"], "to": changed["status"]})
        else:
            detail["fields"] = list(changed)
        if status_held:
            detail["status_held"] = {"orchestrator_owns": row["status"],
                                     "source_proposed": status}
        self.log(actor, "task", tid, "moved" if moved else "federated", detail)
        return {"id": tid, "created": False}

    def federate(self, payload, actor=None):
        """Take a batch from one source program: a project and its cards,
        each keyed by the source's own id. Safe to call again and again.
        Only you, or a program named in config "federate_sources", may."""
        with self._lock:
            self._need(self.roles.can_federate(actor), actor,
                       "push cards onto the board (add the program's name "
                       "to federate_sources in config.json)")
        src = payload.get("source_app")
        if not src:
            raise ValueError("source_app is required")
        proj = payload.get("project") or {}
        if not proj.get("ref") or not proj.get("title") \
                or not proj.get("department"):
            raise ValueError("project needs ref, title, department")
        pr = self.upsert_project(
            src, proj["ref"], proj["title"], proj["department"],
            summary=proj.get("summary"), status=proj.get("status"),
            visibility=proj.get("visibility"))
        out = {"source_app": src, "project": pr, "tasks": [],
               "passes_logged": 0}
        for t in payload.get("tasks", []):
            if not t.get("ref") or not t.get("title"):
                raise ValueError("each task needs ref and title")
            tr = self.upsert_task(
                src, t["ref"], pr["id"], t["title"], status=t.get("status"),
                assignee_agent=t.get("assignee_agent"),
                context_ref=t.get("context_ref"), notes=t.get("notes"),
                priority=t.get("priority"), due=t.get("due"),
                crm_person=t.get("crm_person"))
            tr["ref"] = t["ref"]
            for p in t.get("passes", []):
                if not p.get("agent") or not p.get("summary"):
                    raise ValueError("each pass needs agent and summary")
                if self.add_pass(tr["id"], p["agent"], p["summary"],
                                 outputs=p.get("outputs"),
                                 result=p.get("result", "progressed"),
                                 next_step=p.get("next_step", ""),
                                 dedup_key=p.get("key", "")):
                    out["passes_logged"] += 1
            out["tasks"].append(tr)
        return out

    # -- the orchestrator loop -------------------------------------------
    @staticmethod
    def _dispatch_block(card, dept, agent, acts):
        """Optional per-agent limits from cards/<agent>.json: which
        departments it may work in and how many times a day. An agent with
        no file is always allowed."""
        if not card:
            return ""
        sc = card.get("scopes") or {}
        depts = sc.get("depts") or []
        if depts and dept and dept not in depts:
            return "out_of_scope"
        cap = card.get("max_activations_per_day")
        if cap and acts.get(agent, 0) >= cap:
            return "cap_exhausted"
        return ""

    @_locked
    def activations_today(self):
        from collections import Counter
        c = Counter()
        for e in self.conn.execute(
                "SELECT detail FROM events WHERE action='dispatched' AND ts>=?",
                (time.strftime("%Y-%m-%d") + " 00:00:00",)):
            try:
                a = json.loads(e["detail"] or "{}").get("agent")
            except (ValueError, TypeError):
                a = None
            if a:
                c[a] += 1
        return dict(c)

    @_locked
    def next_actionable(self, limit=5, wip_limits=None, cards=None):
        """The ranked queue of Ready cards an agent can start now: priority,
        then soonest due date, then oldest. Each item says whether it can be
        handed out, and if not, why (hold_reason)."""
        wip_limits = wip_limits or {}
        acts = self.activations_today() if cards else {}
        rows = [dict(r) for r in self.conn.execute(
            "SELECT * FROM tasks WHERE archived=0")]
        wip_in = sum(1 for r in rows if r["status"] == "in_progress")
        cap = wip_limits.get("in_progress")
        at_cap = cap is not None and wip_in >= cap

        def has_blockers(r):
            try:
                return bool(json.loads(r["blocked_by"] or "[]"))
            except (ValueError, TypeError):
                return False

        cand = [r for r in rows
                if r["status"] == "ready" and not has_blockers(r)]
        cand.sort(key=lambda r: (PRIORITY_RANK.get(r["priority"], 2),
                                 r["due"] or "9999-99-99", r["created"]))
        projects = {p["id"]: dict(p) for p in self.conn.execute(
            "SELECT * FROM projects")}
        out = []
        dispatchable = 0
        for i, r in enumerate(cand):
            proj = projects.get(r["project_id"], {})
            owner = r["assignee_agent"]
            kind = self.roles.kind(owner)
            if not owner:
                hold = "no_owner"
            elif kind == "you":
                hold = "yours"  # a human card: never handed to an agent
            elif not self.roles.known_agent(owner):
                hold = "unknown_agent"  # a typo, or an agent not installed
            elif at_cap:
                hold = "wip_cap"
            else:
                hold = self._dispatch_block((cards or {}).get(owner),
                                            proj.get("department"), owner,
                                            acts)
            if hold == "":
                dispatchable += 1
            if i >= limit:
                continue
            lp = self.conn.execute(
                "SELECT * FROM passes WHERE task_id=? ORDER BY id DESC LIMIT 1",
                (r["id"],)).fetchone()
            ho = self.conn.execute(
                "SELECT * FROM handoffs WHERE task_id=? ORDER BY id DESC"
                " LIMIT 1", (r["id"],)).fetchone()
            out.append({
                "task_id": r["id"], "title": r["title"],
                "project": proj.get("title"), "project_id": r["project_id"],
                "department": proj.get("department"),
                "source_app": r["source_app"], "assignee_agent": owner,
                "priority": r["priority"], "due": r["due"],
                "context_ref": r["context_ref"], "crm_person": r["crm_person"],
                "notes": r["notes"], "dispatchable": hold == "",
                "hold_reason": hold,
                "last_pass": dict(lp) if lp else None,
                "last_handoff": dict(ho) if ho else None,
            })
        return {"wip": {"in_progress": wip_in, "cap": cap, "at_cap": at_cap},
                "ready_count": len(cand), "dispatchable_count": dispatchable,
                "next": out}

    def _intake_plan(self, dept_leads, hold_tags=None, routing=None):
        """Work out what intake WOULD do, without writing anything."""
        hold = set(hold_tags or [])
        routing = routing or []

        def route(title):
            t = (title or "").lower()
            for rule in routing:
                if any(kw.lower() in t for kw in rule.get("match", [])):
                    return rule.get("agent")
            return None

        plan = []
        rows = [dict(r) for r in self.conn.execute(
            "SELECT t.*, p.department AS dept, p.status AS pstatus "
            "FROM tasks t JOIN projects p ON p.id = t.project_id "
            "WHERE t.status IN ('backlog','ready') AND t.archived=0 AND "
            "(t.source_app='manual' OR t.source_app='' OR "
            "t.source_app IS NULL)")]
        for r in rows:
            if r["pstatus"] != "active":
                continue
            try:
                tags = set(json.loads(r["tags"] or "[]"))
            except (ValueError, TypeError):
                tags = set()
            if tags & hold:
                continue
            owner = r["assignee_agent"]
            new_owner = None
            via = None
            if not owner:
                matched = route(r["title"])
                new_owner = matched or dept_leads.get(r["dept"]) or None
                via = "routing" if matched else "dept-lead"
                if not new_owner:
                    continue  # nobody to route to: left for you
                owner = new_owner
            try:
                blocked = bool(json.loads(r["blocked_by"] or "[]"))
            except (ValueError, TypeError):
                blocked = False
            promote = (r["status"] == "backlog" and owner and not blocked
                       and self.roles.kind(owner) != "you")
            plan.append({"id": r["id"], "new_owner": new_owner, "via": via,
                         "promote": promote, "owner": owner})
        return plan

    @_locked
    def run_intake(self, dept_leads, hold_tags=None, routing=None,
                   actor="orchestrator"):
        """Triage: give an ownerless card an owner (a keyword match on its
        title, else its department lead), then move owned Backlog cards to
        Ready. Only the orchestrator runs this."""
        self._need(self.roles.is_orchestrator(actor), actor, "run intake")
        assigned = promoted = 0
        for p in self._intake_plan(dept_leads, hold_tags, routing):
            if p["new_owner"]:
                self.conn.execute(
                    "UPDATE tasks SET assignee_agent=?, updated=? WHERE id=?",
                    (p["new_owner"], now(), p["id"]))
                self.log(actor, "task", p["id"], "auto-assigned",
                         {"agent": p["new_owner"], "via": p["via"]})
                assigned += 1
            if p["promote"]:
                self.conn.execute(
                    "UPDATE tasks SET status='ready', updated=? WHERE id=?",
                    (now(), p["id"]))
                self.log(actor, "task", p["id"], "auto-queued",
                         {"from": "backlog", "via": "intake"})
                promoted += 1
        self.conn.commit()
        return {"assigned": assigned, "promoted": promoted}

    @_locked
    def work_waiting(self, config, cards=None):
        """How many cards an orchestrator pass could actually hand out right
        now, counted WITHOUT writing anything. The schedule reads this and
        does not start Claude when it is 0."""
        wip = config.get("hygiene", {}).get("wip_limits", {})
        q = self.next_actionable(limit=0, wip_limits=wip, cards=cards)
        if q["wip"]["at_cap"]:
            return {"ready": q["dispatchable_count"], "from_backlog": 0,
                    "total": 0, "at_cap": True}
        from_backlog = 0
        ic = config.get("intake", {})
        if ic.get("auto_pickup", True):
            leads = {d["id"]: d.get("lead", "")
                     for d in config.get("departments", [])}
            from_backlog = sum(1 for p in self._intake_plan(
                leads, ic.get("hold_tags", []), ic.get("routing", []))
                if p["promote"])
        total = q["dispatchable_count"] + from_backlog
        return {"ready": q["dispatchable_count"],
                "from_backlog": from_backlog, "total": total, "at_cap": False}

    @_locked
    def dispatch(self, task_id, agent=None, actor="orchestrator", cards=None):
        """Claim a card for its owner agent: Ready -> In Progress. Only the
        orchestrator may do this, so two sessions can't claim the same card."""
        self._need(self.roles.is_orchestrator(actor), actor,
                   "dispatch a card")
        row = self._task_row(task_id)
        if row["status"] != "ready":
            raise ValueError(
                f"only a card in Ready can be taken; this one is in "
                f"{row['status']}. Another /forge-run may already have "
                f"taken it.")
        owner = agent or row["assignee_agent"]
        if not owner:
            raise ValueError("task has no owner agent to dispatch to")
        if self.roles.kind(owner) == "you":
            raise ValueError("this card is yours - the orchestrator does not "
                             "hand your cards to an agent")
        if not self.roles.known_agent(owner):
            raise ValueError(f"'{owner}' is not one of your agents (check the "
                             f"spelling on the card, or re-run install.py "
                             f"after adding the agent)")
        if cards:
            dept = self.conn.execute(
                "SELECT department FROM projects WHERE id=?",
                (row["project_id"],)).fetchone()
            reason = self._dispatch_block(
                cards.get(owner), dept["department"] if dept else None,
                owner, self.activations_today())
            if reason:
                raise ValueError(f"dispatch refused: {reason} ({owner})")
        self.conn.execute(
            "UPDATE tasks SET status='in_progress', assignee_agent=?,"
            " orch_claimed=1, updated=? WHERE id=?", (owner, now(), task_id))
        self.conn.commit()
        self.log(actor, "task", task_id, "dispatched",
                 {"agent": owner, "from": row["status"]})
        return self.task_detail(task_id)

    @_locked
    def reap_stale_dispatches(self, ttl_min=45, actor="hygiene"):
        """A card claimed for an agent that never reported back within
        ttl_min goes back to Ready, with an event saying why."""
        from datetime import datetime, timedelta
        fmt = "%Y-%m-%d %H:%M:%S"
        cutoff = datetime.now() - timedelta(minutes=ttl_min)
        reaped = []
        for r in [dict(x) for x in self.conn.execute(
                "SELECT id FROM tasks WHERE status='in_progress' AND "
                "orch_claimed=1 AND archived=0")]:
            tid = r["id"]
            disp = self.conn.execute(
                "SELECT ts FROM events WHERE entity_id=? AND "
                "action='dispatched' ORDER BY id DESC LIMIT 1",
                (tid,)).fetchone()
            if not disp:
                continue
            try:
                disp_dt = datetime.strptime(disp["ts"][:19], fmt)
            except ValueError:
                continue
            if disp_dt > cutoff:
                continue
            p = self.conn.execute(
                "SELECT ts FROM passes WHERE task_id=? ORDER BY id DESC "
                "LIMIT 1", (tid,)).fetchone()
            if p:
                try:
                    if datetime.strptime(p["ts"][:19], fmt) >= disp_dt:
                        continue
                except ValueError:
                    pass
            self.conn.execute(
                "UPDATE tasks SET status='ready', orch_claimed=0, updated=? "
                "WHERE id=?", (now(), tid))
            self.log(actor, "task", tid, "reaped",
                     {"from": "in_progress", "ttl_min": ttl_min,
                      "reason": "no report within the time limit"})
            reaped.append(tid)
        self.conn.commit()
        return reaped

    def commit_pass(self, task_id, agent, summary, result="progressed",
                    outputs=None, next_step="", dedup_key="",
                    actor="orchestrator", intent=None):
        """The orchestrator records an agent's report AND moves the card to
        the column its result implies. Never overrides awaiting_you."""
        with self._lock:
            self._need(self.roles.is_orchestrator(actor), actor,
                       "commit a result (only the orchestrator moves cards)")
            if result not in PASS_RESULTS:
                raise ValueError(f"unknown result: {result}")
            cur = self._task_row(task_id)
            if cur["status"] not in ("in_progress", "awaiting_you"):
                raise ValueError(
                    f"commit is for a card the orchestrator has taken (In "
                    f"Progress); this one is in {cur['status']}. Take it "
                    f"first with dispatch.")
            logged = self.add_pass(task_id, agent, summary, outputs=outputs,
                                   result=result, next_step=next_step,
                                   dedup_key=dedup_key, intent=intent)
            target = RESULT_TRANSITION.get(result)
            if result == "completed" and \
                    self.roles.is_outward(cur["assignee_agent"]):
                target = "review"  # outward work stops for you to check
            moved = None
            if target and cur["status"] not in ("awaiting_you", target):
                self.move_task(task_id, target, actor=actor)
                moved = target
            return {"pass_logged": logged, "status": moved or cur["status"],
                    "moved": moved is not None}

    # -- alerts (the no-AI health check) ----------------------------------
    @_locked
    def sync_alerts(self, current):
        open_rows = {(r["kind"], r["task_id"]): r["id"] for r in
                     self.conn.execute(
                         "SELECT * FROM alerts WHERE resolved=''")}
        new = 0
        seen = set()
        for kind, task_id, message, level in current:
            seen.add((kind, task_id))
            if (kind, task_id) not in open_rows:
                self.conn.execute(
                    "INSERT INTO alerts (kind, task_id, message, level,"
                    " created) VALUES (?,?,?,?,?)",
                    (kind, task_id, message, level, now()))
                new += 1
        cleared = 0
        for key, alert_id in open_rows.items():
            if key not in seen:
                self.conn.execute("UPDATE alerts SET resolved=? WHERE id=?",
                                  (now(), alert_id))
                cleared += 1
        self.conn.commit()
        return new, cleared

    @_locked
    def raise_alert(self, kind, task_id, message, level="alert"):
        """Put one alert on the board straight away, outside the health
        check. Used when the health check itself fails: a failure that only
        printed in a terminal window was a failure nobody saw. The next
        health check that works clears it, because sync_alerts closes every
        open alert it does not find again."""
        row = self.conn.execute(
            "SELECT id FROM alerts WHERE kind=? AND task_id=? AND resolved=''",
            (kind, task_id)).fetchone()
        if row:
            self.conn.execute("UPDATE alerts SET message=? WHERE id=?",
                              (message, row["id"]))
        else:
            self.conn.execute(
                "INSERT INTO alerts (kind, task_id, message, level, created)"
                " VALUES (?,?,?,?,?)", (kind, task_id, message, level, now()))
        self.conn.commit()

    @_locked
    def open_alerts(self):
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM alerts WHERE resolved='' ORDER BY level DESC,"
            " id DESC")]

    @_locked
    def dismiss_alert(self, alert_id, actor=None):
        if not (actor or "").strip():
            raise NotAllowed("refused: name yourself to dismiss an alert")
        self.conn.execute("UPDATE alerts SET resolved=? WHERE id=?",
                          (now(), alert_id))
        self.conn.commit()
        self.log(actor, "alert", str(alert_id), "dismissed", {})

    # -- reads ------------------------------------------------------------
    @_locked
    def state(self):
        projects = [dict(r) for r in self.conn.execute(
            "SELECT * FROM projects ORDER BY updated DESC")]
        tasks = [dict(r) for r in self.conn.execute(
            "SELECT t.*, (SELECT COUNT(*) FROM passes p WHERE"
            " p.task_id=t.id) AS pass_count FROM tasks t"
            " ORDER BY t.position, t.updated DESC")]
        return {"projects": projects, "tasks": tasks, "statuses": STATUSES}

    @_locked
    def recent_events(self, limit=40):
        """The activity list, with the NAME of what each change happened to.

        Without the names every row read "created this" and the list was a
        wall of pronouns. entity_title is the card or project; parent_title
        is the project a card lives in.
        """
        rows = [dict(r) for r in self.conn.execute(
            "SELECT * FROM events ORDER BY id DESC LIMIT ?", (limit,))]
        cards = {r["id"]: (r["title"], r["project_id"]) for r in
                 self.conn.execute("SELECT id, title, project_id FROM tasks")}
        projects = {r["id"]: r["title"] for r in
                    self.conn.execute("SELECT id, title FROM projects")}
        for e in rows:
            title, parent = "", ""
            if e["entity_type"] == "task" and e["entity_id"] in cards:
                title, pid = cards[e["entity_id"]]
                parent = projects.get(pid, "")
            elif e["entity_type"] == "project":
                title = projects.get(e["entity_id"], "")
            e["entity_title"] = title
            e["parent_title"] = parent
        return rows

    @_locked
    def agents_summary(self):
        agents = {}

        def entry(name):
            return agents.setdefault(name, {"open": [], "last_pass": None,
                                            "results": []})

        for t in self.conn.execute(
                "SELECT * FROM tasks WHERE status != 'done' AND archived=0 "
                "AND assignee_agent != '' ORDER BY updated DESC"):
            entry(t["assignee_agent"])["open"].append({
                "id": t["id"], "title": t["title"], "status": t["status"],
                "priority": t["priority"], "due": t["due"],
                "project_id": t["project_id"]})
        for p in self.conn.execute(
                "SELECT p.*, t.title AS task_title FROM passes p "
                "LEFT JOIN tasks t ON t.id = p.task_id "
                "ORDER BY p.id DESC LIMIT 300"):
            e = entry(p["agent"])
            if e["last_pass"] is None:
                e["last_pass"] = {"ts": p["ts"], "result": p["result"],
                                  "summary": p["summary"],
                                  "task_id": p["task_id"],
                                  "task_title": p["task_title"]}
            if len(e["results"]) < 8:
                e["results"].append(p["result"])
        return agents

    @_locked
    def metrics(self, window_days=7):
        """Board health numbers: cards finished, cards sent backwards, how
        often YOU had to step in, and cards stuck for 3+ days."""
        from collections import Counter
        from datetime import datetime, timedelta
        rank = {"backlog": 0, "ready": 1, "in_progress": 2, "review": 3,
                "awaiting_you": 4, "done": 5}
        fmt = "%Y-%m-%d %H:%M:%S"
        now_dt = datetime.now()
        cutoff = (now_dt - timedelta(days=window_days)).strftime(fmt)
        evs = [dict(r) for r in self.conn.execute(
            "SELECT * FROM events WHERE ts >= ? ORDER BY id", (cutoff,))]

        def detail(e):
            try:
                return json.loads(e["detail"] or "{}")
            except (ValueError, TypeError):
                return {}

        done_tasks = {e["entity_id"] for e in evs if e["action"] == "moved"
                      and detail(e).get("to") == "done"}
        rework = 0
        for e in evs:
            if e["action"] != "moved":
                continue
            d = detail(e)
            f, t = d.get("from"), d.get("to")
            if f in rank and t in rank and rank[t] < rank[f]:
                rework += 1
        human = sum(1 for e in evs
                    if self.roles.kind(e["actor"]) == "you")
        dispatches = sum(1 for e in evs if e["action"] == "dispatched")
        live = [dict(r) for r in self.conn.execute(
            "SELECT * FROM tasks WHERE archived=0")]
        stuck = 0
        for t in live:
            if t["status"] in ("ready", "in_progress", "blocked", "review"):
                try:
                    idle = (now_dt - datetime.strptime(
                        str(t["updated"])[:19], fmt)).days
                except ValueError:
                    idle = 0
                if idle >= 3:
                    stuck += 1
        return {"window_days": window_days,
                "generated": now_dt.strftime(fmt),
                "throughput_done": len(done_tasks),
                "rework_moves": rework,
                "human_interventions": human,
                "dispatches": dispatches,
                "stuck_active_cards": stuck,
                "active_cards": len(live),
                "by_status": dict(Counter(t["status"] for t in live))}

    @_locked
    def task_detail(self, task_id):
        task = self._task_row(task_id)
        handoffs = [dict(r) for r in self.conn.execute(
            "SELECT * FROM handoffs WHERE task_id=? ORDER BY id", (task_id,))]
        events = [dict(r) for r in self.conn.execute(
            "SELECT * FROM events WHERE entity_id=? ORDER BY id LIMIT 200",
            (task_id,))]
        passes = [dict(r) for r in self.conn.execute(
            "SELECT * FROM passes WHERE task_id=? ORDER BY id", (task_id,))]
        project = self.conn.execute("SELECT * FROM projects WHERE id=?",
                                    (task["project_id"],)).fetchone()
        return {"task": dict(task), "handoffs": handoffs, "events": events,
                "passes": passes,
                "project": dict(project) if project else None}


def open_store(cfg):
    """The store for a loaded config, with that config's roles."""
    from .config import db_path, roles_from
    return Store(db_path(cfg), roles=roles_from(cfg),
                 departments=[d.get("id") for d in
                              cfg.get("departments", []) or []])


__all__ = ["Store", "open_store", "NotAllowed", "STATUSES", "PASS_RESULTS",
           "PERFORMATIVES", "PRIORITIES"]
