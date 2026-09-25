"""Install ProjectForge into your own setup.

    python install.py              the interview (about 2 minutes)
    python install.py --uninstall  take it out again (your board data stays)

What it does, in order, and only after you say yes:
  1. asks where your second brain vault, CRM vault and agents folder are
  2. lists your real agents so you can pick which ones are managers
  3. writes config.json (the only settings file)
  4. creates an EMPTY board database at data/forge.db
  5. installs the agents' tool, forge_agent.py, into <claude folder>/projectforge/
  6. installs the /forge-run command into your Claude Code commands folder
     (backing up any file already there first)
  7. optionally writes a board summary note into your second brain
  8. optionally starts the board by itself, with no window, each time you
     switch on your computer and sign in (question 9).
     Off unless you say yes. Windows: a .vbs file in your Startup folder.
     Mac: a launchd file in ~/Library/LaunchAgents.

It never starts anything on a timer. Running it twice changes nothing the
second time. If something it needs is missing it says so and changes nothing.

For scripts and tests: --yes takes every default without asking, and each
question has a flag (see --help).
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

MARKER = "installed by outliers-ws-03-projectforge"

# the optional start-up file: it starts the board by itself when the computer
# starts. Off unless you answer yes to question 9 (or pass
# --start-with-computer; --logon, its name before 2026-09-24, still works).
LOGON_NAME = "Outliers ProjectForge.vbs"
PLIST_NAME = "ai.outliers.projectforge.plist"
LOGON_MARK = "ProjectForge start-up file (written by install.py)"
# the mark used before 2026-09-24, still recognised so an earlier file is
# replaced or taken away rather than left behind as somebody else's.
OLD_LOGON_MARKS = ("ProjectForge logon file (written by install.py)",)

# 3.11 or newer. 3.9 stopped getting security fixes on 2025-10-31 and 3.10
# stops on 2026-10-31, so naming either would send a member to a runtime
# with no security fixes.
MIN_PY = (3, 11)


def say(msg=""):
    print(msg, flush=True)


def claude_home():
    env = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(env) if env else Path.home() / ".claude"


# ---------------------------------------------------------------- finding

def find_vaults():
    """Folders under your home (and Documents) that contain .obsidian."""
    roots = [Path.home(), Path.home() / "Documents"]
    found = []
    for r in roots:
        try:
            for child in sorted(r.iterdir()):
                if child.is_dir() and (child / ".obsidian").is_dir():
                    if child not in found:
                        found.append(child)
        except OSError:
            continue
    return found


def guess_second_brain(vaults):
    for v in vaults:
        if "brain" in v.name.lower():
            return v
    for v in vaults:
        if "crm" not in v.name.lower():
            return v
    return None


def guess_crm(vaults, second_brain):
    for v in vaults:
        if v != second_brain and ("crm" in v.name.lower() or
                                  (v / "Today.md").is_file()):
            return v
    return None


def read_agents(folders):
    """Every agent file (*.md) in the folders, by the name Claude Code uses:
    the `name:` line in its front matter, else the file name."""
    names = []
    for folder in folders:
        f = Path(folder)
        if not f.is_dir():
            continue
        for md in sorted(f.glob("*.md")):
            if md.name.lower() == "readme.md":
                continue
            name = md.stem
            try:
                # utf-8-sig: a file saved by Notepad as "UTF-8 with BOM"
                # starts with a mark, and the front matter below would never
                # be found, so the agent was filed under its file name
                text = md.read_text(encoding="utf-8-sig", errors="replace")
            except OSError:
                continue
            if text.startswith("---"):
                for line in text.split("\n")[1:40]:
                    if line.strip() == "---":
                        break
                    if line.lower().startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip("'\"")
                        break
            if name and name not in names:
                names.append(name)
    return names


# ---------------------------------------------------------------- asking

class Asker:
    def __init__(self, yes):
        self.yes = yes

    def ask(self, prompt, default=""):
        if self.yes:
            say(f"{prompt}: {default}")
            return default
        shown = f" [{default}]" if default not in ("", None) else ""
        try:
            got = input(f"{prompt}{shown}: ").strip()
        except EOFError:
            got = ""
        return got or default

    def confirm(self, prompt, default=True):
        if self.yes:
            return True
        d = "Y/n" if default else "y/N"
        try:
            got = input(f"{prompt} [{d}]: ").strip().lower()
        except EOFError:
            got = ""
        return default if not got else got.startswith("y")

    def offer(self, prompt, default=False):
        """A question that keeps its default under --yes, so a script can
        never switch something on that was never asked for."""
        if self.yes:
            return default
        d = "Y/n" if default else "y/N"
        try:
            got = input(f"{prompt} [{d}]: ").strip().lower()
        except EOFError:
            got = ""
        return default if not got else got.startswith("y")


def split_names(s):
    return [x.strip() for x in (s or "").split(",") if x.strip()]


# ------------------------------- starting it by itself when the computer starts
#
# Pieces 1, 2 and 4 all offer this, so the board offers it too. It is off
# unless you say yes: --yes takes the default for every other question and
# leaves this one off, so a script can never put a file in your Startup
# folder without being told to.
#
# Windows: a .vbs file in your Startup folder. `Run ..., 0, False` means 0 =
# no window at all and False = do not wait for it, and the program it starts
# is pythonw.exe, the copy of Python that has no console. Together that is
# what keeps a window off your desktop when the computer starts.
#
# Mac: a launchd file in ~/Library/LaunchAgents. Linux: the crontab line is
# printed for you to paste, because there is no one place every Linux uses.

def on_windows():
    """Kept as a function so a check can pretend to be a Mac without
    changing os.name, which would change how every file path is read."""
    return os.name == "nt"


def on_mac():
    return sys.platform == "darwin"


def sign_in_words():
    """The computer's own sign-in, in the words each system uses. No
    account of any kind is involved."""
    if on_mac():
        return "log in to your Mac"
    if on_windows():
        return "sign in to Windows"
    return "sign in"


def ours(text):
    return any(m in text for m in (LOGON_MARK,) + OLD_LOGON_MARKS)


def startup_dir():
    appdata = os.environ.get("APPDATA")
    return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / \
        "Programs" / "Startup" if appdata else None


def pythonw():
    """python.exe opens a console window; pythonw.exe does not."""
    p = Path(sys.executable).with_name("pythonw.exe")
    return str(p if p.exists() else sys.executable)


def logon_text(port):
    cmd = '"%s" "%s" serve --port %d' % (pythonw(), BASE / "forge.py",
                                         int(port))
    return ("' %s\n"
            "' Starts the ProjectForge board, with no window, each time you "
            "switch on the computer and sign in.\n"
            "' Remove it with: python install.py --uninstall\n"
            'Set sh = CreateObject("WScript.Shell")\n'
            'sh.CurrentDirectory = "%s"\n'
            'sh.Run "%s", 0, False\n'
            % (LOGON_MARK, BASE, cmd.replace('"', '""')))


def plist_text(port):
    """A start-up job on a Mac starts with almost none of the folders your
    terminal searches, so the full path to Python is written in."""
    return ("""<?xml version="1.0" encoding="UTF-8"?>
