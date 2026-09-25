"""The second fix round, 2026-09-22. One test per fault found in testing.

Every test here failed before the fix beside it and passes after it. Each
block names the cause. Nothing here touches a real home folder, a real vault
or a real port: servers are started on port 0, which asks the computer for
any free port, so these tests pass on a machine where the board is already
running on 3020.
"""
import json
import re
from pathlib import Path

import pytest

from conftest import HUMAN, MANAGER, ORCH, REPO, WORKER, new_card  # noqa: F401
from engine.rules import NotAllowed  # noqa: F401

CFG = {"human": HUMAN, "orchestrator": ORCH, "managers": [MANAGER],
       "departments": [{"id": "content", "name": "Content", "lead": ""}],
       "summary_note": ""}


# ---- 1. a due date typed in words stopped the health check for good -------
# Cause: update_task and upsert_task listed `due` among the fields they set
# and never checked its shape; hygiene.py then parsed it with nothing round
# the parse, so one bad card ended every later check.

def test_a_due_date_in_words_is_refused_where_it_is_typed(store):
    tid = new_card(store)
    with pytest.raises(ValueError) as e:
        store.update_task(tid, {"due": "tomorrow"}, actor=HUMAN)
    assert "YYYY-MM-DD" in str(e.value)
    assert store.task_detail(tid)["task"]["due"] == ""


def test_a_due_date_that_is_not_a_real_date_is_refused(store):
    tid = new_card(store)
    for bad in ("2026-13-02", "02/10/2026", "next week", "2026-10-32"):
        with pytest.raises(ValueError):
            store.update_task(tid, {"due": bad}, actor=HUMAN)
    store.update_task(tid, {"due": "2026-10-02"}, actor=HUMAN)
    assert store.task_detail(tid)["task"]["due"] == "2026-10-02"


def test_a_program_pushing_a_bad_due_date_in_is_refused(store):
    pid = store.add_project("Spring launch", "content", actor=HUMAN)
    with pytest.raises(ValueError):
        store.upsert_task("crm-today", "x1", pid, "Ring Dan",
                          due="tomorrow", actor="crm-today")


def test_the_health_check_survives_a_bad_due_date_already_saved(store):
    """A board that already has 'tomorrow' in it must still be checkable."""
    from engine import hygiene
    tid = new_card(store)
    store.conn.execute("UPDATE tasks SET due='tomorrow' WHERE id=?", (tid,))
    store.conn.commit()
    r = hygiene.run(store, CFG, REPO)          # must not raise
    assert r["checked"] >= 1
    kinds = {a["kind"] for a in store.open_alerts()}
    assert "bad-due" in kinds
    msg = [a["message"] for a in store.open_alerts()
           if a["kind"] == "bad-due"][0]
    assert "tomorrow" in msg and "YYYY-MM-DD" in msg


def test_a_health_check_that_fails_shows_on_the_board(store, monkeypatch):
    """Cause: the failure was printed in the terminal the member had
    minimised, and nothing on the board said the check had stopped."""
    from engine import server

    def boom(*a, **k):
        raise RuntimeError("something in the data is unreadable")
    monkeypatch.setattr(server, "health_check_once", boom)
    server.health_check_guarded(store, CFG, REPO)
    alerts = [a for a in store.open_alerts() if a["kind"] == "health-check"]
    assert len(alerts) == 1
    assert "health check" in alerts[0]["message"].lower()
    assert alerts[0]["level"] == "alert"
    # it does not pile up: a second failure updates the same alert
    server.health_check_guarded(store, CFG, REPO)
    assert len([a for a in store.open_alerts()
                if a["kind"] == "health-check"]) == 1
    assert server.LAST_CHECK["error"]


def test_the_health_check_alert_clears_itself_when_the_check_works(store):
    from engine import server
    store.raise_alert("health-check", "board", "it failed earlier", "alert")
    server.health_check_guarded(store, CFG, REPO)
    assert not [a for a in store.open_alerts()
                if a["kind"] == "health-check"]
    assert server.LAST_CHECK["error"] == ""


# ---- 2. a Today.md that is not plain UTF-8 crashed the CRM import ---------
# Cause: adapters/crm_today.py was the only read in the download without
# errors="replace".

