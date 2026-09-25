"""Faults found on 2026-09-22 by the security audit, the cold walk-through
and the usability teardown. Each test was written first, seen to fail on the
old code, and then the fix made it pass. The cause is named above each one."""
import json
import socket
import threading
import urllib.error
import urllib.request

import pytest

from conftest import HUMAN, MANAGER, ORCH, REPO, WORKER, new_card
from engine.rules import NotAllowed


def _boot(store, cfg, port=0):
    from engine.server import make_server
    httpd = make_server(store, cfg, REPO, port=port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _raw(port, method, path, body=None, headers=None):
    """Send a request with exactly the headers given (urllib would add its
    own Host header; a web page controls Origin and Content-Type)."""
    data = json.dumps(body).encode() if body is not None else b""
    h = {"Host": f"127.0.0.1:{port}", "Content-Length": str(len(data))}
    h.update(headers or {})
    s = socket.create_connection(("127.0.0.1", port), timeout=5)
    try:
        lines = [f"{method} {path} HTTP/1.1"] + \
            [f"{k}: {v}" for k, v in h.items()] + ["Connection: close", "", ""]
        s.sendall("\r\n".join(lines).encode() + data)
        got = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            got += chunk
    finally:
        s.close()
    return int(got.split(b" ", 2)[1])


CFG = {"human": HUMAN, "departments": [], "summary_note": "",
       "federate_sources": ["crm-today"]}
OPEN = {"actor": HUMAN, "project_title": "P", "department": "content",
        "title": "Injected ready card", "status": "ready",
        "assignee_agent": WORKER}


# ---- 1. any website could write to the board ----------------------------
# Cause: the server never checked Host, Origin or Content-Type, and read any
# body as JSON, so a page could send a "simple" text/plain POST.

def test_outside_page_cannot_write(store):
    httpd, port = _boot(store, CFG)
    try:
        code = _raw(port, "POST", "/api/open", OPEN,
                    {"Origin": "https://attacker.example",
                     "Content-Type": "text/plain"})
        assert code in (403, 415)
        code = _raw(port, "POST", "/api/open", OPEN,
                    {"Origin": "https://attacker.example",
                     "Content-Type": "application/json"})
        assert code == 403
        code = _raw(port, "POST", "/api/open", OPEN,
                    {"Content-Type": "text/plain"})
        assert code == 415
        code = _raw(port, "POST", "/api/open", OPEN,
                    {"Host": "attacker.example",
                     "Content-Type": "application/json"})
        assert code == 403
        assert store.state()["tasks"] == []
        # the board's own page still works
        code = _raw(port, "POST", "/api/open", OPEN,
                    {"Origin": f"http://127.0.0.1:{port}",
                     "Content-Type": "application/json"})
        assert code == 200
    finally:
        httpd.shutdown()


def test_rebound_address_cannot_read(store):
    httpd, port = _boot(store, CFG)
    try:
        assert _raw(port, "GET", "/api/state", None,
                    {"Host": "attacker.example"}) == 403
        assert _raw(port, "GET", "/api/state", None,
                    {"Host": f"localhost:{port}"}) == 200
    finally:
        httpd.shutdown()


# ---- 2. single-writer gaps ------------------------------------------------
# Cause: /api/federate needed no name; forge.py defaulted --actor to you.

FED = {"source_app": "crm-today",
       "project": {"ref": "p", "title": "T", "department": "sales"},
       "tasks": [{"ref": "a", "title": "Injected", "status": "done"}]}


def test_federate_needs_a_listed_source(store):
    with pytest.raises(NotAllowed):
        store.federate(FED)
    with pytest.raises(NotAllowed):
        store.federate(FED, actor="some-page")
    assert store.federate(FED, actor="crm-today")["tasks"][0]["created"]


def test_federate_over_http_needs_actor(store):
    httpd, port = _boot(store, CFG)
    try:
        hdr = {"Content-Type": "application/json"}
        assert _raw(port, "POST", "/api/federate", FED, hdr) in (400, 403)
        assert _raw(port, "POST", "/api/federate",
                    {**FED, "actor": "writer-bot"}, hdr) == 403
        assert _raw(port, "POST", "/api/federate",
                    {**FED, "actor": "crm-today"}, hdr) == 200
    finally:
        httpd.shutdown()


def test_forge_cli_needs_actor(cfg_env):
    import forge
    from engine.config import load_config
    from engine.db import open_store
    st = open_store(load_config())
    tid = new_card(st)
    st.close()
    for argv in (["move", tid, "done"], ["archive", tid],
                 ["set", tid, "--priority", "high"],
                 ["comment", tid, "hello"], ["dispatch", tid],
                 ["intake"], ["commit", tid, WORKER, "x"],
                 ["add-project", "P", "--dept", "content"]):
        with pytest.raises(SystemExit) as e:
            forge.main(argv)
        assert e.value.code == 2, argv


def test_orchestrator_cannot_take_a_card_out_of_awaiting_you(store):
    tid = new_card(store, status="awaiting_you", owner=HUMAN)
    with pytest.raises(NotAllowed):
        store.move_task(tid, "ready", actor=ORCH)
    with pytest.raises(NotAllowed):
        store.reorder("ready", [tid], actor=ORCH)
    assert store.task_detail(tid)["task"]["status"] == "awaiting_you"
    # the orchestrator may still put a card INTO it (the "failed twice" rule)
    t2 = new_card(store, status="blocked")
    store.move_task(t2, "awaiting_you", actor=ORCH)
    # and you may take it out
    store.move_task(tid, "done", actor=HUMAN)


def test_a_card_cannot_be_claimed_twice(store):
    tid = new_card(store)
    store.dispatch(tid, actor=ORCH)
    with pytest.raises(ValueError, match="Ready"):
        store.dispatch(tid, actor=ORCH)


def test_commit_needs_a_claimed_card(store):
    tid = new_card(store)  # Ready, never claimed
    with pytest.raises(ValueError):
        store.commit_pass(tid, WORKER, "x", result="completed", actor=ORCH)
    assert store.task_detail(tid)["task"]["status"] == "ready"


def test_progressed_goes_back_to_ready(store):
    # Cause: "progressed" left the card In Progress, and only Ready cards are
    # handed out, so the card sat with "working..." on it for ever.
    tid = new_card(store)
    store.dispatch(tid, actor=ORCH)
    r = store.commit_pass(tid, WORKER, "half done", result="progressed",
                          actor=ORCH)
    assert r["status"] == "ready"


# ---- 3. two boards on one port (Windows) ----------------------------------
# Cause: http.server sets SO_REUSEADDR, which on Windows lets a second
# program bind the same port silently.

def test_second_board_on_same_port_is_refused(store):
    httpd, port = _boot(store, CFG)
    try:
        from engine.server import make_server
        with pytest.raises(OSError):
            make_server(store, CFG, REPO, port=port)
    finally:
        httpd.shutdown()


# ---- 4. the unattended run's permissions ---------------------------------
# Cause: run_if_ready.py started claude -p "/forge-run" with no permission
# settings, so it either stalled on a question or ran with whatever the
# member's default mode allowed.

def test_unattended_run_allows_only_board_commands(cfg_env, monkeypatch):
    from engine.config import load_config
    from tools import run_if_ready
    monkeypatch.setattr(run_if_ready.shutil, "which", lambda n: "claude")
    cfg = load_config()
    cfg["adapter_dir"] = "C:/Users/sam/.claude/projectforge"
    cmd = run_if_ready.claude_command(cfg)
    assert cmd[1:3] == ["-p", "/forge-run"]
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    allowed = cmd[cmd.index("--allowedTools") + 1:]
    allowed = allowed[:next((i for i, a in enumerate(allowed)
                             if a.startswith("--")), len(allowed))]
    assert "Bash" not in allowed and "Bash(*)" not in allowed
    bash = [a for a in allowed if a.startswith("Bash(")]
    assert bash and all("forge.py" in a or "forge_agent.py" in a
                        for a in bash)
    assert any(a.endswith("waiting)") for a in bash)
    assert any("move * awaiting_you" in a for a in bash)
    assert not any(" move *)" in a for a in bash)


# ---- 5. uninstall must give back the member's own command -----------------
# Cause: a 2nd install backed up our own 1st copy, and uninstall restored the
# newest backup, which was ours.

def test_uninstall_after_two_installs_restores_members_command(
        tmp_path, monkeypatch):
    import install
    from test_install import fake_setup, run
    home, ch, sb, crm, cfgp = fake_setup(tmp_path, monkeypatch)
    (ch / "commands").mkdir(parents=True)
    mine = "# the member's OWN forge-run command\n"
    (ch / "commands" / "forge-run.md").write_text(mine, encoding="utf-8")
    assert run(sb, crm, ["--name", "you"]) == 0
    assert run(sb, crm, ["--name", "Sam"]) == 0
    assert install.main(["--uninstall", "--yes"]) == 0
    assert (ch / "commands" / "forge-run.md").read_text(
        encoding="utf-8") == mine


# ---- critic: typing lost, blank titles, typo agents, refusals invisible ---

def test_blank_title_refused(store):
    tid = new_card(store)
    with pytest.raises(ValueError, match="title"):
        store.update_task(tid, {"title": "   "}, actor=HUMAN)
    pid = store.add_project("P2", "content", actor=HUMAN)
    with pytest.raises(ValueError, match="title"):
        store.add_task(pid, "", actor=HUMAN)


def test_unknown_department_refused(tmp_path):
    from engine.db import Store
    from engine.rules import Roles
    s = Store(tmp_path / "d.db", roles=Roles(human=HUMAN),
              departments=["content", "sales"])
    try:
        with pytest.raises(ValueError, match="department"):
            s.add_project("P", "marketing", actor=HUMAN)
        s.add_project("P", "content", actor=HUMAN)
    finally:
        s.close()


def test_typo_agent_is_not_handed_work(tmp_path):
    from engine.db import Store
    from engine.rules import Roles
    s = Store(tmp_path / "a.db",
              roles=Roles(human=HUMAN, agents=[WORKER, MANAGER]))
    try:
        pid = s.add_project("P", "content", actor=HUMAN)
        tid = s.add_task(pid, "x", status="ready", assignee_agent="writerbot",
                         actor=HUMAN)
        n = s.next_actionable(limit=5)["next"][0]
        assert not n["dispatchable"] and n["hold_reason"] == "unknown_agent"
        with pytest.raises(ValueError):
            s.dispatch(tid, actor=ORCH)
    finally:
        s.close()


def test_refused_write_leaves_a_mark(store):
    tid = new_card(store)
    with pytest.raises(NotAllowed):
        store.move_task(tid, "done", actor=WORKER)
    with pytest.raises(ValueError):
        store.handoff(tid, WORKER, "editor-bot", done="x")
    ev = [e for e in store.task_detail(tid)["events"]
          if e["action"] == "refused"]
    assert len(ev) == 2 and ev[0]["actor"] == WORKER
    assert store.refusals_today() == 2


def test_escalate_is_its_own_event(cfg_env, monkeypatch):
    monkeypatch.setenv("FORGE_DIR", str(REPO))
    from adapters import forge_agent
    from engine.config import load_config
    from engine.db import open_store
    st = open_store(load_config())
    tid = new_card(st)
    st.close()
    assert forge_agent.main(["escalate", "--card", tid, "--agent", WORKER,
                             "--note", "I need the client's login"]) == 0
    st = open_store(load_config())
    ev = [e["action"] for e in st.task_detail(tid)["events"]]
    st.close()
    assert "escalation" in ev


# ---- walk-through: CRM Today links, summary note links, limit files -------

def test_crm_today_reads_wiki_links(cfg_env, tmp_path):
    crm = tmp_path / "crm"
    (crm / "People").mkdir(parents=True)
    (crm / "People" / "Dan Pike.md").write_text("# Dan", encoding="utf-8")
    (crm / "People" / "Mia Lowe.md").write_text("# Mia", encoding="utf-8")
    (crm / "Today.md").write_text(
        "| # | Who | Why | When |\n|---|---|---|---|\n"
        "| 1 | [[People/Dan Pike]] | Asked about payroll | today |\n"
        "| 2 | [[Mia Lowe|Mia]] | New connection | this week |\n",
        encoding="utf-8")
    from adapters import crm_today
    rows = crm_today.read_today(crm)
    assert [r["person"] for r in rows] == ["Dan Pike", "Mia Lowe"]
    assert crm_today.person_note(crm, "Dan Pike") == "People/Dan Pike.md"


def test_summary_note_links_open_the_crm_vault(store, tmp_path):
    from engine import mirror
    pid = store.add_project("P", "sales", actor=HUMAN)
    store.add_task(pid, "Call Dan", status="awaiting_you", actor=HUMAN,
                   assignee_agent=HUMAN, crm_person="People/Dan Pike.md")
    note = tmp_path / "v" / "Board.md"
    note.parent.mkdir()   # the board never makes a folder in your vault
    mirror.write_mirrors(store, {"summary_note": str(note),
                                 "crm_vault": "D:/CRM", "departments": []})
    text = note.read_text(encoding="utf-8")
    assert "[[People/Dan Pike.md]]" not in text
    assert "obsidian://open?path=" in text and "Dan%20Pike" in text


def test_limit_file_applies_to_the_name_in_the_file_name(tmp_path):
    from engine import cards
    d = tmp_path / "cards"
    d.mkdir()
    ex = json.loads((REPO / "cards" / "example-agent.json.example")
                    .read_text(encoding="utf-8"))
    (d / "writer-bot.json").write_text(json.dumps(ex), encoding="utf-8")
    loaded = cards.load_cards(tmp_path)
    assert "writer-bot" in loaded and "example-agent" not in loaded
    assert any("slug" in p for p in
               cards.validate_all(tmp_path).get("writer-bot", []))


def test_hygiene_prints_the_cards(cfg_env, capsys):
    import forge
    from engine.config import load_config
    from engine.db import open_store
    st = open_store(load_config())
    new_card(st, owner="")  # Ready with no owner -> an alert
    st.close()
    assert forge.main(["hygiene"]) == 0
    out = capsys.readouterr().out
    assert "Draft 3 posts" in out and "no agent" in out


def test_board_runs_the_health_check_itself(store):
    # Cause: the check only ran under `daemon`, so `serve` always showed
    # "All clear" and "working..." never expired.
    from engine import server
    pid = store.add_project("P", "content", actor=HUMAN)
    store.add_task(pid, "no owner", status="ready", actor=HUMAN)
    info = server.health_check_once(store, CFG, REPO)
    assert info["open_alerts"] == 1 and store.open_alerts()
    assert server.LAST_CHECK["ts"]


def test_installer_writes_the_settings_members_edit(tmp_path, monkeypatch):
    from test_install import fake_setup, run
    home, ch, sb, crm, cfgp = fake_setup(tmp_path, monkeypatch)
    assert run(sb, crm) == 0
    cfg = json.loads(cfgp.read_text(encoding="utf-8"))
    for key in ("intake", "schedule", "hygiene", "crm_today",
                "federate_sources", "departments"):
        assert key in cfg, key
    assert "routing" in cfg["intake"] and "model" in cfg["schedule"]


def test_mac_schedule_can_find_claude(monkeypatch, cfg_env):
    from tools import schedule
    monkeypatch.setattr(schedule.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(schedule.shutil, "which",
                        lambda n: "/opt/homebrew/bin/claude")
    body = list(schedule.plan(60)["files"].values())[0]
    assert "<key>EnvironmentVariables</key>" in body
    assert "/opt/homebrew/bin" in body


# Cause (found on the live unattended test, 2026-09-22): run_if_ready read
# Claude's output in the Windows code page, so the first non-English
# character crashed the reader and the log line came out empty.
def test_unattended_run_reads_output_as_utf8(cfg_env, monkeypatch):
    import subprocess
    from engine.config import load_config
    from engine.db import open_store
    from tools import run_if_ready
    st = open_store(load_config())
    new_card(st)
    st.close()
    seen = {}

    class R:
        returncode = 0
        stdout = "done → Review"

    def fake_run(cmd, **k):
        seen.update(k)
        return R()
    monkeypatch.setattr(run_if_ready.shutil, "which", lambda n: "claude")
    monkeypatch.setattr(subprocess, "run", fake_run)
    assert run_if_ready.main([]) == 0
    assert seen.get("encoding") == "utf-8"
    assert seen.get("errors") == "replace"


def test_unattended_run_also_allows_powershell_form(cfg_env):
    from engine.config import load_config
    from tools import run_if_ready
    rules = run_if_ready.allowed_tools(load_config())
    assert any(r.startswith("PowerShell(") and r.endswith("waiting)")
               for r in rules)
    assert not any(r in ("PowerShell", "Bash") for r in rules)
