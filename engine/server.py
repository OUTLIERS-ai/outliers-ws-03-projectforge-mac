"""The web board. Python standard library only; nothing to install.

Serves the viewer (viewer/) and a small JSON API over the store. Listens on
127.0.0.1 only, so no other computer can reach it.

3 guards stop a web page you happen to visit from using the board (the same
3 guards the Jeeves download uses):
  - Host check on every request: it must be addressed to 127.0.0.1 or
    localhost. This stops "DNS rebinding", a trick where a web page renames
    its own address to 127.0.0.1 and then reads or writes your board.
  - Origin check on every write: only a page served by this board may write.
  - Content-Type check on every write: it must be application/json. A web
    page cannot send that to another site without the browser first asking
    this server for permission, and this server never gives it.

The board also runs the no-AI health check (engine/hygiene.py) when it
starts and then every few minutes, so Alerts and the 45-minute return of
hung cards work under plain `serve`, not only under `daemon`.
"""
import json
import os
import signal
import socket
import socketserver
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from . import cards as cards_mod
from . import mirror
from .config import PY
from .rules import NotAllowed

ALLOWED_HOSTS = {"127.0.0.1", "localhost"}
MAX_BODY = 1_000_000
LAST_CHECK = {"ts": "", "open_alerts": None, "error": ""}
APP_NAME = "projectforge"

HEALTH_FAILED = (
    "The board's health check has stopped. Until this is fixed the board is "
    "not watching for overdue, stale or stuck cards, is not sending back "
    "cards whose agent never reported, and is not archiving old Done cards. "
    "The reason: {why}")


def health_check_once(store, config, base_dir):
    """Run the no-AI health check once and remember when."""
    from . import hygiene
    info = hygiene.run(store, config, base_dir)
    LAST_CHECK["ts"] = info.get("ts") or time.strftime("%Y-%m-%d %H:%M:%S")
    LAST_CHECK["open_alerts"] = info.get("open_alerts")
    return info


def health_check_guarded(store, config, base_dir):
    """Run the check and, if it fails, say so ON THE BOARD.

    A failure used to print one line into a terminal window the member had
    minimised, so the 4 jobs above stopped with nothing on screen. Now it
    raises a red alert, which the next check that works clears by itself.
    """
    try:
        info = health_check_once(store, config, base_dir)
        LAST_CHECK["error"] = ""
        return info
    except Exception as e:  # noqa: BLE001 - the board must keep serving
        why = f"{type(e).__name__}: {e}"
        LAST_CHECK["error"] = why
        print("health check error:", why, flush=True)
        try:
            store.raise_alert("health-check", "board",
                              HEALTH_FAILED.format(why=why), "alert")
        except Exception:  # noqa: BLE001 - never hide the failure
            pass
        return None