<!-- %s -->
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" \
"http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>ai.outliers.projectforge</string>
  <key>ProgramArguments</key><array>
    <string>%s</string><string>%s</string><string>serve</string>
    <string>--port</string><string>%d</string></array>
  <key>WorkingDirectory</key><string>%s</string>
  <key>RunAtLoad</key><true/>
</dict></plist>
""" % (LOGON_MARK, sys.executable, BASE / "forge.py", int(port), BASE))


def logon_path():
    if on_windows():
        d = startup_dir()
        return d / LOGON_NAME if d else None
    if on_mac():
        return Path.home() / "Library" / "LaunchAgents" / PLIST_NAME
    return None


def install_logon(port):
    """Returns (changed, sentence). Never writes over a file somebody else
    put there."""
    path = logon_path()
    if path is None:
        if on_windows():
            return False, ("Could not find your Startup folder, so nothing "
                           "was written.")
        return False, ("On Linux, add this line to  crontab -e  to start the "
                       "board when the computer starts:\n     @reboot cd %s "
                       "&& %s forge.py serve" % (BASE, sys.executable))
    text = logon_text(port) if on_windows() else plist_text(port)
    if path.is_file():
        old = path.read_text(encoding="utf-8", errors="replace")
        if old == text:
            return False, "The start-up file is already in place: %s" % path
        if not ours(old):
            return False, ("%s already exists and was not written by this "
                           "installer, so it was left alone." % path)
    atomic_write(path, text)
    if on_windows():
        return True, ("the board will start by itself, with no window, each "
                      "time you switch on the computer and sign in: %s" % path)
    return True, ("wrote %s. To switch it on now, without logging out:\n"
                  "     launchctl load -w %s" % (path, path))


def remove_logon():
    """Take away only a file this installer wrote.

    Returns (removed, left_alone). A file of the same name that somebody else
    put there is never removed, and it is named to the member instead of being
    passed over in silence."""
    gone, kept = [], []
    d = startup_dir()
    for p in ([d / LOGON_NAME] if d else []) + \
            [Path.home() / "Library" / "LaunchAgents" / PLIST_NAME]:
        try:
            if not p.is_file():
                continue
            if not ours(p.read_text(encoding="utf-8", errors="replace")):
                kept.append(str(p))
                continue
            p.unlink()
            gone.append(str(p))
        except OSError:
            continue
    return gone, kept


# ---------------------------------------------------------------- writing

def atomic_write(path, text):
    from engine.config import atomic_write as aw
    aw(path, text)


def write_if_changed(path, text, backup=False):
    """Returns 'created', 'updated', or 'unchanged'. Backs up first when
    asked and the file is different."""
    path = Path(path)
    if path.is_file():
        old = path.read_text(encoding="utf-8", errors="replace")
        if old == text:
            return "unchanged"
        # back up the member's own file, never our own earlier copy: a 2nd
        # install would otherwise bury their original under ours
        if backup and MARKER not in old:
            stamp = time.strftime("%Y%m%d-%H%M%S")
            shutil.copy2(path, path.with_name(f"{path.name}.bak-{stamp}"))
        atomic_write(path, text)
        return "updated"
    atomic_write(path, text)
    return "created"


def render_command(cfg, adapter_path):
    tpl = (BASE / "commands" / "forge-run.md").read_text(encoding="utf-8")
    return (tpl.replace("{{PY}}", python_word())
               .replace("{{FORGE_DIR}}", BASE.as_posix())
               .replace("{{ADAPTER}}", Path(adapter_path).as_posix())
               .replace("{{HUMAN}}", cfg["human"])
               .replace("{{ORCH}}", cfg["orchestrator"]))


def python_word():
    """The command that starts Python, in the lines Claude Code runs for you.
    A current Mac has python3 and no python, so a bare `python` there fails
    with "command not found"."""
    return "python" if on_windows() else "python3"


def claude_snippet(cfg, adapter_path):
    a = Path(adapter_path).as_posix()
    py = python_word()
    mgr = ", ".join(cfg["managers"]) or "(none yet - only you open cards)"
    return f"""## ProjectForge - the work board (paste into your CLAUDE.md)

