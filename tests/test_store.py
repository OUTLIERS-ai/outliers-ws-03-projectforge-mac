"""Engine behaviour, carried over from the original ProjectForge tests and
fixed for the awaiting_you rename and the explicit actor."""
import pytest

from conftest import HUMAN, ORCH, WORKER, new_card

SENT = "2000-01-01 00:00:00"
FED = {"source_app": "crm-today",
       "project": {"ref": "p1", "title": "Pipeline", "department": "sales"},
       "tasks": [{"ref": "o1", "title": "Opp", "status": "ready",
                  "assignee_agent": "research-bot"}]}


def test_lifecycle(store):
    tid = new_card(store)
    d = store.dispatch(tid, actor=ORCH)
    assert d["task"]["status"] == "in_progress"
    assert d["task"]["orch_claimed"] == 1
    r = store.commit_pass(tid, WORKER, "did it", result="completed",
                          actor=ORCH)
    assert r["status"] == "done"


def test_transitions(store):
    t1 = new_card(store, status="in_progress")
    assert store.commit_pass(t1, WORKER, "x", result="needs-review",
                             actor=ORCH)["status"] == "review"
    t2 = new_card(store, status="in_progress")
    assert store.commit_pass(t2, WORKER, "x", result="failed",
                             actor=ORCH)["status"] == "blocked"
    # awaiting_you is your hold and must never be auto-overridden
    t3 = new_card(store, status="awaiting_you", owner=HUMAN)
    assert store.commit_pass(t3, WORKER, "x", result="completed",
                             actor=ORCH)["status"] == "awaiting_you"
    # outward owner cannot auto-finish: completed stops at review
    t4 = new_card(store, status="in_progress", owner="social-poster")
    assert store.commit_pass(t4, "social-poster", "x", result="completed",
                             actor=ORCH)["status"] == "review"


def test_no_awaiting_ashley_anywhere():
    from engine.db import STATUSES
    assert "awaiting_you" in STATUSES
    assert not any("ashley" in s for s in STATUSES)


def test_human_card_never_dispatched(store):
    tid = new_card(store, owner=HUMAN)
    q = store.next_actionable(limit=5)
    assert q["next"][0]["hold_reason"] == "yours"
    with pytest.raises(ValueError):
        store.dispatch(tid, actor=ORCH)


def test_federation_idempotent(store):
    payload = {**FED, "tasks": [{**FED["tasks"][0], "passes": [
        {"agent": "research-bot", "summary": "found", "key": "o1|found"}]}]}
    r1 = store.federate(payload, actor="crm-today")
    r2 = store.federate(payload, actor="crm-today")
    assert r1["tasks"][0]["created"] is True
    assert r2["tasks"][0]["created"] is False
    assert store.conn.execute("SELECT COUNT(*) c FROM tasks").fetchone()[
        "c"] == 1
    assert r2["passes_logged"] == 0


def test_orch_claimed_freeze(store):
    store.federate(FED, actor="crm-today")
    tid = store.conn.execute(
        "SELECT id FROM tasks WHERE external_ref='o1'").fetchone()["id"]
    store.dispatch(tid, actor=ORCH)
    store.federate(FED, actor="crm-today")  # the source still says 'ready'
    assert store.task_detail(tid)["task"]["status"] == "in_progress"


def test_wip_and_tracking(store):
    store.federate(actor="tracker", payload={"source_app": "tracker",
                    "project": {"ref": "fp", "title": "F",
                                "department": "operations"},
                    "tasks": [{"ref": "P1", "title": "a number we watch",
                               "status": "tracking"}]})
    new_card(store)
    q = store.next_actionable(limit=5, wip_limits={"in_progress": 1})
    assert q["wip"]["in_progress"] == 0
    assert q["next"][0]["dispatchable"]


def test_intent(store):
    t1 = new_card(store, status="in_progress")
    store.commit_pass(t1, WORKER, "x", result="failed", actor=ORCH)
    row = store.conn.execute("SELECT intent FROM passes WHERE task_id=?",
                             (t1,)).fetchone()
    assert row["intent"] == "FAILURE"
    with pytest.raises(ValueError):
        store.add_pass(t1, WORKER, "x", intent="NONSENSE")