def make_handler(store, config, base_dir: Path):
    viewer_dir = Path(base_dir) / "viewer"
    ctypes = {".html": "text/html; charset=utf-8",
              ".js": "application/javascript; charset=utf-8",
              ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml",
              ".png": "image/png"}

    def public_config():
        return {
            "workspace": config.get("workspace", "ProjectForge"),
            "departments": config.get("departments", []),
            "human": config.get("human", "you"),
            "orchestrator": config.get("orchestrator", "orchestrator"),
            "agents": config.get("agents", []),
            "managers": config.get("managers", []),
            "outward_owners": config.get("outward_owners", []),
            "crm_vault": config.get("crm_vault", ""),
            # the command this computer runs Python with, for commands the
            # page prints (python3 on a Mac, python on Windows)
            "python": "python" if os.name == "nt" else "python3",
        }

    class Handler(BaseHTTPRequestHandler):
        def _send(self, code, body, ctype="application/json"):
            data = body if isinstance(body, bytes) else \
                json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control",
                             "no-cache, no-store, must-revalidate")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(data)

        def _host_ok(self):
            host = (self.headers.get("Host") or "").strip()
            if host.startswith("["):
                host = host[1:].split("]", 1)[0]
            else:
                host = host.rsplit(":", 1)[0]
            return host.lower() in ALLOWED_HOSTS

        def _origin_ok(self):
            origin = self.headers.get("Origin")
            if not origin:
                return True  # our own tools, tests, and older browsers
            o = urlparse(origin)
            return (o.hostname or "").lower() in ALLOWED_HOSTS and \
                o.port == self.server.server_address[1]

        def do_GET(self):
            if not self._host_ok():
                return self._send(403, {"error": "refused: wrong address"})
            url = urlparse(self.path)
            path, q = url.path, parse_qs(url.query)
            if path == "/api/state":
                state = store.state()
                state["config"] = public_config()
                state["last_check"] = LAST_CHECK["ts"]
                state["health_error"] = LAST_CHECK["error"]
                state["refused_today"] = store.refusals_today()
                return self._send(200, state)
            if path == "/api/events":
                return self._send(200, store.recent_events())
            if path == "/api/agents":
                return self._send(200, store.agents_summary())
            if path == "/api/alerts":
                return self._send(200, store.open_alerts())
            if path == "/api/metrics":
                return self._send(200, store.metrics(
                    int(q.get("days", ["7"])[0])))
            if path == "/api/cards":
                return self._send(200, cards_mod.load_cards(base_dir))
            if path == "/api/next":
                wip = config.get("hygiene", {}).get("wip_limits", {})
                return self._send(200, store.next_actionable(
                    int(q.get("limit", ["5"])[0]), wip,
                    cards=cards_mod.load_cards(base_dir)))
            if path == "/api/waiting":
                return self._send(200, store.work_waiting(
                    config, cards=cards_mod.load_cards(base_dir)))
            if path == "/api/meta":
                # Who is answering on this port. `serve --stop` reads this
                # before it ends anything, so a record left behind by an old
                # board can never end another program that happens to have
                # the same process number now.
                # "record" names this board's own data/forge.pid, so a
                # copy of the download folder can never stop the board
                # that belongs to another folder.
                return self._send(200, {
                    "app": APP_NAME, "pid": os.getpid(),
                    "port": self.server.server_address[1],
                    "record": str(record_path(config))})
            if path.startswith("/api/task/"):
                try:
                    return self._send(200, store.task_detail(
                        path.rsplit("/", 1)[-1]))
                except KeyError:
                    return self._send(404, {"error": "not found"})
            if path == "/":
                path = "/index.html"
            fp = (viewer_dir / path.lstrip("/")).resolve()
            root = viewer_dir.resolve()
            if (root in fp.parents or fp == root) and fp.is_file():
                return self._send(200, fp.read_bytes(),
                                  ctypes.get(fp.suffix, "text/plain"))
            return self._send(404, {"error": "not found"})

        def do_POST(self):
            if not self._host_ok() or not self._origin_ok():
                return self._send(403, {
                    "error": "refused: writes are only accepted from the "
                             "board's own page"})
            ctype = (self.headers.get("Content-Type") or "").split(";")[0]
            if ctype.strip().lower() != "application/json":
                return self._send(415, {"error": "send application/json"})
            try:
                length = int(self.headers.get("Content-Length", 0))
            except ValueError:
                length = 0
            if length > MAX_BODY:
                return self._send(413, {"error": "too large"})
            try:
                p = json.loads(self.rfile.read(length) or b"{}")
            except (json.JSONDecodeError, UnicodeDecodeError):
                return self._send(400, {"error": "bad json"})
            if not isinstance(p, dict):
                return self._send(400, {"error": "bad json"})
            path = urlparse(self.path).path
            actor = p.get("actor")
            if path not in ("/api/pass", "/api/handoff") and not actor:
                return self._send(400, {
                    "error": "actor is required - name yourself"})
            result = {"ok": True}
            try:
                if path == "/api/federate":
                    result = store.federate(p, actor=actor)
                elif path == "/api/intake":
                    leads = {d["id"]: d.get("lead", "")
                             for d in config.get("departments", [])}
                    ic = config.get("intake", {})
                    result = store.run_intake(leads, ic.get("hold_tags", []),
                                              ic.get("routing", []),
                                              actor=actor)
                elif path == "/api/dispatch":
                    result = store.dispatch(
                        p["task_id"], agent=p.get("agent"), actor=actor,
                        cards=cards_mod.load_cards(base_dir))
                elif path == "/api/commit":
                    result = store.commit_pass(
                        p["task_id"], p["agent"], p["summary"],
                        result=p.get("result", "progressed"),
                        outputs=p.get("outputs"),
                        next_step=p.get("next_step", ""),
                        dedup_key=p.get("key", ""), actor=actor,
                        intent=p.get("intent"))
                elif path == "/api/task/move":
                    store.move_task(p["task_id"], p["status"], actor=actor)
                elif path == "/api/task/reorder":
                    store.reorder(p["status"], p["ids"], actor=actor)
                elif path == "/api/task/update":
                    store.update_task(p["task_id"], p, actor=actor)
                elif path == "/api/task/tags":
                    result["tags"] = store.set_tags(
                        p["task_id"], p.get("tags", []), actor=actor)
                elif path == "/api/alert/dismiss":
                    store.dismiss_alert(p["id"], actor=actor)
                elif path == "/api/project/update":
                    store.update_project(p["project_id"], p, actor=actor)
                elif path == "/api/task/archive":
                    store.set_archived(p["task_id"], p.get("archived", True),
                                       actor=actor)
                elif path == "/api/task/checklist":
                    result["items"] = store.set_checklist(
                        p["task_id"], p.get("items", []), actor=actor)
                elif path == "/api/pass":
                    store.add_pass(p["task_id"], p.get("agent", ""),
                                   p.get("summary", ""),
                                   outputs=p.get("outputs", []),
                                   result=p.get("result", "progressed"),
                                   next_step=p.get("next_step", ""),
                                   dedup_key=p.get("key", ""),
                                   intent=p.get("intent"))
                elif path == "/api/task/comment":
                    store.comment(p["task_id"], p.get("text", ""),
                                  actor=actor)
                elif path == "/api/project/add":
                    result["id"] = store.add_project(
                        p["title"], p["department"],
                        summary=p.get("summary", ""), actor=actor)
                elif path == "/api/task/add":
                    result["id"] = store.add_task(
                        p["project_id"], p["title"],
                        status=p.get("status", "backlog"),
                        assignee_agent=p.get("assignee_agent", ""),
                        context_ref=p.get("context_ref", ""),
                        notes=p.get("notes", ""), actor=actor,
                        crm_person=p.get("crm_person", ""))
                elif path == "/api/open":
                    result["id"] = store.open_card(
                        actor, p["project_title"], p["department"],
                        p["title"], assignee_agent=p.get("assignee_agent", ""),
                        status=p.get("status", "backlog"),
                        context_ref=p.get("context_ref", ""),
                        notes=p.get("notes", ""),
                        crm_person=p.get("crm_person", ""))
                elif path == "/api/handoff":
                    store.handoff(
                        p["task_id"], p.get("from_agent", ""),
                        p.get("to_agent", ""), done=p.get("done", ""),
                        decisions=p.get("decisions", ""),
                        state=p.get("state", ""),
                        next_first=p.get("next_first", ""),
                        warnings=p.get("warnings", ""),
                        context=p.get("context", ""),
                        intent=p.get("intent", "DELEGATE"))
                else:
                    return self._send(404, {"error": "not found"})
            except NotAllowed as e:
                return self._send(403, {"error": str(e)})
            except KeyError as e:
                return self._send(400, {"error": f"missing or unknown: {e}"})
            except ValueError as e:
                return self._send(400, {"error": str(e)})
            try:
                mirror.write_mirrors(store, config, base_dir)
            except OSError:
                pass  # the summary note is a courtesy; never fail a write
            return self._send(200, result)

        def log_message(self, *args):
            pass  # keep quiet

    return Handler