def test_a_today_page_in_the_old_windows_format_is_still_read(tmp_path):
    from adapters import crm_today
    vault = tmp_path / "crm"
    vault.mkdir()
    (vault / "Today.md").write_bytes(
        "| 1 | [[Renée Café]] | Asked about payroll | Today |\n"
        .encode("cp1252"))
    rows = crm_today.read_today(vault)
    assert len(rows) == 1
    assert rows[0]["rank"] == 1
    assert rows[0]["reason"] == "Asked about payroll"


# ---- 3. a hand-edited config.json gave a wall of error text ---------------
# Cause: engine/config.py read the file with plain utf-8 and parsed it with
# nothing round the parse, so Notepad's byte-order mark or a trailing comma
# ended every command in a stack trace.

BAD_COMMA = '{\n  "human": "sam",\n  "port": 3020,\n}\n'


def test_a_trailing_comma_in_the_config_gives_one_plain_sentence(tmp_path):
    from engine.config import ConfigError, load_config
    p = tmp_path / "config.json"
    p.write_text(BAD_COMMA, encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    msg = str(e.value)
    assert "line 3" in msg
    assert "config.json" in msg
    assert "Traceback" not in msg and "json.decoder" not in msg


def test_a_config_saved_by_notepad_as_utf8_with_bom_still_works(tmp_path):
    from engine.config import load_config
    p = tmp_path / "config.json"
    p.write_bytes(b"\xef\xbb\xbf" + json.dumps({"human": "sam"}).encode())
    assert load_config(p)["human"] == "sam"


def test_every_command_prints_that_sentence_instead_of_a_stack_trace(
        tmp_path, monkeypatch, capsys):
    import forge
    p = tmp_path / "config.json"
    p.write_text(BAD_COMMA, encoding="utf-8")
    monkeypatch.setenv("FORGE_CONFIG", str(p))
    assert forge.main(["list"]) == 1
    out = capsys.readouterr().out
    assert "line 3" in out and "Traceback" not in out


# ---- 4. uninstall crashed half way ---------------------------------------
# Cause: install.py renamed a whole folder with os.replace, which fails on
# Windows when a file inside is open; and the stamp counted whole seconds, so
# 2 uninstalls in the same second collided. The command file had already been
# removed by then, leaving the member half-uninstalled.

def _installed(tmp_path, monkeypatch):
    """A complete install inside a throwaway home folder."""
    import install
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True, exist_ok=True)
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("FORGE_CONFIG", str(tmp_path / "config.json"))
    assert install.main(["--yes", "--second-brain", str(vault),
                         "--crm", "none", "--agents-dir",
                         str(home / ".claude" / "agents"),
                         "--summary-note", "none"]) == 0
    return install, home


def test_uninstall_leaves_everything_in_place_when_a_file_is_open(
        tmp_path, monkeypatch, capsys):
    install, home = _installed(tmp_path, monkeypatch)
    cmd = home / ".claude" / "commands" / "forge-run.md"
    assert cmd.is_file()
    real = install.os.replace

    def refuse(src, dst):
        if Path(src).is_dir():
            raise PermissionError(5, "Access is denied")
        return real(src, dst)
    monkeypatch.setattr(install.os, "replace", refuse)
    assert install.main(["--uninstall", "--yes"]) == 1
    out = capsys.readouterr().out
    assert "Traceback" not in out
    assert "Nothing was changed" in out
    assert "close" in out.lower()
    # the half-uninstall: /forge-run must still be there
    assert cmd.is_file()
    # and it is repeatable: with the file closed again, it works
    monkeypatch.setattr(install.os, "replace", real)
    assert install.main(["--uninstall", "--yes"]) == 0
    assert not cmd.is_file()


def test_two_uninstalls_inside_the_same_second_do_not_collide(
        tmp_path, monkeypatch):
    install, home = _installed(tmp_path, monkeypatch)
    adir = home / ".claude" / "projectforge"
    assert install.main(["--uninstall", "--yes"]) == 0
    _installed(tmp_path, monkeypatch)
    assert adir.is_dir()
    assert install.main(["--uninstall", "--yes"]) == 0
    aside = sorted(adir.parent.glob("projectforge.removed-*"))
    assert len(aside) == 2, aside


