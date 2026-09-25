"""Working on a copy of the board while the everyday board keeps running.

Written 2026-09-24. The guide tells a member to copy the download folder and
change the copy. Done with an ordinary folder copy, 4 faults followed:

  - the copy carried data/forge.pid, the record of the everyday board's
    process number and port, so `serve --stop` typed in the copy ended the
    everyday board;
  - the copy's config.json had the same port, so the copy could not start
    while the everyday board ran;
  - the copy still pointed at the member's summary note, so a test card in
    the copy was written into the member's vault;
  - running install.py or tools/schedule.py in the copy would point the
    agents' tool, /forge-run and the start-up file at the copy.

These checks hold the fixes: `serve --stop` only ends the board that belongs
to the folder it is typed in, and `forge.py make-copy` makes a copy that
shares nothing live with the everyday board.

No check uses ports 3001, 3010, 3020 or 4040, and the home folder and
APPDATA point at a throwaway folder before anything runs.
"""
import json
import os
import shutil
import sqlite3
import subprocess
import sys

import pytest

from conftest import REPO
from test_logon_and_stop import (NO_WINDOW, REAL_PORTS, free_port, meta_of,
                                 wait_for)


def make_everyday(tmp_path, port):
    """An everyday install: its own folder with config.json and a board."""
    home = tmp_path / "home"
    (home / "appdata").mkdir(parents=True)
    note = tmp_path / "vault" / "ProjectForge Board.md"
    note.parent.mkdir(parents=True)
    cfgp = tmp_path / "everyday" / "config.json"
    cfgp.parent.mkdir(parents=True)
    cfgp.write_text(json.dumps({
        "human": "you", "port": port, "db_path": "data/forge.db",
        "summary_note": str(note),
        "schedule": {"installed": True, "every_min": 60},
        "hygiene": {"check_every_min": 60}}), encoding="utf-8")
    env = dict(os.environ, FORGE_CONFIG=str(cfgp), HOME=str(home),
               USERPROFILE=str(home), APPDATA=str(home / "appdata"),
               CLAUDE_CONFIG_DIR=str(home / ".claude"))
    return cfgp, env, note


def forge(args, env, cwd=REPO, script=None):
    return subprocess.run(
        [sys.executable, str(script or REPO / "forge.py"), *args],
        cwd=str(cwd), env=env, capture_output=True, text=True, timeout=120,
        creationflags=NO_WINDOW)


def start_board(env, port, script=None, cwd=REPO):
    return subprocess.Popen(
        [sys.executable, str(script or REPO / "forge.py"), "serve",
         "--port", str(port)], cwd=str(cwd), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW)


def end(proc):
    if proc.poll() is None:
        proc.kill()
        proc.wait(timeout=10)


# ------------------------------------------- an ordinary copy of the folder

def test_stop_typed_in_a_copied_folder_leaves_the_everyday_board_alone(
        tmp_path):
    """The member copies the folder with Explorer or Copy-Item, record and
    all, and types serve --stop in the copy. The everyday board must live."""
    port = free_port()
    cfgp, env, _ = make_everyday(tmp_path, port)
    board = start_board(env, port)
    try:
        assert wait_for(lambda: meta_of(port)), "the everyday board never started"
        assert wait_for(lambda: (cfgp.parent / "data" / "forge.pid").is_file())
        copy_dir = tmp_path / "copied"
        shutil.copytree(cfgp.parent, copy_dir)
        env2 = dict(env, FORGE_CONFIG=str(copy_dir / "config.json"))
        out = forge(["serve", "--stop"], env2)
        assert out.returncode == 0, out.stdout + out.stderr
        assert meta_of(port), "serve --stop in the copy ended the everyday board"
        assert "nothing was stopped" in out.stdout.lower()
    finally:
        end(board)


# ------------------------------------------------------ forge.py make-copy