Agent work is recorded on the ProjectForge board. The tool is
`{py} "{a}"`.

- Before project work, an agent reads its card: `{py} "{a}" card <card_id>`.
- After the work, it logs a work report:
  `{py} "{a}" pass --card <id> --agent <name> --summary "..." --outputs "files" --result <completed|progressed|blocked|failed|needs-review> --next "..."`.
- Handing work to another agent needs all 5 fields or the board refuses it:
  `{py} "{a}" handoff --card <id> --from <me> --to <next> --done "..." --decisions "... because ..." --state "..." --next-first "..." --warnings "none"`.
- Workers only add to a card (pass, handoff, comment, escalate). Managers ({mgr})
  may also `open` cards. Only the orchestrator (/forge-run) moves cards.
- A handover also makes the receiving agent the card's owner.
- Need a person? `{py} "{a}" escalate --card <id> --agent <name> --note "..."`
  (marks the card NEEDS YOU, counts it in the header and moves it into
  Awaiting You, your own column).
- Agents use only this tool, never forge.py.

## Paste into each worker agent's file

End every task with a WORK REPORT: what you did, the files you made,
decisions and why, where it stands (including what is not done), what the
next agent should do first, and warnings.
"""


# ---------------------------------------------------------------- install

def interview(args, ask, existing):
    ch = claude_home()
    vaults = find_vaults()
    sb_default = args.second_brain or existing.get("second_brain") or \
        str(guess_second_brain(vaults) or "")
    say("\n1. Your second brain vault (the Obsidian folder your agents work "
        "in).\n   Type 'none' if you have not got one: the board works "
        "without it.")
    sb = ask.ask("   Path", sb_default or "none")
    if sb.strip().lower() in ("none", ""):
        sb = ""
        say("   No vault. The board, the cards and the agents' tool all "
            "work the same; there is just no summary note in a vault.")
    elif not Path(sb).is_dir():
        return None, (f"Second brain folder not found: {sb}. Check the path, "
                      f"or type 'none' if you have not got a vault")
    else:
        sb = str(Path(sb).resolve())
        if not (Path(sb) / ".obsidian").is_dir():
            say("   (no .obsidian folder inside - fine if it is still a "
                "vault)")

    crm_default = args.crm if args.crm is not None else \
        existing.get("crm_vault") or \
        str(guess_crm(vaults, Path(sb) if sb else None) or "")
    say("\n2. Your CRM vault (type 'none' if you do not have one).")
    crm = ask.ask("   Path", crm_default or "none")
    if crm.lower() == "none":
        crm = ""
    if crm and not Path(crm).is_dir():
        return None, f"CRM folder not found: {crm}"
    crm = str(Path(crm).resolve()) if crm else ""

    vault_agents = Path(sb) / ".claude" / "agents" if sb else None
    ad_default = args.agents_dir or existing.get("agents_dirs") or \
        [str(ch / "agents")] + ([str(vault_agents)]
                                if vault_agents and vault_agents.is_dir()
                                else [])
    say("\n3. Your agents folder(s). Separate several with ;")
    ad = ask.ask("   Folders", ";".join(ad_default))
    agents_dirs = [str(Path(x.strip()).resolve()) for x in ad.split(";")
                   if x.strip()]
    agents = read_agents(agents_dirs)
    if agents:
        say(f"   Found {len(agents)} agent(s) who can own cards:")
        for i in range(0, len(agents), 4):
            say("     " + ", ".join(agents[i:i + 4]))
    else:
        say("   No agent files found there. You can still use the board; "
            "add agents later and re-run the installer.")

    say("\n4. Managers: which agents may OPEN new cards? Everyone else may "
        "only add work reports.\n   Names separated by commas. No default: "
        "press Enter for none (then only you open cards).")
    mgr_default = args.managers if args.managers is not None else \
        ",".join(existing.get("managers", []))
    managers = split_names(ask.ask("   Managers", mgr_default))
    unknown = [m for m in managers if m not in agents]
    if unknown:
        say(f"   Note: not in your agents folder: {', '.join(unknown)} "
            f"(kept anyway)")

    say("\n5. Which agents' work reaches other people (posts, emails, "
        "messages)?\n   Their finished cards stop in Review for you to check. "
        "Commas. No default: press Enter for none.")
    out_default = args.outward if args.outward is not None else \
        ",".join(existing.get("outward_owners", []))
    outward = split_names(ask.ask("   Agents whose work reaches other people", out_default))

    say("\n6. What should the board call you?")
    human = ask.ask("   Your name on the board",
                    args.name or existing.get("human") or "you")

    if sb:
        say("\n7. Where should the /forge-run command go?\n   user = every "
            "Claude Code session on this computer;\n   vault = only sessions "
            "opened in your second brain.")
        if args.commands:
            say(f"   user or vault: {args.commands}")
            where = args.commands.lower()
        else:
            where = ask.ask("   user or vault",
                            existing.get("_commands_to", "user")).lower()
        if where not in ("user", "vault"):
            return None, "answer user or vault"
    else:
        say("\n7. The /forge-run command goes to every Claude Code session "
            "on this computer\n   (there is no vault to put it in).")
        where = "user"
    cmd_dir = (ch / "commands") if where == "user" else \
        (Path(sb) / ".claude" / "commands")

    if sb:
        say("\n8. A board summary note in your second brain (a markdown copy "
            "of the board,\n   rewritten after every change). Press Enter to "
            "write it at the path shown,\n   or type 'none' for no note.")
        note_default = args.summary_note \
            if args.summary_note is not None else \
            (existing.get("summary_note") or
             str(Path(sb) / "ProjectForge Board.md"))
        note = ask.ask("   Note path", note_default or "none")
        if note.lower() == "none":
            note = ""
        if note:
            note = str(Path(note).resolve())
    else:
        say("\n8. No summary note: that note lives in a vault, and you have "
            "not got one.")
        note = ""

    port = int(args.port or existing.get("port") or 3020)

    say("\n9. Start the board by itself each time you switch on your computer\n"
        "   and %s? The board is a program that has to be running\n"
        "   for http://127.0.0.1:%d to answer, so if you close its window or\n"
        "   restart the computer, the board is gone until you start it again.\n"
        "   Yes writes a small file that starts it with no window at all.\n"
        "   To stop it, type  %s forge.py serve --stop  in this folder.\n"
        "   To take the file away again:  %s install.py --uninstall"
        % (sign_in_words(), port, python_word(), python_word()))
    if args.start_with_computer:
        say("   Start by itself when the computer starts: yes")
        logon = True
    else:
        logon = ask.offer("   Start by itself when the computer starts?",
                          bool(existing.get("_start_at_logon", False)))
        if ask.yes:
            say("   Start by itself when the computer starts: %s"
                % ("yes" if logon else "no"))

    answers = {
        "second_brain": sb, "crm_vault": crm, "agents_dirs": agents_dirs,
        "agents": agents, "managers": managers, "outward_owners": outward,
        "human": human, "summary_note": note, "port": port,
        "command_path": str(cmd_dir / "forge-run.md"),
        "adapter_dir": str(ch / "projectforge"),
        "_commands_to": where,
        "_start_at_logon": bool(logon),
    }
    return answers, None


def refuse_in_a_copy(existing, doing):
    """A practice copy made by  forge.py make-copy  must never install or
    uninstall: either would point your agents, /forge-run and the start-up
    file at the copy, or take away the ones the everyday board uses."""
    if not existing.get("copy_of"):
        return False
    say(f"Refused: this folder is a practice copy of {existing['copy_of']}. "
        f"{doing} here would point your agents, the /forge-run command and "
        f"the start-up file at the copy, or take away the ones your everyday "
        f"board uses. Run it in {existing['copy_of']} instead. Nothing "
        f"changed.")
    return True


def do_install(args):
    from engine.config import config_path, load_config
    ask = Asker(args.yes)
    say("ProjectForge installer - a work board for your agents.")
    if sys.version_info < MIN_PY:
        say(f"Refused: Python {MIN_PY[0]}.{MIN_PY[1]} or newer is needed "
            f"(you have {sys.version.split()[0]}). Nothing changed.")
        say("Python 3.10 gets security fixes only until 2026-10-31, and older "
            "versions get none. Install a newer Python from "
            "https://www.python.org/downloads/ and run this again.")
        return 1
    cpath = config_path()
    existing = {}
    if cpath.is_file():
        from engine.config import ConfigError, read_json_file
        try:
            existing = read_json_file(cpath)
        except ConfigError as e:
            say(f"\nStopped: {e}")
            say("Nothing changed.")
            return 1
    if refuse_in_a_copy(existing, "Installing"):
        return 1
    answers, err = interview(args, ask, existing)
    if err:
        say(f"\nStopped: {err}. Nothing changed.")
        return 1

    new_cfg = dict(existing)
    new_cfg.update(answers)
    new_cfg.setdefault("workspace", "My AI Workforce")
    new_cfg.setdefault("orchestrator", "orchestrator")
    new_cfg.setdefault("db_path", "data/forge.db")
    # write the settings members are told to edit, so they can see them
    import copy
    from engine.config import DEFAULTS
    for key in ("departments", "hygiene", "intake", "crm_today",
                "federate_sources", "schedule"):
        new_cfg.setdefault(key, copy.deepcopy(DEFAULTS[key]))

    say("\nAbout to:")
    say(f"  write   {cpath}")
    say(f"  create  an empty board in {cpath.parent / 'data'} "
        f"(if not already there)")
    say(f"  install {answers['adapter_dir']}{os.sep}forge_agent.py")
    say(f"  install {answers['command_path']}  (backup first if different)")
    if answers["summary_note"]:
        say(f"  write   {answers['summary_note']}  (only if it does not exist)")
    if answers["_start_at_logon"] and logon_path():
        say(f"  write   {logon_path()}  (starts the board by itself when the computer starts)")
    if not ask.confirm("Go ahead?"):
        say("Nothing changed.")
        return 1

    changes = []
    text = json.dumps(new_cfg, indent=2) + "\n"
    r = write_if_changed(cpath, text)
    if r != "unchanged":
        changes.append(f"config.json {r}")

    cfg = load_config()
    from engine.config import db_path
    dbp = db_path(cfg)
    fresh = not dbp.is_file()
    from engine.db import open_store
    st = open_store(cfg)
    st.close()
    if fresh:
        changes.append(f"empty board created at {dbp}")

    adir = Path(answers["adapter_dir"])
    for fname in ("forge_agent.py", "forge_client.py"):
        src = (BASE / "adapters" / fname).read_text(encoding="utf-8")
        r = write_if_changed(adir / fname, src)
        if r != "unchanged":
            changes.append(f"{fname} {r}")
    r = write_if_changed(adir / "forge_agent.json",
                         json.dumps({"forge_dir": str(BASE)}, indent=2) + "\n")
    if r != "unchanged":
        changes.append(f"forge_agent.json {r}")

    adapter = adir / "forge_agent.py"
    r = write_if_changed(answers["command_path"],
                         render_command(cfg, adapter), backup=True)
    if r != "unchanged":
        changes.append(f"/forge-run command {r}")

    snippet = dbp.parent / "CLAUDE-snippet.md"
    r = write_if_changed(snippet, claude_snippet(cfg, adapter))
    if r != "unchanged":
        changes.append(f"{snippet} {r}")

    if answers["summary_note"] and not Path(answers["summary_note"]).is_file():
        from engine import mirror
        st = open_store(cfg)
        try:
            mirror.write_mirrors(st, cfg, BASE)
            changes.append(f"summary note written: {answers['summary_note']}")
        except OSError as e:
            changes.append(f"summary note NOT written - {e}")
        finally:
            st.close()

    notes = []
    if answers["_start_at_logon"]:
        changed, line = install_logon(cfg["port"])
        (changes if changed else notes).append(line)
    else:
        removed, left_alone = remove_logon()
        for r in removed:
            changes.append(f"start-up file removed: {r}")
        for k in left_alone:
            notes.append(f"{k} was not written by this installer, so it was "
                         f"left alone.")

    say("")
    for n in notes:
        say(f"  {n}")
    if not changes:
        say("Already installed exactly like this. Nothing changed.")
    else:
        for c in changes:
            say(f"  done: {c}")
    say(f"""