class BoardServer(ThreadingHTTPServer):
    """A server that owns its port outright.

    Python's http.server sets SO_REUSEADDR. On Windows that lets a second
    program bind the SAME port silently, so 2 boards answer on 3020 and your
    browser reaches either one. Here the option is off, and on Windows the
    port is claimed with SO_EXCLUSIVEADDRUSE, so a second board fails with
    a clear error instead."""
    allow_reuse_address = False
    daemon_threads = True

    def server_bind(self):
        excl = getattr(socket, "SO_EXCLUSIVEADDRUSE", None)
        if excl is not None:
            self.socket.setsockopt(socket.SOL_SOCKET, excl, 1)
        # http.server's own server_bind also asks for this address's name
        # (socket.getfqdn), only to fill in server_name, which nothing here uses.
        # On GitHub's test Macs that look-up took 35 seconds on every start
        # (measured 2026-09-24), so the page answered 35 seconds late and the
        # checks that wait 15 or 20 seconds for it failed. The name is now the
        # address as given, with no look-up.
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name, self.server_port = str(host), port


def make_server(store, config, base_dir: Path, port=3020):
    return BoardServer(("127.0.0.1", port),
                       make_handler(store, config, base_dir))


def start_health_loop(store, config, base_dir, every_min=None):
    """Run the health check now, then every few minutes, in the background."""
    every = every_min or (config.get("hygiene") or {}).get(
        "check_every_min", 10) or 10

    def loop():
        while True:
            health_check_guarded(store, config, base_dir)
            time.sleep(max(int(every), 1) * 60)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


# ------------------------------------------------------------- start / stop
#
# A board that started by itself when the computer started has no window,
# so there is nothing to press Ctrl+C in. The running board writes its own process number and port into
# data/forge.pid, and `serve --stop` reads that file. It is written by the
# board itself, however it was started: by you, by the start-up file, or by
# the schedule.

def record_path(config) -> Path:
    from .config import db_path
    return db_path(config).parent / "forge.pid"


