"""The setting's plain name, the Mac's python3, and a true Python message.

Written 2026-09-24, after the third read of the member guide:

  - The setting that starts the board by itself when the computer starts was
    called `--logon`. Members read "logon" as "needs an account". It is now
    `--start-with-computer`; `--logon` still works for anyone who typed it
    before, but `--help` shows only the new name.
  - A current Mac has `python3` and no `python`. The installer writes commands
    that Claude Code runs for you (the lines it gives you to paste into your
    CLAUDE.md, the /forge-run command file, and the list of commands an
    unattended run may use). On a Mac every one of them said `python`, so every
    one failed with "command not found".
  - The installer's refusal of an old Python now says what python.org says:
    3.10 gets security fixes only until 2026-10-31.

Nothing here touches the real Startup folder or the real home folder.
"""
import pytest

import install
from test_install import fake_setup, run
from test_logon_and_stop import fake_startup


# ------------------------------------------------- --start-with-computer

@pytest.mark.parametrize("flag", ["--start-with-computer", "--logon"])
def test_both_names_write_the_start_up_file(tmp_path, monkeypatch, flag):
    home, ch, sb, crm, cfgp = fake_setup(tmp_path, monkeypatch)
    startup = fake_startup(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "on_windows", lambda: True)
    monkeypatch.setattr(install, "on_mac", lambda: False)
    assert run(sb, crm, [flag]) == 0
    assert len(list(startup.glob("*.vbs"))) == 1


def test_help_shows_only_the_new_name(capsys):
    with pytest.raises(SystemExit):
        install.main(["--help"])
    out = capsys.readouterr().out
    assert "--start-with-computer" in out
    assert "--logon" not in out
    assert "logon" not in out.lower()


# ---------------------------------------------------------- python3 on a Mac

def _as_mac(monkeypatch):
    monkeypatch.setattr(install, "on_windows", lambda: False)
    monkeypatch.setattr(install, "on_mac", lambda: True)


def test_the_claude_md_lines_say_python3_on_a_mac(monkeypatch):
    _as_mac(monkeypatch)
    cfg = {"managers": ["content-lead"]}
    text = install.claude_snippet(cfg, "/Users/sam/.claude/projectforge/forge_agent.py")
    assert 'python3 "/Users/sam/.claude/projectforge/forge_agent.py"' in text
    assert '`python "' not in text


def test_the_claude_md_lines_say_python_on_windows(monkeypatch):
    monkeypatch.setattr(install, "on_windows", lambda: True)
    cfg = {"managers": []}
    text = install.claude_snippet(cfg, "C:/Users/sam/.claude/projectforge/forge_agent.py")
    assert '`python "C:/Users/sam/.claude/projectforge/forge_agent.py"' in text


def test_the_forge_run_command_says_python3_on_a_mac(monkeypatch):
    _as_mac(monkeypatch)
    cfg = {"human": "you", "orchestrator": "orchestrator"}
    text = install.render_command(cfg, "/Users/sam/.claude/projectforge/forge_agent.py")
    assert "python3 \"" in text
    assert "`python \"" not in text
    assert "{{" not in text


def test_an_unattended_run_allows_python3_on_a_mac(cfg_env, monkeypatch):
    from engine.config import load_config
    from tools import run_if_ready
    monkeypatch.setattr(run_if_ready, "on_windows", lambda: False)
    rules = run_if_ready.allowed_tools(load_config())
    board = [r for r in rules if "forge" in r]
    assert board and all("python3 \"" in r for r in board)


def test_an_unattended_run_allows_python_on_windows(cfg_env, monkeypatch):
    from engine.config import load_config
    from tools import run_if_ready
    monkeypatch.setattr(run_if_ready, "on_windows", lambda: True)
    rules = run_if_ready.allowed_tools(load_config())
    board = [r for r in rules if "forge" in r]
    assert board and all("(python \"" in r for r in board)


# ---------------------------------------------------- the Python message

def test_the_python_refusal_tells_the_truth(tmp_path, monkeypatch, capsys):
    fake_setup(tmp_path, monkeypatch)
    monkeypatch.setattr(install, "MIN_PY", (99, 0))
    assert install.main(["--yes"]) == 1
    out = capsys.readouterr().out
    assert "3.10 gets security fixes only until 2026-10-31" in out
    assert "Nothing changed" in out