# ---- 5. the summary note was written into a folder the board invented -----
# Cause: atomic_write makes every parent folder, so a vault that was renamed,
# or a OneDrive vault that had not synced, got a brand new empty folder with
# one note in it, and `mirror` reported success.

def test_the_summary_note_is_refused_when_its_folder_is_not_there(
        store, tmp_path, capsys):
    from engine import mirror
    gone = tmp_path / "vault-that-is-not-there"
    cfg = dict(CFG, summary_note=str(gone / "ProjectForge Board.md"))
    with pytest.raises(mirror.VaultMissing) as e:
        mirror.write_mirrors(store, cfg, REPO)
    assert str(gone) in str(e.value)
    assert not gone.exists()


def test_forge_mirror_says_so_and_stops(tmp_path, monkeypatch, capsys):
    import forge
    gone = tmp_path / "vault-that-is-not-there"
    cfg = {"human": HUMAN, "db_path": str(tmp_path / "forge.db"),
           "summary_note": str(gone / "ProjectForge Board.md")}
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setenv("FORGE_CONFIG", str(p))
    assert forge.main(["mirror"]) == 2
    out = capsys.readouterr().out
    assert "not written" in out and str(gone) in out
    assert not gone.exists()


def test_the_health_check_raises_an_alert_about_the_missing_folder(
        store, tmp_path):
    from engine import hygiene
    gone = tmp_path / "vault-that-is-not-there"
    cfg = dict(CFG, summary_note=str(gone / "ProjectForge Board.md"))
    new_card(store)
    hygiene.run(store, cfg, REPO)               # must not raise
    kinds = {a["kind"] for a in store.open_alerts()}
    assert "summary-note" in kinds
    assert not gone.exists()


# ---- 6. a member with no Obsidian vault could not install -----------------
# Cause: question 1 had no default and no 'none' answer, unlike question 2,
# so pressing Enter ended the installer.

def test_a_member_with_no_vault_can_install(tmp_path, monkeypatch, capsys):
    import install
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    monkeypatch.setenv("FORGE_CONFIG", str(tmp_path / "config.json"))
    assert install.main(["--yes", "--second-brain", "none", "--crm", "none",
                         "--agents-dir", str(home / ".claude" / "agents")]) == 0
    cfg = json.loads((tmp_path / "config.json").read_text(encoding="utf-8"))
    assert cfg["second_brain"] == ""
    assert cfg["summary_note"] == ""
    assert (home / ".claude" / "commands" / "forge-run.md").is_file()


def test_question_1_offers_the_same_answer_as_question_2():
    src = (REPO / "install.py").read_text(encoding="utf-8")
    one = src.split("\\n1. Your second brain vault")[1].split("ask.ask")[0]
    assert "none" in one.lower()


def test_the_installer_refuses_python_older_than_3_11():
    import install
    assert install.MIN_PY == (3, 11)


# ---- 7. an agent asking for a person left no mark ------------------------
# Cause: escalate wrote an event and nothing else. The one change that needs
# a human was the only one with no sign on the board.

def test_an_escalation_marks_the_card_and_puts_it_in_your_column(store):
    tid = new_card(store, status="in_progress")
    store.escalate(tid, "The client has not replied in 6 days. Please ring "
                        "Oakfield.", actor=WORKER)
    t = store.task_detail(tid)["task"]
    assert t["escalated"] == 1
    assert t["status"] == "awaiting_you"
    kinds = [e["action"] for e in store.recent_events()]
    assert "escalation" in kinds and "moved" in kinds


def test_an_escalation_raises_an_alert_that_outlives_the_activity_list(store):
    from engine import hygiene
    tid = new_card(store, status="in_progress")
    store.escalate(tid, "Please ring Oakfield.", actor=WORKER)
    hygiene.run(store, CFG, REPO)
    a = [x for x in store.open_alerts() if x["kind"] == "needs you"]
    assert len(a) == 1 and a[0]["task_id"] == tid


def test_the_mark_clears_when_you_move_the_card_on(store):
    tid = new_card(store, status="in_progress")
    store.escalate(tid, "Please ring Oakfield.", actor=WORKER)
    store.move_task(tid, "ready", actor=HUMAN)
    assert store.task_detail(tid)["task"]["escalated"] == 0


