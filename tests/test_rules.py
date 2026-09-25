"""The single-writer rule and the 5-field handover, enforced in the store."""
import pytest

from engine.rules import NotAllowed, check_handover
from conftest import HUMAN, MANAGER, ORCH, WORKER, new_card

GOOD = dict(done="Wrote drafts/post-1.md and drafts/post-2.md",
            decisions="Kept it to 2 posts because the brief said one a day",
            state="2 of 3 drafts written; post 3 not started",
            next_first="Read drafts/post-1.md then write post 3",
            warnings="none")


# ---- single-writer rule ---------------------------------------------------

def test_worker_cannot_move_a_card(store):
    tid = new_card(store)
    with pytest.raises(NotAllowed):
        store.move_task(tid, "done", actor=WORKER)
    assert store.task_detail(tid)["task"]["status"] == "ready"


def test_manager_cannot_move_a_card(store):
    tid = new_card(store)
    with pytest.raises(NotAllowed):
        store.move_task(tid, "done", actor=MANAGER)


def test_orchestrator_and_you_can_move(store):
    tid = new_card(store)
    store.move_task(tid, "blocked", actor=ORCH)
    store.move_task(tid, "ready", actor=HUMAN)
    assert store.task_detail(tid)["task"]["status"] == "ready"


def test_nameless_write_refused(store):
    tid = new_card(store)
    with pytest.raises(NotAllowed):
        store.move_task(tid, "done", actor=None)
    with pytest.raises(NotAllowed):
        store.add_pass(tid, "", "did something")


def test_worker_cannot_open_a_card(store):
    pid = store.add_project("P", "content", actor=HUMAN)
    with pytest.raises(NotAllowed):
        store.add_task(pid, "sneaky new work", actor=WORKER)
    with pytest.raises(NotAllowed):
        store.open_card(WORKER, "P", "content", "sneaky")


def test_manager_can_open_a_card(store):
    tid = store.open_card(MANAGER, "Spring launch", "content", "Draft posts",
                          assignee_agent=WORKER)
    t = store.task_detail(tid)["task"]
    assert t["status"] == "backlog" and t["assignee_agent"] == WORKER
    # the same project is reused, not duplicated
    store.open_card(MANAGER, "Spring launch", "content", "Draft emails")
    assert len(store.state()["projects"]) == 1


def test_manager_open_cannot_start_in_done(store):
    with pytest.raises(ValueError):
        store.open_card(MANAGER, "P", "content", "x", status="done")


def test_worker_pass_is_recorded_but_card_does_not_move(store):
    tid = new_card(store, status="in_progress")
    store.add_pass(tid, WORKER, "wrote 2 drafts", result="completed")
    d = store.task_detail(tid)
    assert d["task"]["status"] == "in_progress"
    assert d["passes"][0]["agent"] == WORKER


def test_worker_cannot_commit_or_dispatch(store):
    tid = new_card(store)
    with pytest.raises(NotAllowed):
        store.dispatch(tid, actor=WORKER)
    with pytest.raises(NotAllowed):
        store.commit_pass(tid, WORKER, "done", result="completed",
                          actor=WORKER)
    assert store.task_detail(tid)["task"]["status"] == "ready"


def test_worker_cannot_edit_or_tag(store):
    tid = new_card(store)
    with pytest.raises(NotAllowed):
        store.update_task(tid, {"assignee_agent": "someone"}, actor=WORKER)
    with pytest.raises(NotAllowed):
        store.set_tags(tid, ["x"], actor=WORKER)
    with pytest.raises(NotAllowed):
        store.set_archived(tid, True, actor=WORKER)


def test_worker_cannot_drag_between_columns(store):
    tid = new_card(store)
    with pytest.raises(NotAllowed):
        store.reorder("done", [tid], actor=WORKER)


def test_only_orchestrator_runs_intake(store):
    with pytest.raises(NotAllowed):
        store.run_intake({}, actor=WORKER)


def test_worker_can_comment(store):
    tid = new_card(store)
    store.comment(tid, "question for my manager", actor=WORKER)
    ev = [e for e in store.task_detail(tid)["events"]
          if e["action"] == "comment"]
    assert ev and ev[0]["actor"] == WORKER


# ---- the 5-field handover -------------------------------------------------

@pytest.mark.parametrize("missing", list(GOOD))
def test_handover_missing_any_field_refused(store, missing):
    tid = new_card(store)
    fields = dict(GOOD)
    fields[missing] = ""
    with pytest.raises(ValueError, match="handover refused"):
        store.handoff(tid, WORKER, "editor-bot", **fields)
    assert store.task_detail(tid)["handoffs"] == []
    assert store.task_detail(tid)["task"]["assignee_agent"] == WORKER


def test_handover_placeholder_refused(store):
    tid = new_card(store)
    with pytest.raises(ValueError):
        store.handoff(tid, WORKER, "editor-bot", **{**GOOD, "state": "tbd"})


def test_handover_decisions_must_say_why(store):
    tid = new_card(store)
    with pytest.raises(ValueError, match="because"):
        store.handoff(tid, WORKER, "editor-bot",
                      **{**GOOD, "decisions": "kept it short"})


def test_full_handover_accepted(store):
    tid = new_card(store)
    store.handoff(tid, WORKER, "editor-bot", **GOOD)
    d = store.task_detail(tid)
    h = d["handoffs"][0]
    assert h["done"].startswith("Wrote") and h["warnings"] == "none"
    assert d["task"]["assignee_agent"] == "editor-bot"


def test_decisions_none_is_allowed():
    assert check_handover({**GOOD, "decisions": "none"}) == []
