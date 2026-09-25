"""Mac only (fault row 7, build plan V3): the empty web board must not tell a Mac member to type `python`.

The empty board's first-run panel said "python tools/demo_board.py --out demo --serve" on every
computer (seen on GitHub's test Macs, 2026-09-24). A Mac has python3 and no python. The board now
tells the page which command this computer runs Python with, and the page prints that.

Run by name only:  python3 -m pytest -q tests/mac/mac_board_names_python3.py
"""
import json
import sys
import threading
import urllib.request

import pytest

from conftest import REPO

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="Mac wording")


def test_the_board_tells_the_page_to_say_python3(store, tmp_path):
    from engine import server

    httpd = server.make_server(store, {"summary_note": "", "db_path": str(tmp_path / "t.db")}, tmp_path, port=0)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        with urllib.request.urlopen("http://127.0.0.1:%d/api/state" % port, timeout=10) as r:
            state = json.loads(r.read())
    finally:
        httpd.shutdown()
        httpd.server_close()
    assert state["config"].get("python") == "python3"


def test_the_page_prints_the_command_the_board_names():
    js = (REPO / "viewer" / "app.js").read_text(encoding="utf-8")
    assert "<code>python tools/demo_board.py" not in js, "the page prints `python` whatever the computer"
    assert "STATE.config.python" in js
