"""Starting the board when you log in, and stopping a board you cannot see.

Written 2026-09-23. Pieces 1, 2 and 4 all offer to start themselves when you
log in; the board did not, so a member who closed the window or restarted the
computer lost the board with nothing in the guide to tell them what to do.

2 rules these checks hold to:

  - The logon file is never written unless the member asks for it. `--yes`
    answers every other question with its default and leaves this one off, so
    a script can never quietly put a file in someone's Startup folder.
  - `serve --stop` never ends a program it has not first heard call itself the
    board. It asks the address for `/api/meta` and compares the process number
    there with the one on record. A stale record must leave the other program
    alone; the check below proves that by starting a plain web server on the
    recorded address and showing it is still alive afterwards.

Nothing here touches the real Startup folder: APPDATA and the home folder are
pointed at a throwaway folder first. No check uses ports 3001, 3010, 3020 or
4040, the 4 ports this set of downloads uses.
"""
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

import install
from conftest import REPO
from test_install import fake_setup, run

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
REAL_PORTS = {3001, 3010, 3020, 4040}


def free_port():
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    finally:
        s.close()
    assert port not in REAL_PORTS
    return port


def fake_startup(tmp_path, monkeypatch):
    """A Startup folder of our own, so no check can reach the real one."""
    appdata = tmp_path / "appdata"
    startup = appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup"
    startup.mkdir(parents=True)
    monkeypatch.setenv("APPDATA", str(appdata))
    return startup


def wait_for(fn, seconds=20):
    end = time.time() + seconds
    while time.time() < end:
        got = fn()
        if got:
            return got
        time.sleep(0.2)
    return None


def meta_of(port):
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/meta" % port, timeout=1) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:  # noqa: BLE001 - not up yet is the usual answer
        return None


# ---------------------------------------------------------- the logon file

def test_the_logon_file_is_not_written_unless_you_ask(tmp_path, monkeypatch):
    home, ch, sb, crm, cfgp = fake_setup(tmp_path, monkeypatch)
    startup = fake_startup(tmp_path, monkeypatch)
    assert run(sb, crm) == 0
    assert list(startup.iterdir()) == []


@pytest.mark.skipif(sys.platform != "win32", reason="pythonw and the .vbs file exist on Windows only; "
                    "tests/mac/mac_logon_plist.py checks the Mac start-up file")
def test_the_logon_file_is_written_when_you_ask(tmp_path, monkeypatch):
    home, ch, sb, crm, cfgp = fake_setup(tmp_path, monkeypatch)
    startup = fake_startup(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "on_windows", lambda: True)
    monkeypatch.setattr(install, "on_mac", lambda: False)
    assert run(sb, crm, ["--logon"]) == 0
    made = list(startup.glob("*.vbs"))
    assert len(made) == 1
    text = made[0].read_text(encoding="utf-8")
    # 0 = no window at all, False = do not wait for it
    assert ", 0, False" in text
    assert "pythonw" in text.lower()
    assert "forge.py" in text and "serve" in text


def test_running_the_installer_twice_leaves_one_logon_file(tmp_path,
                                                           monkeypatch):
    home, ch, sb, crm, cfgp = fake_setup(tmp_path, monkeypatch)
    startup = fake_startup(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "on_windows", lambda: True)
    monkeypatch.setattr(install, "on_mac", lambda: False)
    assert run(sb, crm, ["--logon"]) == 0
    assert run(sb, crm, ["--logon"]) == 0
    assert len(list(startup.glob("*.vbs"))) == 1


def test_uninstall_takes_the_logon_file_away(tmp_path, monkeypatch):
    home, ch, sb, crm, cfgp = fake_setup(tmp_path, monkeypatch)
    startup = fake_startup(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "on_windows", lambda: True)
    monkeypatch.setattr(install, "on_mac", lambda: False)
    assert run(sb, crm, ["--logon"]) == 0
    assert list(startup.glob("*.vbs"))
    assert install.main(["--uninstall", "--yes"]) == 0
    assert list(startup.glob("*.vbs")) == []


def test_a_logon_file_somebody_else_wrote_is_left_alone(tmp_path, monkeypatch,
                                                        capsys):
    """And it is named on screen, both ways round, rather than passed over in
    silence: the guide promises that."""
    home, ch, sb, crm, cfgp = fake_setup(tmp_path, monkeypatch)
    startup = fake_startup(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "on_windows", lambda: True)
    monkeypatch.setattr(install, "on_mac", lambda: False)
    mine = startup / install.LOGON_NAME
    mine.write_text("' my own launcher\n", encoding="utf-8")
    assert run(sb, crm, ["--logon"]) == 0
    assert mine.read_text(encoding="utf-8") == "' my own launcher\n"
    assert "left alone" in capsys.readouterr().out.lower()
    assert install.main(["--uninstall", "--yes"]) == 0
    assert mine.is_file()
    assert "left alone" in capsys.readouterr().out.lower()


def test_on_a_mac_a_launchd_file_is_written(tmp_path, monkeypatch):
    home, ch, sb, crm, cfgp = fake_setup(tmp_path, monkeypatch)
    fake_startup(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "on_windows", lambda: False)
    monkeypatch.setattr(install, "on_mac", lambda: True)
    assert run(sb, crm, ["--logon"]) == 0
    plist = home / "Library" / "LaunchAgents" / install.PLIST_NAME
    assert plist.is_file()
    body = plist.read_text(encoding="utf-8")
    assert "RunAtLoad" in body and "forge.py" in body and "serve" in body
    assert install.main(["--uninstall", "--yes"]) == 0
    assert not plist.exists()


