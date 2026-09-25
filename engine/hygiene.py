"""ProjectForge hygiene engine — deterministic board health, no AI.

Run on a cycle (forge.py daemon) or once (forge.py hygiene). Rules:
  - overdue          due date passed, card not done
  - due-soon         due within N days
  - stale            no activity for N days in an active lane
  - blocked-stuck    sitting in blocked for N days
  - unassigned       in an active lane with no agent
  - wip-overload     lane exceeds its WIP limit
Actions (not just alerts):
  - auto-archive Done cards untouched for N days (never deleted)
Thresholds come from config.json "hygiene" block; defaults below.
"""
import time
from datetime import datetime, timedelta

from . import mirror

DEFAULTS = {
    "stale_days": 5,
    "blocked_days": 3,
    "due_soon_days": 2,
    "done_archive_days": 14,
    "dispatch_ttl_min": 45,
    "wip_limits": {"in_progress": 10, "review": 8, "awaiting_you": 10},
}

ACTIVE_LANES = ("ready", "in_progress", "review")


def _dt(ts):
    return datetime.strptime(str(ts)[:19], "%Y-%m-%d %H:%M:%S")


def _check_one(store, cfg, t, today, alerts):
    """Check 1 card. Returns 1 if it was auto-archived, else 0."""
    tid, title = t["id"], t["title"]
    # auto-archive old done cards (action, not alert)
    if (t["status"] == "done"
            and _dt(t["updated"]) < today - timedelta(
                days=cfg["done_archive_days"])):
        store.set_archived(tid, True, actor="hygiene")
        return 1
    if t["status"] == "done":
        return 0
    # an agent has asked for a person: this outlives the activity list
    if t.get("escalated"):
        alerts.append(("needs you", tid,
                       f"'{title}' - {t['assignee_agent'] or 'an agent'} "
                       f"asked for a person", "alert"))
    # overdue / due soon
    if t["due"]:
        try:
            due = datetime.strptime(t["due"], "%Y-%m-%d")
        except ValueError:
            alerts.append(("bad-due", tid,
                           f"'{title}' has a due date the board cannot read "
                           f"('{t['due']}'). Set it again as YYYY-MM-DD, or "
                           f"clear it, so this card is checked again.",
                           "alert"))
            due = None
        if due is not None:
            days_left = (due + timedelta(hours=23, minutes=59) - today).days
            if days_left < 0:
                alerts.append(("overdue", tid,
                               f"'{title}' was due {t['due']}", "alert"))
            elif days_left <= cfg["due_soon_days"]:
                alerts.append(("due-soon", tid,
                               f"'{title}' due {t['due']}", "warn"))
    # stale in active lanes
    idle = (today - _dt(t["updated"])).days
    if t["status"] in ACTIVE_LANES and idle >= cfg["stale_days"]:
        alerts.append(("stale", tid,
                       f"'{title}' untouched for {idle} days in "
                       f"{t['status']}", "warn"))
    # stuck in blocked
    if t["status"] == "blocked" and idle >= cfg["blocked_days"]:
        alerts.append(("blocked-stuck", tid,
                       f"'{title}' blocked for {idle} days", "alert"))
    # active but unassigned
    if t["status"] in ACTIVE_LANES and not t["assignee_agent"]:
        alerts.append(("unassigned", tid,
                       f"'{title}' is in {t['status']} with no agent", "warn"))
    return 0


def run(store, config, base_dir):
    cfg = {**DEFAULTS, **config.get("hygiene", {})}
    # closed-loop action: reclaim cards whose dispatched agent never reported
    # back within the TTL (a hung/crashed agent) — return them to the queue.
    reaped = store.reap_stale_dispatches(cfg.get("dispatch_ttl_min", 45))
    state = store.state()
    today = datetime.now()
    alerts = []
    archived = 0

    live = [t for t in state["tasks"] if not t.get("archived")]
    titles = {t["id"]: t["title"] for t in live}

    for t in live:
        tid, title = t["id"], t["title"]
        # one card the check cannot read must never stop the whole check:
        # before this, a single due date in words stopped every later run
        try:
            archived += _check_one(store, cfg, t, today, alerts)
        except Exception as e:  # noqa: BLE001 - name the card, carry on
            alerts.append(("check-failed", tid,
                           f"'{title}' could not be checked: {e}", "alert"))

    # WIP limits per lane. Monitored federated mirrors live in the `tracking`
    # lane (outside the work lifecycle), so the active lanes here are all
    # genuine work — a plain count is correct.
    for lane, limit in cfg["wip_limits"].items():
        n = sum(1 for t in live
                if t["status"] == lane and t["status"] != "done")
        if n > limit:
            alerts.append(("wip-overload", f"lane:{lane}",
                           f"{n} cards in {lane} (limit {limit})", "alert"))

    # the summary note is a courtesy, but a note that is silently not being
    # written is worth an alert: the vault may have been renamed or moved
    try:
        mirror.write_mirrors(store, config, base_dir)
    except mirror.VaultMissing as e:
        alerts.append(("summary-note", "note", str(e), "alert"))
    except OSError as e:
        alerts.append(("summary-note", "note",
                       f"the summary note was not written: {e}", "warn"))

    new, cleared = store.sync_alerts(alerts)
    if archived:
        store.log("hygiene", "board", "done-lane", "auto-archived",
                  {"count": archived})
    return {"checked": len(live), "open_alerts": len(alerts),
            "new": new, "cleared": cleared, "auto_archived": archived,
            "reaped": len(reaped),
            "ts": time.strftime("%Y-%m-%d %H:%M:%S")}