def test_a_card_already_in_your_column_is_not_moved_again(store):
    tid = new_card(store, status="awaiting_you")
    store.escalate(tid, "Please ring Oakfield.", actor=WORKER)
    t = store.task_detail(tid)["task"]
    assert t["status"] == "awaiting_you" and t["escalated"] == 1


# ---- 8. the activity list was a wall of pronouns -------------------------
# Cause: the feed said "created this" with no noun, because the rows the
# viewer was given carried only an id.

def test_every_activity_row_names_what_it_happened_to(store):
    tid = new_card(store)
    store.comment(tid, "ring them back", actor=HUMAN)
    rows = store.recent_events()
    for r in rows:
        if r["entity_type"] == "task" and r["entity_id"] == tid:
            assert r["entity_title"] == "Draft 3 posts"
            assert r["parent_title"] == "Spring launch"
    projects = [r for r in rows if r["entity_type"] == "project"]
    assert projects and projects[0]["entity_title"] == "Spring launch"


# ---- 9. the refusal on the board was written for the agent ---------------
# Cause: one message did 2 jobs. The board showed the agent's command-line
# flags to a member who will never type them.

def test_the_board_copy_of_a_refusal_has_no_command_line_flags(store):
    tid = new_card(store)
    with pytest.raises(ValueError) as e:
        store.handoff(tid, WORKER, "editor-bot", done="x")
    agent_message = str(e.value)
    assert "--done" in agent_message          # the agent still gets the flags
    board = [ev for ev in store.recent_events()
             if ev["action"] == "refused"][0]
    shown = json.loads(board["detail"])["message"]
    assert "--" not in shown, shown
    assert WORKER in shown and "handover" in shown


# ---- 10. small ones ------------------------------------------------------

VIEWER = (REPO / "viewer")


def test_the_activity_list_cannot_stretch_the_page():
    """Cause: the panel had no height of its own, so 40 events made the page
    3,634 px tall and pushed every '+ Add card' button below the fold."""
    css = (VIEWER / "style.css").read_text(encoding="utf-8")
    block = css.split("#activity {")[1].split("}")[0]
    assert "overflow-y: auto" in block
    assert "max-height" in block


def test_add_card_sits_at_the_top_of_a_column():
    js = (VIEWER / "app.js").read_text(encoding="utf-8")
    board = js.split("function renderBoard(")[1].split("function cardEl(")[0]
    assert board.index('"+ Add card"') < board.index("for (const t of tasks)")


def test_the_board_says_so_when_a_search_finds_nothing():
    js = (VIEWER / "app.js").read_text(encoding="utf-8")
    assert "No card matches" in js


def test_the_board_says_when_it_has_paused_instead_of_going_quiet():
    js = (VIEWER / "app.js").read_text(encoding="utf-8")
    assert "aused while you are typing" in js
    # the open card window no longer stops the board refreshing
    busy = js.split("function typing(")[1].split("}\n")[0]
    assert "modal-backdrop" not in busy


def test_every_column_can_be_reached():
    js = (VIEWER / "app.js").read_text(encoding="utf-8")
    html = (VIEWER / "index.html").read_text(encoding="utf-8")
    assert "col-jump" in html and "col-jump" in js


def test_an_alert_that_names_a_card_links_to_it():
    js = (VIEWER / "app.js").read_text(encoding="utf-8")
    block = js.split("async function renderAlerts(")[1].split("\nfunction ")[0]
    assert "al-open" in block          # a visible link, not a hidden click
    assert "Hide for now" in block


def test_the_readme_says_taken_not_claimed():
    assert "claimed" not in (REPO / "README.md").read_text(encoding="utf-8")


FIXED_PORT = re.compile(r"port\s*=\s*(\d{4,5})|127\.0\.0\.1[\"']?\s*,\s*(\d{4,5})")


def test_the_tests_never_need_a_free_port_of_their_own():
    """Cause (seen in the Jeeves download): a test that assumes port 3020 is
    free fails on any machine where the board is already running. Every test
    here asks for port 0, which means any free port."""
    for f in sorted((REPO / "tests").glob("test_*.py")):
        src = f.read_text(encoding="utf-8")
        for m in FIXED_PORT.finditer(src):
            got = m.group(1) or m.group(2)
            assert got == "0", f"{f.name} asks for port {got}"