Next:
  1. Open the board:      {python_word()} forge.py serve
     then visit          http://127.0.0.1:{cfg['port']}
     (leave that window open. To stop the board, press Ctrl+C in it, or
     open a second terminal, type  cd "{BASE}"  and then
     {python_word()} forge.py serve --stop )
  2. Paste the lines in  {snippet}
     into the CLAUDE.md every session reads ({claude_home() / 'CLAUDE.md'}),
     or your vault's CLAUDE.md if you chose "vault" at question 7.
  3. In Claude Code, try  /forge-run dry  (a preview that changes nothing).
Nothing runs on a timer. To add the optional schedule later:
     {python_word()} tools/schedule.py --print""")
    return 0


def removed_stamp():
    """Down to the millisecond. A stamp counting whole seconds meant 2
    uninstalls inside the same second landed on the same name and the second
    one crashed half way."""
    return time.strftime("%Y%m%d-%H%M%S") + f"-{int(time.time() * 1000) % 1000:03d}"


def move_aside(path, stamp):
    """Move a file or folder out of the way, never onto an earlier one."""
    target = path.with_name(f"{path.name}.removed-{stamp}")
    n = 2
    while target.exists():
        target = path.with_name(f"{path.name}.removed-{stamp}-{n}")
        n += 1
    os.replace(path, target)
    return target


def do_uninstall(args):
    from engine.config import ConfigError, config_path, load_config
    ask = Asker(args.yes)
    cpath = config_path()
    if not cpath.is_file():
        say("Not installed (no config.json). Nothing changed.")
        return 0
    try:
        cfg = load_config()
    except ConfigError as e:
        say(f"Stopped: {e}")
        say("Nothing changed.")
        return 1
    if refuse_in_a_copy(cfg, "Uninstalling"):
        return 1
    if not ask.confirm("Remove the /forge-run command, the agents' tool and "
                       "the file that starts the board when the computer "
                       "starts? Your board data and config stay.",
                       default=False):
        say("Nothing changed.")
        return 1
    stamp = removed_stamp()
    # first, stop a board that is running: a board that started by itself has no
    # window, so there is nothing for the member to press Ctrl+C in.
    try:
        from engine import server
        if server.running(cfg):
            server.stop(cfg)
    except Exception as e:  # noqa: BLE001 - say it, never swallow it
        say(f"  (could not stop the board: {e})")
    removed, left_alone = remove_logon()
    for gone in removed:
        say(f"  removed the start-up file: {gone}")
    for k in left_alone:
        say(f"  left alone: {k} was not written by this installer")
    # the agents' tool FIRST, because moving a folder is the step that can
    # fail (Windows refuses while any file inside is open). Doing it first
    # means a failure leaves everything as it was, and running the uninstall
    # again once the file is closed finishes the job.
    adir = Path(cfg.get("adapter_dir", ""))
    if adir.is_dir() and (adir / "forge_agent.json").is_file():
        try:
            moved = move_aside(adir, stamp)
        except OSError as e:
            say(f"\nStopped: {adir} could not be moved aside "
                f"({e.strerror or e}).")
            say("Something is using a file in that folder. Close your editor, "
                "close any terminal sitting in that folder, and stop any "
                "agent that is running, then run this again.")
            say("Nothing was changed.")
            return 1
        say(f"  moved the agents' tool aside: {moved}")
    cmd = Path(cfg.get("command_path", ""))
    if cmd.is_file():
        body = cmd.read_text(encoding="utf-8", errors="replace")
        if MARKER in body:
            try:
                move_aside(cmd, stamp)
            except OSError as e:
                say(f"\nStopped: {cmd} could not be moved aside "
                    f"({e.strerror or e}). Close whatever has it open and "
                    f"run this again.")
                return 1
            baks = [b for b in sorted(cmd.parent.glob(f"{cmd.name}.bak-*"))
                    if MARKER not in b.read_text(encoding="utf-8",
                                                  errors="replace")]
            if baks:
                shutil.copy2(baks[-1], cmd)
                say(f"  restored your earlier {cmd.name} from {baks[-1].name}")
            say(f"  removed /forge-run ({cmd})")
        else:
            say(f"  left {cmd} alone - it is not the one we installed")
    if (cfg.get("schedule") or {}).get("installed"):
        import subprocess
        subprocess.run([sys.executable, str(BASE / "tools" / "schedule.py"),
                        "--remove"],
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW",
                                             0))
    from engine.config import db_path
    report_pasted_lines(cfg, adir / "forge_agent.py",
                        db_path(cfg).parent / "CLAUDE-snippet.md")
    say(f"Done. Your board is still in {db_path(cfg).parent} and your "
        f"settings in {cpath}.")
    return 0


SNIPPET_HEADING = "## ProjectForge - the work board (paste into your CLAUDE.md)"


def pasted_lines(path, wanted):
    """Line numbers and text of the lines in `path` that came from the snippet
    or name the agents' tool. Reads only; never changes the file."""
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    hits = []
    for n, line in enumerate(text.splitlines(), start=1):
        s = line.strip()
        if s and (s in wanted or "forge_agent.py" in s):
            hits.append((n, s))
    return hits


