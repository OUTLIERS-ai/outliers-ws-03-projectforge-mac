"""Where ProjectForge finds its settings.

One file, `config.json`, next to forge.py. The installer writes it. Nothing
in the code knows anyone's folder names: every path comes from here.

Set the environment variable FORGE_CONFIG to use a different file (the tests
do this so they never touch a real install).
"""
import copy
import json
import os
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# The command a member types to run Python. A current Mac has python3 and no
# python, so a printed "python forge.py ..." fails there with "command not found".
PY = "python" if os.name == "nt" else "python3"

DEFAULTS = {
    "workspace": "My AI Workforce",
    # the name the board uses for YOU, the human. Your moves are allowed.
    "human": "you",
    # the name the orchestrator session writes under. Only it (and you)
    # may move a card between columns.
    "orchestrator": "orchestrator",
    # agents allowed to OPEN new cards. Everyone else only appends reports.
    "managers": [],
    # every agent the installer found. Shown as possible card owners.
    "agents": [],
    # owners whose "completed" stops at Review instead of Done, because
    # their work leaves the building (posts, emails, messages).
    "outward_owners": [],
    "port": 3020,
    "db_path": "data/forge.db",
    "second_brain": "",
    "crm_vault": "",
    "agents_dirs": [],
    # a markdown copy of the board written into your second brain. "" = off.
    "summary_note": "",
    "departments": [
        {"id": "content", "name": "Content", "lead": "", "color": "#ff3c00"},
        {"id": "sales", "name": "Sales", "lead": "", "color": "#3ccf6e"},
        {"id": "delivery", "name": "Delivery", "lead": "", "color": "#5b8def"},
        {"id": "operations", "name": "Operations", "lead": "",
         "color": "#b06bff"},
    ],
    "hygiene": {
        # how often the open board runs the no-AI health check
        "check_every_min": 10,
        "stale_days": 5,
        "blocked_days": 3,
        "due_soon_days": 2,
        "done_archive_days": 14,
        "dispatch_ttl_min": 45,
        "wip_limits": {"in_progress": 10, "review": 8, "awaiting_you": 10},
    },
    "intake": {
        "auto_pickup": True,
        "hold_tags": ["hold", "someday", "icebox", "parked"],
        "routing": [],
    },
    "crm_today": {"enabled": False, "owner": ""},
    # programs allowed to push cards onto the board (the CRM Today reader,
    # or your own scripts using adapters/forge_client.py). Anything else
    # that tries is refused.
    "federate_sources": ["crm-today"],
    # the optional schedule. Off unless you switch it on with
    # tools/schedule.py; it only starts Claude when a card is ready.
    "schedule": {"installed": False, "every_min": 60, "model": ""},
    "orchestrator_cwd": "",
}


class ConfigError(Exception):
    """config.json could not be read. Carries one plain sentence naming the
    line, instead of the wall of error text a broken file used to produce."""


def config_path() -> Path:
    env = os.environ.get("FORGE_CONFIG")
    return Path(env) if env else REPO / "config.json"


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def read_json_file(p) -> dict:
    """Read a settings file a member may have edited by hand.

    utf-8-sig drops the mark Notepad and PowerShell put at the start of a
    file saved as "UTF-8 with BOM". A trailing comma, or anything else JSON
    will not take, comes back as one sentence naming the line.
    """
    p = Path(p)
    try:
        raw = p.read_text(encoding="utf-8-sig", errors="replace")
    except OSError as e:
        raise ConfigError(f"{p} could not be opened: {e.strerror or e}.")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ConfigError(
            f"{p} could not be read: line {e.lineno} is wrong ({e.msg}). "
            f"Open that file, fix line {e.lineno}, and save it as plain "
            f"UTF-8 - or delete the file and run: {PY} install.py")
    if not isinstance(data, dict):
        raise ConfigError(
            f"{p} must hold settings between curly brackets. Fix it, or "
            f"delete the file and run: {PY} install.py")
    return data


def load_config(path=None) -> dict:
    p = Path(path) if path else config_path()
    data = read_json_file(p) if p.is_file() else {}
    cfg = _merge(DEFAULTS, data)
    cfg["_path"] = str(p)
    return cfg


def db_path(cfg) -> Path:
    p = Path(cfg.get("db_path") or "data/forge.db")
    if not p.is_absolute():
        base = Path(cfg["_path"]).parent if cfg.get("_path") else REPO
        p = base / p
    return p


def roles_from(cfg):
    from .rules import Roles
    return Roles(human=cfg.get("human", "you"),
                 orchestrator=cfg.get("orchestrator", "orchestrator"),
                 managers=cfg.get("managers", []),
                 outward_owners=cfg.get("outward_owners", []),
                 agents=cfg.get("agents", []),
                 federate_sources=cfg.get("federate_sources", []))


def atomic_write(path, text):
    """Write a text file so a crash can never leave half a file behind:
    write a temporary sibling, then swap it in with os.replace."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
