"""ProjectForge client for other programs (federation).

Copy this one file into any program of your own so it can push its work onto
the board over the local web API. Python standard library only.

The board refuses programs it does not know. Add the program's name (the
first argument to ForgeClient) to "federate_sources" in config.json, for
example  "federate_sources": ["crm-today", "my-script"],  and restart the
board.

The board never blocks the program sending to it: if the board is not
running, every call quietly returns None and the program carries on.

A refusal is different from a board that is switched off, and is never
quiet: if the board turns the write down - an unknown program name, a card
missing its title - the call raises ForgeRefused carrying the board's own
words, so nobody is left thinking a write worked when it did not.

Each program owns its own cards. It sends them keyed by ITS OWN ids (`ref`);
the board matches on (program name, ref) and updates the same card on every
re-send, so nothing is ever duplicated. Give a work report a stable `key`
and re-sending it is safe too.

Usage
-----
    from forge_client import ForgeClient

    forge = ForgeClient("my-script")          # the program's name
    forge.federate(
        project={"ref": "spring-launch", "title": "Spring launch",
                 "department": "content"},
        tasks=[{"ref": "post-1", "title": "Draft launch post",
                "status": "backlog", "assignee_agent": "content-writer"}],
    )

Columns : backlog ready in_progress blocked review awaiting_you done tracking
Results : completed progressed blocked failed needs-review
"""
import json
import urllib.error
import urllib.request

import os

DEFAULT_URL = os.environ.get("FORGE_URL", "http://127.0.0.1:3020")


class ForgeRefused(Exception):
    """The board turned the write down and said why. The message is the
    board's own wording, so it reads the same here as it does on screen."""

    def __init__(self, message, status=None, path=None):
        super().__init__(message)
        self.status = status
        self.path = path


class ForgeClient:
    def __init__(self, source_app, base_url=DEFAULT_URL, timeout=4,
                 verbose=False):
        if not source_app:
            raise ValueError("source_app is required")
        self.source_app = source_app
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.verbose = verbose

    # -- low level -------------------------------------------------------
    def _post(self, path, payload):
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{self.base_url}{path}", data=data, method="POST",
            headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return json.loads(r.read() or b"{}")
        except urllib.error.HTTPError as e:
            # the board answered, and the answer is no. Say so out loud in
            # its own words: a silent None here reads as "that worked".
            body = e.read().decode(errors="replace")
            said = body
            try:
                answer = json.loads(body or "{}")
                if isinstance(answer, dict) and answer.get("error"):
                    said = answer["error"]
            except ValueError:
                pass
            if self.verbose:
                print(f"[forge] {path} -> HTTP {e.code}: {body}")
            raise ForgeRefused(
                (said or f"the board answered HTTP {e.code}").strip(),
                status=e.code, path=path) from None
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            # daemon down / unreachable — federation is best-effort
            if self.verbose:
                print(f"[forge] daemon unreachable: {e}")
            return None

    def ping(self):
        """True if the ProjectForge daemon is reachable."""
        req = urllib.request.Request(f"{self.base_url}/api/state")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                return r.status == 200
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    # -- federation ------------------------------------------------------
    def federate(self, project, tasks=None):
        """Upsert a project and its tasks (with optional nested passes) in
        one batch. Returns the id map from the daemon, or None if the daemon
        is unreachable. Raises ForgeRefused if the board turns the write
        down, with the board's own wording."""
        payload = {
            "source_app": self.source_app,
            # the board only accepts programs named in config.json
            # "federate_sources"; add this program's name there
            "actor": self.source_app,
            "project": project,
            "tasks": tasks or [],
        }
        return self._post("/api/federate", payload)

    def project(self, ref, title, department, summary=None, status=None):
        """Upsert a single project, no tasks. Convenience wrapper."""
        proj = {"ref": ref, "title": title, "department": department}
        if summary is not None:
            proj["summary"] = summary
        if status is not None:
            proj["status"] = status
        return self.federate(proj, [])

    def card(self, project, ref, title, status=None, assignee_agent=None,
             context_ref=None, passes=None, **extra):
        """Upsert one project + one task in a single call — the common case
        for an agent reporting work. `project` is the project dict (ref/
        title/department); `ref`/`title` describe the task. Returns the id
        map. Example:

            forge.card(project={"ref": "p1", "title": "Spring launch",
                                "department": "content"},
                       ref="post-1", title="Draft launch post")
        """
        task = {"ref": ref, "title": title}
        if status is not None:
            task["status"] = status
        if assignee_agent is not None:
            task["assignee_agent"] = assignee_agent
        if context_ref is not None:
            task["context_ref"] = context_ref
        if passes:
            task["passes"] = passes
        task.update(extra)
        return self.federate(project, [task])
