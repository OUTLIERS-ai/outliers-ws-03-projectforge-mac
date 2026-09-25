"""Fault row 9 (build plan V3): the board must not wait 35 seconds for a name look-up before it answers.

Measured 2026-09-24 on all 3 of GitHub's test Macs: Python's socket.getfqdn("127.0.0.1") took
35 seconds on every call, and Python's web server calls it once when it starts, only to fill in a
name nothing here uses. So the board answered 35 seconds after it started, and the checks that wait
20 seconds for it failed (the 5 checks that said the board "never started"). The server now starts with no name look-up.

This check makes the look-up slow on purpose (30 seconds), so it shows the fault on any computer,
then times how long the server takes to start. It is kept in tests/mac/ and named mac_*.py so a
plain `python -m pytest -q` never runs it and the counts the guides print stay true. Run by name:
  python3 -m pytest -q tests/mac/mac_server_starts_quickly.py
"""
import subprocess
import sys
import textwrap
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]


def test_the_server_starts_without_waiting_for_a_name_look_up():
    code = textwrap.dedent('''
        import socket, sys, time
        sys.path.insert(0, {kit!r})
        socket.getfqdn = lambda *a, **k: (time.sleep(30), "slow.example")[1]
        t = time.time()
        from http.server import BaseHTTPRequestHandler
        from engine import server
        srv = server.BoardServer(("127.0.0.1", 0), BaseHTTPRequestHandler)
        print("started in %.1f seconds" % (time.time() - t))
        srv.server_close()
    ''').format(kit=str(KIT))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert r.returncode == 0, r.stdout + r.stderr
    took = float(r.stdout.split("started in ")[1].split()[0])
    assert took < 5, "the server took %.1f seconds to start: it waited for the name look-up" % took
