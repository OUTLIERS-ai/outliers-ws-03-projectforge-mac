"""Mac only (fault row 8, build plan V3): the Mac twin of the Windows-only start-up file check.

tests/test_logon_and_stop.py::test_the_logon_file_is_written_when_you_ask checks the Windows
.vbs file for `pythonw`, the Windows Python that opens no window. A Mac has no pythonw, so on a
Mac that check failed although nothing was wrong; it now runs on Windows only. This is the same
check for the file a Mac gets: a LaunchAgent that starts the board with this computer's own
Python, and that macOS's own checker (plutil) accepts.

Run by name only:  python3 -m pytest -q tests/mac/mac_logon_plist.py
"""
import plistlib
import subprocess
import sys

import pytest

import install
from test_install import fake_setup, run

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Mac start-up file (LaunchAgent)")


def test_the_mac_start_up_file_is_written_when_you_ask(tmp_path, monkeypatch):
    home, ch, sb, crm, cfgp = fake_setup(tmp_path, monkeypatch)
    assert run(sb, crm, ["--start-with-computer"]) == 0
    plist = home / "Library" / "LaunchAgents" / install.PLIST_NAME
    assert plist.is_file()
    lint = subprocess.run(["plutil", "-lint", str(plist)], capture_output=True, text=True)
    assert lint.returncode == 0, lint.stdout + lint.stderr
    job = plistlib.loads(plist.read_bytes())
    assert job["RunAtLoad"] is True
    assert job["ProgramArguments"][0] == sys.executable
    assert job["ProgramArguments"][1].endswith("forge.py") and job["ProgramArguments"][2] == "serve"
    assert install.main(["--uninstall", "--yes"]) == 0
    assert not plist.exists()
