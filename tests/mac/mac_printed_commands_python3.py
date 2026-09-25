"""Mac only (fault row 18, build plan V3): what ProjectForge prints must never tell a Mac member to type `python`.

A Mac has `python3` and no plain `python`. On GitHub's test Macs (wave 0a M7, 2026-09-24) the
installer, the board and the command line printed 10 lines such as "1. Open the board:
python forge.py serve", each of which fails there with "command not found". Windows keeps `python`.

Run by name only:  python3 -m pytest -q tests/mac/mac_printed_commands_python3.py
"""
import json
import re
import subprocess
import sys

import pytest

from conftest import REPO
from test_install import fake_setup, run

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Mac wording")
COMMAND = re.compile(r"(?<![\w/.\-])(python|pip)(?![\w.\-])")


def bad(text):
    return [ln for ln in text.splitlines() if COMMAND.search(ln)]


def forge(args, cfgp):
    import os
    env = dict(os.environ, FORGE_CONFIG=str(cfgp))
    return subprocess.run([sys.executable, str(REPO / "forge.py")] + args, cwd=str(REPO), env=env,
                          capture_output=True, text=True, timeout=120)


def test_the_installer_says_python3(tmp_path, monkeypatch, capsys):
    home, ch, sb, crm, cfgp = fake_setup(tmp_path, monkeypatch)
    assert run(sb, crm) == 0
    out = capsys.readouterr().out
    assert "python3 forge.py serve" in out
    assert bad(out) == [], bad(out)


def test_the_command_line_says_python3(cfg_env):
    out = forge(["list"], cfg_env).stdout + forge(["projects"], cfg_env).stdout
    r = forge(["add-project", "Content week 39", "--dept", "content", "--actor", "you"], cfg_env)
    out += r.stdout + r.stderr
    r = forge(["add-task", "pf-p-nothere", "Card", "--actor", "you"], cfg_env)
    out += r.stdout + r.stderr
    assert "python3 forge.py" in out
    assert bad(out) == [], bad(out)


def test_the_board_says_python3_when_the_port_is_taken(cfg_env):
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    s.listen(1)
    try:
        r = forge(["serve", "--port", str(s.getsockname()[1])], cfg_env)
    finally:
        s.close()
    assert "python3 forge.py serve --port" in r.stdout
    assert bad(r.stdout) == [], r.stdout


def test_a_broken_config_says_python3(tmp_path):
    cfgp = tmp_path / "config.json"
    cfgp.write_text("{ not json", encoding="utf-8")
    r = forge(["list"], cfgp)
    assert "python3 install.py" in r.stdout + r.stderr
    assert bad(r.stdout + r.stderr) == []


def test_the_demo_and_the_summary_note_say_python3(tmp_path, store):
    from engine import mirror
    r = subprocess.run([sys.executable, str(REPO / "tools" / "demo_board.py"), "--out", str(tmp_path / "demo")],
                       cwd=str(REPO), capture_output=True, text=True, timeout=120)
    assert "python3 tools/demo_board.py" in r.stdout
    assert bad(r.stdout) == [], r.stdout
    vault = tmp_path / "vault"
    vault.mkdir()
    note = vault / "ProjectForge Board.md"
    mirror.write_mirrors(store, {"summary_note": str(note)}, REPO)
    assert "`python3 forge.py mirror`" in note.read_text(encoding="utf-8")
    with pytest.raises(mirror.VaultMissing) as e:
        mirror.write_mirrors(store, {"summary_note": str(tmp_path / "gone" / "n.md")}, REPO)
    assert bad(str(e.value)) == [], str(e.value)


def test_a_practice_copy_says_python3(tmp_path, cfg_env):
    r = forge(["make-copy", str(tmp_path / "practice")], cfg_env)
    assert "python3 forge.py serve" in r.stdout, r.stdout + r.stderr
    assert bad(r.stdout) == [], r.stdout
