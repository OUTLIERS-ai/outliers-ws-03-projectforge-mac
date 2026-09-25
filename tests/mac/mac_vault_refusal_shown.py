"""Mac only (fault row 20, build plan V3): when macOS refuses the vault, the board says so.

A program that starts by itself on a Mac (the board's start-up file) may be refused the
Documents folder with "Operation not permitted". Python then sees the folder as missing, so the
summary job said the folder "is not there" and pointed at a vault that was where it should be.
It must say "macOS refused access to <folder>", and never that the vault is missing.

GitHub's test Macs cannot show a real refusal (System Integrity Protection is off there, wave 0a
M5), so the first check makes reading the vault raise the error macOS gives, and the second
uses a folder this user may not read, which the test Macs do refuse.

Run by name only:  python3 -m pytest -q tests/mac/mac_vault_refusal_shown.py
"""
import errno
import os
import sys
from pathlib import Path

import pytest

from conftest import REPO

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS folder refusal")


def assert_refusal_said_plainly(message, folder):
    assert "macOS refused access to" in message, message
    assert str(folder) in message, message
    assert "is not there" not in message and "missing" not in message.lower(), message
    assert "python3" in message


def test_operation_not_permitted_is_reported_as_a_refusal(store, tmp_path, monkeypatch):
    from engine import hygiene, mirror

    vault = tmp_path / "Documents" / "Second Brain"
    vault.mkdir(parents=True)
    config = {"summary_note": str(vault / "ProjectForge Board.md")}
    real_listdir, real_is_dir = os.listdir, Path.is_dir

    def refused_listdir(p="."):
        if Path(p) == vault:
            raise PermissionError(errno.EPERM, "Operation not permitted", str(p))
        return real_listdir(p)

    def refused_is_dir(self, **kw):
        # What Python answers for a folder macOS refuses: not a folder.
        return False if self == vault else real_is_dir(self, **kw)

    monkeypatch.setattr(os, "listdir", refused_listdir)
    monkeypatch.setattr(Path, "is_dir", refused_is_dir)
    with pytest.raises(mirror.VaultMissing) as e:
        mirror.write_mirrors(store, config, REPO)
    assert_refusal_said_plainly(str(e.value), vault)
    # the health check turns it into the alert the board shows
    hygiene.run(store, dict(config, human="you", orchestrator="orchestrator", managers=["content-lead"],
                            departments=[{"id": "content", "name": "Content", "lead": ""}]), REPO)
    notes = [a for a in store.open_alerts() if a["kind"] == "summary-note"]
    assert notes, "the health check raised no alert about the summary note"
    assert_refusal_said_plainly(notes[0]["message"], vault)


def test_a_folder_this_user_may_not_open_is_reported_as_a_refusal(store, tmp_path):
    from engine import mirror

    vault = tmp_path / "Second Brain"
    vault.mkdir()
    vault.chmod(0)
    try:
        with pytest.raises(OSError) as e:
            mirror.write_mirrors(store, {"summary_note": str(vault / "ProjectForge Board.md")}, REPO)
    finally:
        vault.chmod(0o755)
    assert_refusal_said_plainly(str(e.value), vault)