def test_scope_enforcement(store):
    cards = {"sales-bot": {"slug": "sales-bot", "scopes": {"depts": ["sales"]},
                           "max_activations_per_day": 20}}
    ok = new_card(store, owner="sales-bot", dept="sales")
    bad = new_card(store, owner="sales-bot", dept="content")
    holds = {n["task_id"]: n["hold_reason"] for n in
             store.next_actionable(limit=9, cards=cards)["next"]}
    assert holds[ok] == "" and holds[bad] == "out_of_scope"
    with pytest.raises(ValueError):
        store.dispatch(bad, actor=ORCH, cards=cards)
    assert store.dispatch(ok, actor=ORCH, cards=cards)["task"][
        "status"] == "in_progress"


def test_activation_cap(store):
    cards = {WORKER: {"slug": WORKER, "max_activations_per_day": 1}}
    t1 = new_card(store)
    t2 = new_card(store)
    store.dispatch(t1, actor=ORCH, cards=cards)
    holds = {n["task_id"]: n["hold_reason"] for n in
             store.next_actionable(limit=9, cards=cards)["next"]}
    assert holds[t2] == "cap_exhausted"


def test_reaper(store):
    tid = new_card(store)
    store.dispatch(tid, actor=ORCH)
    assert tid in store.reap_stale_dispatches(ttl_min=0)
    t = store.task_detail(tid)["task"]
    assert t["status"] == "ready" and t["orch_claimed"] == 0
    t2 = new_card(store)
    store.dispatch(t2, actor=ORCH)
    store.add_pass(t2, WORKER, "made progress")
    assert t2 not in store.reap_stale_dispatches(ttl_min=0)


def test_cycle_detection(store):
    a = new_card(store, status="backlog")
    b = new_card(store, status="backlog")
    store.set_blockers(b, [a], actor=HUMAN)
    with pytest.raises(ValueError):
        store.set_blockers(a, [b], actor=HUMAN)


def test_metrics(store):
    tid = new_card(store)
    store.dispatch(tid, actor=ORCH)
    store.commit_pass(tid, WORKER, "done", result="completed", actor=ORCH)
    m = store.metrics(7)
    assert m["throughput_done"] == 1 and m["dispatches"] == 1
    assert m["human_interventions"] >= 2  # you opened the project + card
    assert "done" in m["by_status"]


def test_intake_adopts_and_promotes(store):
    pid = store.add_project("P", "content", actor=HUMAN)
    rid = store.add_task(pid, "ownerless ready card", status="ready",
                         actor=HUMAN)
    bid = store.add_task(pid, "backlog card", actor=HUMAN)
    store.run_intake({"content": "lead-bot"}, actor=ORCH)
    r = store.task_detail(rid)["task"]
    b = store.task_detail(bid)["task"]
    assert r["assignee_agent"] == "lead-bot" and r["status"] == "ready"
    assert b["assignee_agent"] == "lead-bot" and b["status"] == "ready"


def test_intake_routing_by_keyword(store):
    pid = store.add_project("P", "content", actor=HUMAN)
    tid = store.add_task(pid, "Make the podcast notes", actor=HUMAN)
    store.run_intake({}, routing=[{"match": ["podcast"],
                                   "agent": "podcast-bot"}], actor=ORCH)
    assert store.task_detail(tid)["task"]["assignee_agent"] == "podcast-bot"


def test_federation_updated_honesty(store):
    store.federate(FED, actor="crm-today")
    store.conn.execute("UPDATE tasks SET updated=?, synced=''", (SENT,))
    store.conn.commit()
    before = store.conn.execute("SELECT COUNT(*) c FROM events").fetchone()[
        "c"]
    store.federate(FED, actor="crm-today")
    t = store.conn.execute("SELECT updated, synced FROM tasks").fetchone()
    after = store.conn.execute("SELECT COUNT(*) c FROM events").fetchone()["c"]
    assert t["updated"] == SENT and t["synced"] != ""
    assert after == before
    store.federate({**FED, "tasks": [{**FED["tasks"][0],
                                      "status": "in_progress"}]},
                   actor="crm-today")
    t = store.conn.execute("SELECT updated, status FROM tasks").fetchone()
    assert t["updated"] != SENT and t["status"] == "in_progress"


def test_crm_person_link(store):
    tid = new_card(store)
    store.update_task(tid, {"crm_person": "People/Sam Carter.md"},
                      actor=HUMAN)
    assert store.task_detail(tid)["task"]["crm_person"] == \
        "People/Sam Carter.md"