def write_record(config, port):
    from .config import atomic_write
    atomic_write(record_path(config),
                 json.dumps({"pid": os.getpid(), "port": int(port)}) + "\n")


def read_record(config):
    """{"pid": ..., "port": ...} for the board this computer last started,
    or None when there is no record or it cannot be read."""
    try:
        rec = json.loads(record_path(config).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(rec, dict) and isinstance(rec.get("pid"), int):
        return rec
    return None


def clear_record(config, pid=None):
    """Take the record away, but only when it is still ours."""
    rec = read_record(config)
    if rec and pid is not None and rec.get("pid") != pid:
        return
    try:
        record_path(config).unlink()
    except OSError:
        pass


def ask_board(port, timeout=1.5):
    """The answer from /api/meta on this port, but only when the program
    answering calls itself the board. Anything else comes back as None, so
    it is never stopped."""
    if not port:
        return None
    import urllib.request
    try:
        with urllib.request.urlopen(
                "http://127.0.0.1:%d/api/meta" % int(port),
                timeout=timeout) as r:
            meta = json.loads(r.read().decode("utf-8"))
    except (OSError, ValueError):
        return None
    if isinstance(meta, dict) and meta.get("app") == APP_NAME:
        return meta
    return None


def same_file(a, b):
    try:
        return (os.path.normcase(os.path.abspath(str(a))) ==
                os.path.normcase(os.path.abspath(str(b))))
    except (TypeError, ValueError):
        return False


def is_ours(config, meta):
    """True only when the board answering keeps its record in THIS folder.
    A folder copied with its data/forge.pid names the everyday board's
    process number and port; without this, serve --stop typed in the copy
    ended the everyday board."""
    return bool(meta) and same_file(meta.get("record", ""),
                                    record_path(config))


def running(config):
    """(pid, port) of this folder's board when it is answering AND agrees
    with the record, else None."""
    rec = read_record(config)
    if not rec:
        return None
    meta = ask_board(rec.get("port"))
    if meta and meta.get("pid") == rec["pid"] and is_ours(config, meta):
        return rec["pid"], rec["port"]
    return None


def stop(config):
    """python forge.py serve --stop. Ends the board this computer started,
    whichever window (or none) it is in."""
    rp = record_path(config)
    rec = read_record(config)
    if not rec:
        print(f"The board is not running: there is no record of it in {rp}.",
              flush=True)
        print("If a board is open in a terminal window, press Ctrl+C in that "
              "window instead.", flush=True)
        return 0
    live = running(config)
    if not live:
        print(f"The board on record (process {rec['pid']}, port "
              f"{rec.get('port')}) is not answering. Nothing was stopped.",
              flush=True)
        print("The record is out of date, or it was copied from another "
              "folder, so no other program was touched. The record has been "
              "cleared.", flush=True)
        clear_record(config)
        return 0
    pid, port = live
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as e:
        print(f"Could not stop the board (process {pid}): "
              f"{getattr(e, 'strerror', None) or e}", flush=True)
        return 1
    for _ in range(40):
        if ask_board(port, 0.3) is None:
            break
        time.sleep(0.25)
    print(f"Stopped the board (process {pid}, port {port}).", flush=True)
    clear_record(config, pid)
    return 0


def port_in_use_message(config, port):
    """What to say when the port is taken, naming who has it."""
    other = ("run this one on another port:  %s forge.py serve --port %d"
             % (PY, int(port) + 1))
    meta = ask_board(port)
    if meta and is_ours(config, meta):
        return (f"This board is already running at http://127.0.0.1:{port} - "
                f"open that address in your browser. To stop it:  {PY} "
                f"forge.py serve --stop")
    if meta:
        return (f"Port {port} is already in use by a ProjectForge board from "
                f"another folder. Stop that one from its own folder, or "
                f"{other}")
    return (f"Port {port} is already in use by another program. "
            f"R{other[1:]}")


def serve(store, config, base_dir: Path, port=3020, health_every_min=None):
    try:
        httpd = make_server(store, config, base_dir, port)
    except OSError:
        print(port_in_use_message(config, port), flush=True)
        return 6
    start_health_loop(store, config, base_dir, health_every_min)
    try:
        write_record(config, port)
    except OSError as e:
        print(f"(could not write {record_path(config)}: {e} - the board "
              f"still works, but serve --stop will not find it)", flush=True)
    print(f"ProjectForge board: http://127.0.0.1:{port}", flush=True)
    print("Leave this window open while you use the board. Press Ctrl+C "
          f"here, or run  {PY} forge.py serve --stop  in another terminal, "
          "to stop it.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
        clear_record(config, os.getpid())
    return 0
