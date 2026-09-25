"""python forge.py make-copy <folder>: a practice copy of the board.

The guide tells you to change a copy, never the board you use every day. A
plain folder copy is not safe for that: it carries the record of the running
board (data/forge.pid), the same port, the same summary note in your vault,
and an installer that would point your agents at the copy. This makes a copy
that shares none of those:

  - the code, and a snapshot of today's cards in its own data/forge.db
    (taken with SQLite's own backup, so it is whole even while the everyday
    board is running);
  - its own port: the next free one after yours, never 3001, 3010, 3020 or
    4040, the 4 ports this set of downloads uses;
  - no summary note, no schedule, no start-up file, no record file;
  - "copy_of" in its config.json, so install.py and tools/schedule.py refuse
    to run there.

Your agents and /forge-run keep using the everyday board. Nothing done in the
copy reaches it.
"""
import json
import shutil
import socket
import sqlite3
from pathlib import Path

from .config import PY, atomic_write, config_path, db_path, read_json_file

SET_PORTS = {3001, 3010, 3020, 4040}
SKIP = ("data", "config.json", "__pycache__", ".pytest_cache", "*.tmp",
        "*.removed-*")


def port_is_free(port):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", int(port)))
        return True
    except OSError:
        return False
    finally:
        s.close()


def pick_port(everyday):
    for p in range(int(everyday) + 1, int(everyday) + 200):
        if p not in SET_PORTS and port_is_free(p):
            return p
    raise ValueError("no free port found after %s" % everyday)


def make_copy(cfg, base, dest, port=None):
    """Returns (exit code, lines to print)."""
    base = Path(base).resolve()
    dest = Path(dest).expanduser().resolve()
    if dest.exists():
        return 2, [f"Refused: {dest} is already there. Pick a folder name "
                   f"that does not exist yet; nothing was changed."]
    if dest == base or base in dest.parents:
        return 2, [f"Refused: {dest} is inside the download folder. Put the "
                   f"copy beside it instead, for example ../projectforge-"
                   f"practice"]
    everyday_port = int(cfg.get("port") or 3020)
    if port is None:
        port = pick_port(everyday_port)
    elif int(port) == everyday_port:
        return 2, [f"Refused: port {port} is the everyday board's port. "
                   f"Leave --port out and the next free one is picked."]

    shutil.copytree(base, dest, ignore=shutil.ignore_patterns(*SKIP))
    (dest / "data").mkdir()
    src_db = db_path(cfg)
    if src_db.is_file():
        src = sqlite3.connect(src_db)
        dst = sqlite3.connect(dest / "data" / "forge.db")
        try:
            src.backup(dst)
        finally:
            dst.close()
            src.close()

    cpath = config_path()
    raw = read_json_file(cpath) if cpath.is_file() else {}
    raw["port"] = int(port)
    raw["db_path"] = "data/forge.db"
    raw["summary_note"] = ""
    sch = dict(raw.get("schedule") or {})
    sch["installed"] = False
    raw["schedule"] = sch
    raw["copy_of"] = str(Path(cfg.get("_path") or cpath).resolve().parent)
    atomic_write(dest / "config.json", json.dumps(raw, indent=2) + "\n")

    return 0, [
        f"Made a practice copy in {dest}",
        f"  Its board answers on http://127.0.0.1:{port}, so it can run "
        f"beside your everyday board on {everyday_port}.",
        "  Its cards are a copy of today's cards, kept in its own "
        "data/forge.db. Nothing you do in the copy reaches your everyday "
        "board, and your agents and /forge-run keep using the everyday "
        "board.",
        "  It writes no summary note into your vault, and has no start-up "
        "file and no schedule.",
        "Next, in this terminal:",
        f"  cd \"{dest}\"",
        f"  {PY} forge.py serve",
    ]
