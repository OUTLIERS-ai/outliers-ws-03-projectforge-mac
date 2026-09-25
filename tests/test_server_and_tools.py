"""The web board, the no-work early exit, the CRM Today reader, the summary
note, and the agents' tool."""
import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from conftest import HUMAN, MANAGER, ORCH, REPO, WORKER, new_card
from test_rules import GOOD


def _boot(store, cfg):
    from engine.server import make_server
    httpd = make_server(store, cfg, REPO, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def _post(url, path, payload):
    req = urllib.request.Request(url + path, data=json.dumps(payload).encode(),
                                 method="POST",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def test_server_rules(store):
    cfg = {"human": HUMAN, "departments": [], "summary_note": ""}
    tid = new_card(store)
    httpd, url = _boot(store, cfg)
    try:
        with urllib.request.urlopen(url + "/", timeout=5) as r:
            assert b"ProjectForge" in r.read()
        with urllib.request.urlopen(url + "/api/state", timeout=5) as r:
            st = json.loads(r.read())
        assert st["config"]["human"] == HUMAN
        assert "awaiting_you" in st["statuses"]
        code, _ = _post(url, "/api/task/move", {"task_id": tid,
                                                "status": "done"})
        assert code == 400  # no actor
        code, body = _post(url, "/api/task/move",
                           {"task_id": tid, "status": "done",
                            "actor": WORKER})
        assert code == 403 and "refused" in body["error"]
        code, _ = _post(url, "/api/task/move",
                        {"task_id": tid, "status": "review", "actor": HUMAN})
        assert code == 200
        code, _ = _post(url, "/api/handoff",
                        {"task_id": tid, "from_agent": WORKER,
                         "to_agent": "editor-bot", "done": "x"})
        assert code == 400
        code, _ = _post(url, "/api/handoff",
                        {"task_id": tid, "from_agent": WORKER,
                         "to_agent": "editor-bot", **GOOD})
        assert code == 200
        code, _ = _post(url, "/api/open",
                        {"actor": WORKER, "project_title": "P",
                         "department": "content", "title": "t"})
        assert code == 403
        code, body = _post(url, "/api/open",
                           {"actor": MANAGER, "project_title": "P",
                            "department": "content", "title": "t"})
        assert code == 200 and body["id"].startswith("pf-t-")
    finally:
        httpd.shutdown()


# ---- the no-work early exit ---------------------------------------------

def test_empty_board_waiting_is_zero(store):
    cfg = {"hygiene": {}, "intake": {"auto_pickup": True}, "departments": []}
    assert store.work_waiting(cfg)["total"] == 0


def test_human_and_ownerless_cards_do_not_count(store):
    cfg = {"hygiene": {}, "intake": {"auto_pickup": True}, "departments": []}
    new_card(store, owner=HUMAN)
    new_card(store, owner="")
    assert store.work_waiting(cfg)["total"] == 0


def test_backlog_with_owner_counts(store):
    cfg = {"hygiene": {}, "intake": {"auto_pickup": True}, "departments": []}
    new_card(store, status="backlog")
    w = store.work_waiting(cfg)
    assert w["total"] == 1 and w["from_backlog"] == 1
    # counting wrote nothing: the card is still in backlog
    assert store.state()["tasks"][0]["status"] == "backlog"


def test_run_if_ready_exits_without_claude(cfg_env, monkeypatch, capsys):
    import subprocess
    from tools import run_if_ready

    def boom(*a, **k):
        raise AssertionError("Claude must not start on an empty board")
    monkeypatch.setattr(subprocess, "run", boom)
    assert run_if_ready.main([]) == 0
    assert "Claude not started" in capsys.readouterr().out
    log = cfg_env.parent / "data" / "run_if_ready.log"
    assert "0 cards" in log.read_text(encoding="utf-8")


def test_run_if_ready_starts_claude_when_work(cfg_env, monkeypatch):
    import subprocess
    from engine.config import load_config
    from engine.db import open_store
    from tools import run_if_ready
    st = open_store(load_config())
    new_card(st)
    st.close()
    calls = []

    class R:
        returncode = 0
        stdout = "ran"

    def fake_run(cmd, **k):
        calls.append((cmd, k))
        return R()
    monkeypatch.setattr(run_if_ready.shutil, "which", lambda n: "claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert run_if_ready.main(["--dry-run"]) == 0
    assert calls == []
    assert run_if_ready.main([]) == 0
    assert len(calls) == 1
    assert calls[0][0][1:3] == ["-p", "/forge-run"]
    assert "dontAsk" in calls[0][0]
    assert "creationflags" in calls[0][1]


# ---- CRM Today reader ---------------------------------------------------

def test_crm_today_makes_linked_cards_once(cfg_env, tmp_path, monkeypatch):
    crm = tmp_path / "crm"
    (crm / "People").mkdir(parents=True)
    (crm / "People" / "Sam Carter.md").write_text("# Sam", encoding="utf-8")
    (crm / "Today.md").write_text(
        "# Today\n\n| # | Who | Why they are here | When |\n|---|---|---|---|\n"
        "| 1 | Sam Carter | Replied | 3 hours ago |\n"
        "| 2 | Priya Shah | A new connection | 2 days ago |\n",
        encoding="utf-8")
    data = json.loads(cfg_env.read_text(encoding="utf-8"))
    data["crm_vault"] = str(crm)
    cfg_env.write_text(json.dumps(data), encoding="utf-8")
    from adapters import crm_today
    assert crm_today.main([]) == 0
    assert crm_today.main([]) == 0  # second run: no copies
    from engine.config import load_config
    from engine.db import open_store
    st = open_store(load_config())
    tasks = st.state()["tasks"]
    st.close()
    assert len(tasks) == 2
    sam = [t for t in tasks if "Sam" in t["title"]][0]
    assert sam["crm_person"] == "People/Sam Carter.md"
    assert sam["status"] == "awaiting_you"


# ---- summary note -------------------------------------------------------

def test_summary_note_written(store, tmp_path):
    from engine import mirror
    new_card(store, status="awaiting_you", owner=HUMAN)
    note = tmp_path / "vault" / "ProjectForge Board.md"
    note.parent.mkdir()   # the board never makes a folder in your vault
    out = mirror.write_mirrors(store, {"summary_note": str(note),
                                       "departments": []})
    assert out == [str(note)]
    text = note.read_text(encoding="utf-8")
    assert "Awaiting you" in text and "Draft 3 posts" in text
    assert not list(note.parent.glob("*.tmp"))


def test_no_summary_note_by_default(store):
    from engine import mirror
    assert mirror.write_mirrors(store, {"summary_note": ""}) == []


# ---- the agents' tool ---------------------------------------------------

def test_forge_agent_worker_flow(cfg_env, monkeypatch, capsys):
    monkeypatch.setenv("FORGE_DIR", str(REPO))
    from adapters import forge_agent
    from engine.config import load_config
    from engine.db import open_store
    st = open_store(load_config())
    tid = new_card(st, status="in_progress")
    st.close()
    assert forge_agent.main(["pass", "--card", tid, "--agent", WORKER,
                             "--summary", "wrote 2 drafts",
                             "--outputs", "drafts/a.md"]) == 0
    assert forge_agent.main(["open", "--agent", WORKER, "--dept", "content",
                             "--project", "P", "--title", "x"]) == 3
    assert forge_agent.main(["handoff", "--card", tid, "--from", WORKER,
                             "--to", "editor-bot", "--done", "x"]) == 2
    assert forge_agent.main(["open", "--agent", MANAGER, "--dept", "content",
                             "--project", "P", "--title", "new work"]) == 0
    st = open_store(load_config())
    d = st.task_detail(tid)
    st.close()
    assert d["task"]["status"] == "in_progress"
    assert d["passes"][0]["summary"] == "wrote 2 drafts"


def test_forge_cli_refuses_worker_move(cfg_env, capsys):
    import forge
    from engine.config import load_config
    from engine.db import open_store
    st = open_store(load_config())
    tid = new_card(st)
    st.close()
    assert forge.main(["move", tid, "done", "--actor", WORKER]) == 3
    assert "refused" in capsys.readouterr().out
    assert forge.main(["move", tid, "done", "--actor", HUMAN]) == 0


def test_every_subprocess_is_windowless():
    import re
    for p in list(REPO.rglob("*.py")):
        if "tests" in p.parts:
            continue
        s = p.read_text(encoding="utf-8")
        for m in re.finditer(r"subprocess\.(run|Popen|call|check_output)\s*\(",
                             s):
            assert "creationflags" in s[m.start():m.start() + 600], \
                f"{p.name} spawns a process without CREATE_NO_WINDOW"