# ---------------------------------------------------------- /api/meta

def test_the_board_says_what_it_is_and_which_process_it_is(store):
    from engine.server import make_server
    cfg = {"human": "you", "departments": [], "summary_note": ""}
    httpd = make_server(store, cfg, REPO, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    try:
        m = meta_of(port)
        assert m["app"] == "projectforge"
        assert m["pid"] == os.getpid()
        assert m["port"] == port
    finally:
        httpd.shutdown()
        httpd.server_close()


# ---------------------------------------------------------- serve --stop

def test_stop_with_no_record_says_so_and_changes_nothing(tmp_path,
                                                         monkeypatch,
                                                         capsys):
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"summary_note": "", "port": free_port()}),
                    encoding="utf-8")
    monkeypatch.setenv("FORGE_CONFIG", str(cfgp))
    from engine.config import load_config
    from engine import server
    assert server.stop(load_config()) == 0
    assert "not running" in capsys.readouterr().out.lower()


def test_stop_leaves_another_program_on_the_port_alone(tmp_path, monkeypatch,
                                                       capsys):
    """A record left behind by an old board must never end whatever is
    answering on that port now. This starts a plain web server that is not the
    board, points the record at it, and shows it is still alive afterwards."""
    port = free_port()
    # A plain web server that is not the board. Not `python -m http.server`: that one
    # looks up the name of 127.0.0.1 before it answers, which took 35 seconds on
    # GitHub's test Macs (2026-09-24), longer than this check waits.
    plain = ("import http.server, socketserver; "
             "socketserver.TCPServer(('127.0.0.1', %d), "
             "http.server.SimpleHTTPRequestHandler).serve_forever()" % port)
    other = subprocess.Popen(
        [sys.executable, "-c", plain],
        cwd=str(tmp_path), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=NO_WINDOW)
    try:
        assert wait_for(lambda: _answers(port)), "the other program never started"
        cfgp = tmp_path / "config.json"
        cfgp.write_text(json.dumps({"summary_note": "", "port": port,
                                    "db_path": "data/forge.db"}),
                        encoding="utf-8")
        monkeypatch.setenv("FORGE_CONFIG", str(cfgp))
        from engine.config import load_config
        from engine import server
        cfg = load_config()
        rec = server.record_path(cfg)
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(json.dumps({"pid": other.pid, "port": port}),
                       encoding="utf-8")
        assert server.stop(cfg) == 0
        assert other.poll() is None, "stop ended a program that is not the board"
        out = capsys.readouterr().out.lower()
        assert "nothing was stopped" in out
    finally:
        other.kill()
        other.wait(timeout=10)


def _answers(port):
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/" % port,
                                    timeout=1) as r:
            r.read(1)
        return True
    except Exception:  # noqa: BLE001
        return False


def test_the_board_records_itself_and_stop_ends_it(tmp_path, monkeypatch):
    """The whole round trip a member does: start the board, then stop it from
    a second terminal without knowing which window it is in."""
    port = free_port()
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"summary_note": "", "port": port,
                                "db_path": "data/forge.db",
                                "hygiene": {"check_every_min": 60}}),
                    encoding="utf-8")
    env = dict(os.environ, FORGE_CONFIG=str(cfgp))
    board = subprocess.Popen([sys.executable, str(REPO / "forge.py"), "serve",
                              "--port", str(port)], cwd=str(REPO), env=env,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,
                             creationflags=NO_WINDOW)
    try:
        m = wait_for(lambda: meta_of(port))
        assert m and m["app"] == "projectforge"
        # The record names the process that answers. Not board.pid: inside a
        # virtual environment on Windows, python.exe is a small launcher that
        # starts the real Python as a second process, so the 2 numbers differ.
        rec = json.loads((tmp_path / "data" / "forge.pid").read_text(
            encoding="utf-8"))
        assert rec == {"pid": m["pid"], "port": port}
        out = subprocess.run([sys.executable, str(REPO / "forge.py"), "serve",
                              "--stop"], cwd=str(REPO), env=env,
                             capture_output=True, text=True, timeout=60,
                             creationflags=NO_WINDOW)
        assert out.returncode == 0, out.stdout + out.stderr
        assert "stopped" in out.stdout.lower()
        assert board.wait(timeout=20) is not None
        assert not (tmp_path / "data" / "forge.pid").exists()
    finally:
        if board.poll() is None:
            board.kill()
            board.wait(timeout=10)


def test_stop_after_the_board_has_gone_clears_the_record(tmp_path, monkeypatch,
                                                         capsys):
    """A board ended with Ctrl+C, or a computer switched off, leaves a record
    naming a process number the machine has since given to something else."""
    port = free_port()
    cfgp = tmp_path / "config.json"
    cfgp.write_text(json.dumps({"summary_note": "", "port": port,
                                "db_path": "data/forge.db"}),
                    encoding="utf-8")
    monkeypatch.setenv("FORGE_CONFIG", str(cfgp))
    from engine.config import load_config
    from engine import server
    cfg = load_config()
    rec = server.record_path(cfg)
    rec.parent.mkdir(parents=True, exist_ok=True)
    rec.write_text(json.dumps({"pid": 999999, "port": port}), encoding="utf-8")
    assert server.stop(cfg) == 0
    assert "nothing was stopped" in capsys.readouterr().out.lower()
    assert not rec.exists()
