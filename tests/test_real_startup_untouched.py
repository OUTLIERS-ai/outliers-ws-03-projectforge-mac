"""Running the checks must never touch the member's own Startup folder.

Written 2026-09-24. The guide tells a member to run `python -m pytest -q`
after every change and promises the checks never touch the Startup folder.
2 checks in test_fixes2.py ran the installer with --yes, which takes away a
start-up file the installer wrote, without first pointing APPDATA at a
throwaway folder. A member who had answered yes at question 9 lost the file
that starts their board, just by running the checks. Found by walking the
guide's steps as typed in a throwaway home on 2026-09-24.

This check runs those 2 checks in a separate pytest with APPDATA pointed at a
folder holding a start-up file exactly as the installer writes it, and shows
the file is still there afterwards.
"""
import os
import subprocess
import sys

from conftest import REPO

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def test_running_the_checks_leaves_a_real_start_up_file_alone(tmp_path):
    import install
    appdata = tmp_path / "member-appdata"
    startup = (appdata / "Microsoft" / "Windows" / "Start Menu" / "Programs"
               / "Startup")
    startup.mkdir(parents=True)
    member_file = startup / install.LOGON_NAME
    member_file.write_text(install.logon_text(3020), encoding="utf-8")
    home = tmp_path / "member-home"
    home.mkdir()
    env = dict(os.environ, APPDATA=str(appdata), HOME=str(home),
               USERPROFILE=str(home))
    env.pop("FORGE_CONFIG", None)
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "tests/test_fixes2.py", "-k",
         "no_vault_can_install or uninstall_leaves_everything"],
        cwd=str(REPO), env=env, capture_output=True, text=True, timeout=300,
        creationflags=NO_WINDOW)
    assert "passed" in r.stdout, r.stdout + r.stderr
    assert member_file.is_file(), (
        "running the checks took away the member's own start-up file")
