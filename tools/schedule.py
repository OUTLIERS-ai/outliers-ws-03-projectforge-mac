"""The optional schedule. OFF unless you switch it on.

It runs tools/run_if_ready.py every N minutes (default 60). That script
checks the database first and only starts Claude when a card is ready, so
an empty board costs nothing but a few milliseconds of Python.

  python tools/schedule.py --print              show what it would set up
  python tools/schedule.py --install [--every 60]
  python tools/schedule.py --remove

Windows: a Scheduled Task named ProjectForge-RunIfReady that starts a hidden
.vbs launcher (so no black window ever flashes up), which runs pythonw.
Mac: a launchd agent in ~/Library/LaunchAgents. Linux: prints a crontab line.
"""
import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from engine.config import (ConfigError, atomic_write,  # noqa: E402
                           config_path, load_config, read_json_file)

TASK = "ProjectForge-RunIfReady"
LABEL = "ai.outliers.projectforge.runifready"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def pythonw():
    exe = Path(sys.executable)
    w = exe.with_name("pythonw.exe")
    return str(w if w.is_file() else exe)


def vbs_text():
    script = BASE / "tools" / "run_if_ready.py"
    cmd = f'"{pythonw()}" "{script}"'
    return ("' ProjectForge: run the orchestrator only if a card is ready.\n"
            "' Hidden window (0), waits for it (True), passes its exit code.\n"
            'Set sh = CreateObject("WScript.Shell")\n'
            f'sh.CurrentDirectory = "{BASE}"\n'
            f"WScript.Quit sh.Run(\"{cmd.replace(chr(34), chr(34) * 2)}\", "
            "0, True)\n")


def plan(every):
    system = platform.system()
    if system == "Windows":
        vbs = BASE / "data" / f"{TASK}.vbs"
        tr = f'wscript.exe //B //Nologo "{vbs}"'
        return {"system": system, "files": {str(vbs): vbs_text()},
                "install": ["schtasks", "/Create", "/TN", TASK, "/SC",
                            "MINUTE", "/MO", str(every), "/TR", tr, "/F"],
                "remove": ["schtasks", "/Delete", "/TN", TASK, "/F"]}
    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"
        # launchd starts jobs with a bare PATH, so name Claude's folder
        claude = shutil.which("claude") or ""
        dirs = [str(Path(claude).parent)] if claude else []
        dirs += ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/bin"]
        path_env = ":".join(dict.fromkeys(dirs))
        body = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>{LABEL}</string>
  <key>ProgramArguments</key><array>
    <string>{sys.executable}</string>
    <string>{BASE / 'tools' / 'run_if_ready.py'}</string>
  </array>
  <key>StartInterval</key><integer>{every * 60}</integer>
  <key>WorkingDirectory</key><string>{BASE}</string>
  <key>EnvironmentVariables</key><dict>
    <key>PATH</key><string>{path_env}</string>
  </dict>
</dict></plist>
"""
        return {"system": system, "files": {str(plist): body},
                "install": ["launchctl", "load", str(plist)],
                "remove": ["launchctl", "unload", str(plist)]}
    line = (f"*/{every} * * * * cd '{BASE}' && '{sys.executable}' "
            f"tools/run_if_ready.py")
    return {"system": system, "files": {}, "install": None, "remove": None,
            "crontab": line}


def set_installed(flag, every):
    p = config_path()
    if not p.is_file():
        return
    data = read_json_file(p)
    sch = data.setdefault("schedule", {})
    sch["installed"] = flag
    sch["every_min"] = every
    if flag:
        # remember where Claude is: a schedule may start with a bare PATH
        claude = shutil.which("claude")
        if claude:
            sch["claude_path"] = claude
    atomic_write(p, json.dumps(data, indent=2) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--print", action="store_true")
    g.add_argument("--install", action="store_true")
    g.add_argument("--remove", action="store_true")
    ap.add_argument("--every", type=int, default=None,
                    help="minutes between checks (default 60)")
    a = ap.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as e:
        print(e)
        return 1
    if cfg.get("copy_of") and (a.install or a.remove):
        # the schedule has 1 name on the whole computer, so a practice copy
        # switching it on or off would replace or remove the everyday one.
        print(f"Refused: this folder is a practice copy of {cfg['copy_of']}. "
              f"The schedule belongs to the everyday board: run this in "
              f"{cfg['copy_of']} instead. Nothing changed.")
        return 2
    every = a.every or (cfg.get("schedule") or {}).get("every_min") or 60
    if every < 5:
        print("Refused: pick 5 minutes or more.")
        return 2
    p = plan(every)
    if a.print:
        for f in p["files"]:
            print(f"would write {f}")
        if p.get("install"):
            print("would run: " + " ".join(p["install"]))
        if p.get("crontab"):
            print("add this line with `crontab -e`:\n  " + p["crontab"])
        return 0
    if a.install:
        if p.get("crontab"):
            print("Add this line with `crontab -e`:\n  " + p["crontab"])
            return 0
        for f, body in p["files"].items():
            atomic_write(Path(f), body)
        r = subprocess.run(p["install"], capture_output=True, text=True,
                           creationflags=NO_WINDOW)
        if r.returncode != 0:
            print("Could not create the schedule:", (r.stderr or r.stdout))
            return r.returncode
        set_installed(True, every)
        print(f"Schedule on: every {every} minutes. Claude starts only when "
              f"a card is ready. Turn off with: {'python' if os.name == 'nt' else 'python3'} tools/schedule.py "
              f"--remove")
        return 0
    if a.remove:
        if p.get("remove"):
            r = subprocess.run(p["remove"], capture_output=True, text=True,
                               creationflags=NO_WINDOW)
            if r.returncode != 0:
                print("Nothing to remove, or it could not be removed:",
                      (r.stderr or r.stdout).strip())
        for f in p["files"]:
            if os.path.isfile(f):
                os.replace(f, f + ".off")  # keep a copy, never just delete
        set_installed(False, every)
        print("Schedule off.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