def test_make_copy_shares_nothing_live_with_the_everyday_board(tmp_path):
    port = free_port()
    cfgp, env, note = make_everyday(tmp_path, port)
    out = forge(["add-project", "Spring launch", "--dept", "content",
                 "--actor", "you"], env)
    assert out.returncode == 0, out.stdout + out.stderr
    before_note = note.read_text(encoding="utf-8") if note.is_file() else None
    board = start_board(env, port)
    try:
        assert wait_for(lambda: meta_of(port))
        assert wait_for(lambda: (cfgp.parent / "data" / "forge.pid").is_file())
        dest = tmp_path / "practice"
        out = forge(["make-copy", str(dest)], env)
        assert out.returncode == 0, out.stdout + out.stderr

        # the record of the everyday board is not carried into the copy
        assert not (dest / "data" / "forge.pid").exists()
        # its own port, never the everyday one and never 1 of the set's 4
        ccfg = json.loads((dest / "config.json").read_text(encoding="utf-8"))
        assert ccfg["port"] != port and ccfg["port"] not in REAL_PORTS
        # no summary note, no schedule, and it knows it is a copy
        assert ccfg["summary_note"] == ""
        assert ccfg["schedule"]["installed"] is False
        assert ccfg["copy_of"]
        assert ccfg["db_path"] == "data/forge.db"
        # a separate database holding today's cards
        db = dest / "data" / "forge.db"
        n = sqlite3.connect(db).execute(
            "SELECT COUNT(*) FROM projects").fetchone()[0]
        assert n == 1
        # the everyday install is unchanged
        assert json.loads(cfgp.read_text(encoding="utf-8"))["port"] == port
        # the code came along, so the copy runs by itself
        assert (dest / "forge.py").is_file() and (dest / "engine").is_dir()

        # both boards run at once, and the copy's changes stay in the copy
        env2 = dict(env)
        env2.pop("FORGE_CONFIG")
        cport = ccfg["port"]
        copy_board = subprocess.Popen(
            [sys.executable, str(dest / "forge.py"), "serve"], cwd=str(dest),
            env=env2, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=NO_WINDOW)
        try:
            assert wait_for(lambda: meta_of(cport)), "the copy never started"
            out = forge(["add-project", "Only in the copy", "--dept",
                         "content", "--actor", "you"], env2, cwd=dest,
                        script=dest / "forge.py")
            assert out.returncode == 0, out.stdout + out.stderr
            everyday = sqlite3.connect(cfgp.parent / "data" / "forge.db")
            titles = [r[0] for r in everyday.execute(
                "SELECT title FROM projects")]
            everyday.close()
            assert "Only in the copy" not in titles
            after = note.read_text(encoding="utf-8") if note.is_file() else None
            assert after == before_note, "the copy wrote the member's vault note"
            # stop typed in the copy ends the copy, and only the copy
            out = forge(["serve", "--stop"], env2, cwd=dest,
                        script=dest / "forge.py")
            assert "stopped the board" in out.stdout.lower(), out.stdout
            assert wait_for(lambda: copy_board.poll() is not None)
            assert meta_of(port), "stopping the copy ended the everyday board"
        finally:
            end(copy_board)
    finally:
        end(board)


def test_make_copy_refuses_a_folder_that_is_already_there(tmp_path):
    port = free_port()
    cfgp, env, _ = make_everyday(tmp_path, port)
    dest = tmp_path / "practice"
    dest.mkdir()
    (dest / "mine.txt").write_text("keep me", encoding="utf-8")
    out = forge(["make-copy", str(dest)], env)
    assert out.returncode != 0
    assert "already" in out.stdout.lower()
    assert [p.name for p in dest.iterdir()] == ["mine.txt"]


def test_install_and_uninstall_refuse_in_a_practice_copy(tmp_path,
                                                          monkeypatch):
    """They would point the agents' tool, /forge-run and the start-up file
    at the copy, or take the everyday ones away."""
    import install
    home = tmp_path / "home"
    (home / ".claude" / "agents").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("APPDATA", str(home / "appdata"))
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", str(home / ".claude"))
    cfgp = tmp_path / "practice" / "config.json"
    cfgp.parent.mkdir()
    cfgp.write_text(json.dumps({"copy_of": str(tmp_path / "everyday"),
                                "summary_note": ""}), encoding="utf-8")
    monkeypatch.setenv("FORGE_CONFIG", str(cfgp))
    assert install.main(["--yes", "--second-brain", "none", "--crm",
                         "none"]) != 0
    assert install.main(["--uninstall", "--yes"]) != 0
    assert not (home / ".claude" / "projectforge").exists()
    assert not (home / ".claude" / "commands").exists()


def test_the_schedule_refuses_in_a_practice_copy(tmp_path, monkeypatch,
                                                  capsys):
    sys.path.insert(0, str(REPO / "tools"))
    import schedule
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"copy_of": str(tmp_path / "everyday")}),
                    encoding="utf-8")
    monkeypatch.setenv("FORGE_CONFIG", str(cfgp))
    monkeypatch.setattr(schedule.subprocess, "run", lambda *a, **k: (
        pytest.fail("the schedule tried to run " + str(a))))
    assert schedule.main(["--install"]) != 0
    assert schedule.main(["--remove"]) != 0
    assert "copy" in capsys.readouterr().out.lower()


def test_a_second_serve_says_who_has_the_port(tmp_path):
    """The message names who has the port: this folder's own board, a board
    from another folder, or another program. Only the first is stopped by
    serve --stop typed here, so the message must not send a member to it
    for the other 2."""
    port = free_port()
    cfgp, env, _ = make_everyday(tmp_path, port)
    board = start_board(env, port)
    try:
        assert wait_for(lambda: meta_of(port))
        assert wait_for(lambda: (cfgp.parent / "data" / "forge.pid").is_file())
        out = forge(["serve", "--port", str(port)], env)
        assert out.returncode == 6
        assert "already running" in out.stdout
        other = tmp_path / "other" / "config.json"
        other.parent.mkdir()
        other.write_text(json.dumps({"summary_note": "", "port": port}),
                         encoding="utf-8")
        out = forge(["serve"], dict(env, FORGE_CONFIG=str(other)))
        assert out.returncode == 6
        assert "another folder" in out.stdout
        assert "--port %d" % (port + 1) in out.stdout
    finally:
        end(board)