def report_pasted_lines(cfg, adapter, snippet_file):
    """Step 9 of the guide has the member paste lines into CLAUDE.md. Those
    lines tell every session to run the agents' tool, which the uninstall has
    just moved aside. The member's own files are theirs: name the file and
    each line to take out, and change nothing."""
    first_part = claude_snippet(cfg, adapter).split(
        "## Paste into each worker agent's file")[0]
    wanted = {ln.strip() for ln in first_part.splitlines() if ln.strip()}
    places = [claude_home() / "CLAUDE.md"]
    if cfg.get("second_brain"):
        places.append(Path(cfg["second_brain"]) / "CLAUDE.md")
    for d in cfg.get("agents_dirs") or []:
        if Path(d).is_dir():
            places.extend(sorted(Path(d).glob("*.md")))
    say("\nOne step is yours. This uninstaller never edits your CLAUDE.md or "
        "your agent files,\nbut the lines you pasted in at step 9 of the guide "
        "still tell your agents to run\nforge_agent.py, which has just been "
        "moved aside. Take these lines out by hand,\nwith the blank lines "
        "between them, and save the file:")
    found = False
    seen = set()
    for p in places:
        key = os.path.normcase(str(Path(p).resolve()))
        if key in seen:
            continue
        seen.add(key)
        hits = pasted_lines(p, wanted)
        if not hits:
            continue
        found = True
        say(f"\n  In {p}")
        for n, s in hits:
            say(f"    line {n}: {s}")
    if not found:
        say(f"\n  None found in {claude_home() / 'CLAUDE.md'}"
            + (f" or {Path(cfg['second_brain']) / 'CLAUDE.md'}"
               if cfg.get("second_brain") else "")
            + " or your agent files.")
        say("  If you pasted them into another CLAUDE.md, take out the "
            "section headed")
        say(f"  {SNIPPET_HEADING.split(' (')[0]}")
    say(f"  The same lines are in {snippet_file}, to compare against.\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Install ProjectForge")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--yes", action="store_true",
                    help="take every default without asking")
    ap.add_argument("--second-brain", dest="second_brain")
    ap.add_argument("--crm", help="CRM vault path, or 'none'")
    ap.add_argument("--agents-dir", dest="agents_dir", action="append",
                    help="an agents folder (repeat for several)")
    ap.add_argument("--managers", help="comma-separated agent names")
    ap.add_argument("--outward", help="comma-separated agent names")
    ap.add_argument("--name", help="what the board calls you")
    ap.add_argument("--commands", choices=["user", "vault"])
    ap.add_argument("--summary-note", dest="summary_note",
                    help="path of the summary note, or 'none'")
    ap.add_argument("--port", type=int)
    ap.add_argument("--start-with-computer", dest="start_with_computer",
                    action="store_true",
                    help="answer yes to question 9: start the board by itself "
                         "each time you switch on the computer and sign in "
                         "(off unless you pass this)")
    # the name this setting had until 2026-09-24, still accepted so nothing
    # typed or saved earlier breaks; hidden from --help.
    ap.add_argument("--logon", dest="start_with_computer", action="store_true",
                    help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    return do_uninstall(args) if args.uninstall else do_install(args)


if __name__ == "__main__":
    sys.exit(main())
