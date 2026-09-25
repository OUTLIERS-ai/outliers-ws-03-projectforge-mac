"""forge_agent.py - the one tool your agents use to write on the board.

Your agents are employees on the board. Before working they read their card;
after working they leave a work report (a "pass") and, if someone else
picks the work up next, a handover. The next agent then starts from a
written trail, not a guess.

WHO MAY DO WHAT (checked by the board itself, not just this script):
  * MANAGERS (listed in config.json "managers") may `open` new cards.
  * WORKERS (every other agent) never open or move a card. They `card`
    (read), `pass` (report work), `handoff` (pass it on, which also makes
    the next agent the card's owner), `comment`, and `escalate` (ask for a
    person: shown in red in the activity list, and the card moves into
    Awaiting You).
  * Only the orchestrator moves cards between columns. There is no `move`
    command here on purpose.

A handover must carry all 5 fields or the board refuses it:
  --done        what was done (name the files)
  --decisions   decisions made and why (include the word "because")
  --state       where it stands, including what is NOT done
  --next-first  what the next agent should do first
  --warnings    anything to watch (write "none" if nothing)

USAGE
  python forge_agent.py mywork <agent>
  python forge_agent.py card <card_id>
  python forge_agent.py pass --card <id> --agent <name> --summary "..." \
        [--result progressed] [--outputs a.md,b.md] [--next "..."]
  python forge_agent.py handoff --card <id> --from <name> --to <name> \
        --done "..." --decisions "... because ..." --state "..." \
        --next-first "..." --warnings "none"
  python forge_agent.py comment --card <id> --agent <name> --text "..."
  python forge_agent.py escalate --card <id> --agent <name> --note "..."
  python forge_agent.py open --agent <manager> --dept content \
        --project "Launch" --title "Draft 3 posts" [--assignee <agent>] \
        [--status backlog|ready] [--crm-person "People/Sam Carter.md"]

It writes straight into the board's database, so it works whether or not
the web board is open. It finds the board through forge_agent.json (written
by install.py next to this file) or the FORGE_DIR environment variable.
"""
import argparse
import json
import os
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

HERE = Path(__file__).resolve().parent


def find_forge_dir():
    env = os.environ.get("FORGE_DIR")
    if env:
        return Path(env)
    j = HERE / "forge_agent.json"
    if j.is_file():
        try:
            d = json.loads(
                j.read_text(encoding="utf-8-sig",
                            errors="replace")).get("forge_dir")
            if d:
                return Path(d)
        except ValueError:
            pass
    if (HERE.parent / "forge.py").is_file():
        return HERE.parent
    return None


def get_store():
    fd = find_forge_dir()
    if not fd or not (fd / "engine" / "db.py").is_file():
        print("ProjectForge not found. Run install.py, or set FORGE_DIR to "
              "the folder that holds forge.py.")
        sys.exit(4)
    sys.path.insert(0, str(fd))
    from engine.config import ConfigError, load_config  # noqa: E402
    from engine.db import open_store  # noqa: E402
    try:
        cfg = load_config()
    except ConfigError as e:
        print(e)
        sys.exit(1)
    return open_store(cfg), cfg, fd


def main(argv=None):
    ap = argparse.ArgumentParser(prog="forge_agent")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("mywork")
    s.add_argument("agent")
    s = sub.add_parser("card")
    s.add_argument("task_id")
    s = sub.add_parser("pass")
    s.add_argument("--card", required=True)
    s.add_argument("--agent", required=True)
    s.add_argument("--summary", required=True)
    s.add_argument("--result", default="progressed")
    s.add_argument("--outputs", default="")
    s.add_argument("--next", default="")
    s.add_argument("--key", default="")
    s = sub.add_parser("handoff")
    s.add_argument("--card", required=True)
    s.add_argument("--from", required=True, dest="from_agent")
    s.add_argument("--to", required=True)
    s.add_argument("--done", default="")
    s.add_argument("--decisions", default="")
    s.add_argument("--state", default="")
    s.add_argument("--next-first", dest="next_first", default="")
    s.add_argument("--warnings", default="")
    s.add_argument("--context", default="")
    s = sub.add_parser("comment")
    s.add_argument("--card", required=True)
    s.add_argument("--agent", required=True)
    s.add_argument("--text", required=True)
    s = sub.add_parser("escalate")
    s.add_argument("--card", required=True)
    s.add_argument("--agent", required=True)
    s.add_argument("--note", required=True)
    s = sub.add_parser("open")
    s.add_argument("--agent", required=True, help="the manager opening it")
    s.add_argument("--dept", required=True)
    s.add_argument("--project", required=True)
    s.add_argument("--title", required=True)
    s.add_argument("--assignee", default="")
    s.add_argument("--status", default="backlog")
    s.add_argument("--context", default="")
    s.add_argument("--notes", default="")
    s.add_argument("--crm-person", dest="crm_person", default="")
    a = ap.parse_args(argv)

    store, cfg, fd = get_store()
    sys.path.insert(0, str(fd))
    from engine import mirror  # noqa: E402
    from engine.rules import NotAllowed  # noqa: E402

    def remirror():
        try:
            mirror.write_mirrors(store, cfg, fd)
        except OSError:
            pass

    try:
        if a.cmd == "mywork":
            me = store.agents_summary().get(a.agent) or {}
            cards = me.get("open", [])
            if not cards:
                print(f"No cards assigned to {a.agent}.")
            else:
                print(f"{len(cards)} card(s) assigned to {a.agent}:")
                for c in cards:
                    print(f"  [{c['status']:>12}] {c['id']}  {c['title']}")
            lp = me.get("last_pass")
            if lp:
                print(f"  last report: {lp['ts']} - {lp['result']} - "
                      f"{lp['summary'][:90]}")
        elif a.cmd == "card":
            d = store.task_detail(a.task_id)
            t = d["task"]
            print(f"CARD {t['id']}  [{t['status']}]  {t['title']}")
            print(f"  owner: {t['assignee_agent'] or '-'}")
            if d.get("project"):
                p = d["project"]
                print(f"  project: {p['title']} - {p['summary'][:120]}")
            for k in ("notes", "context_ref", "crm_person"):
                if t.get(k):
                    print(f"  {k}: {t[k][:300]}")
            for h in d["handoffs"][-2:]:
                print(f"  HANDOVER {h['from_agent']} -> {h['to_agent']} "
                      f"({h['ts']})")
                for k in ("done", "decisions", "state", "next_first",
                          "warnings"):
                    print(f"    {k}: {h.get(k, '')}")
            for pa in d["passes"][-4:]:
                print(f"  report [{pa['result']}] {pa['agent']}: "
                      f"{pa['summary'][:160]}"
                      + (f"  -> next: {pa['next_step']}"
                         if pa["next_step"] else ""))
        elif a.cmd == "pass":
            outs = [o.strip() for o in a.outputs.split(",") if o.strip()]
            store.add_pass(a.card, a.agent, a.summary, outputs=outs,
                           result=a.result, next_step=a.next,
                           dedup_key=a.key)
            remirror()
            print("work report logged. The card stays where it is - the "
                  "orchestrator moves cards.")
        elif a.cmd == "handoff":
            store.handoff(a.card, a.from_agent, a.to, done=a.done,
                          decisions=a.decisions, state=a.state,
                          next_first=a.next_first, warnings=a.warnings,
                          context=a.context)
            remirror()
            print(f"handover to {a.to} recorded.")
        elif a.cmd == "comment":
            store.comment(a.card, a.text, actor=a.agent)
            print("comment added.")
        elif a.cmd == "escalate":
            store.escalate(a.card, a.note, actor=a.agent)
            remirror()
            # name the column the card is in now: the next agent goes and
            # looks in the one this message names
            where = store.task_detail(a.card)["task"]["status"]
            print("escalation recorded. It shows in red in the board's "
                  f"activity list, and the card is in "
                  f"{mirror.STATUS_LABEL.get(where, where)}.")
        elif a.cmd == "open":
            tid = store.open_card(a.agent, a.project, a.dept, a.title,
                                  assignee_agent=a.assignee,
                                  status=a.status, context_ref=a.context,
                                  notes=a.notes, crm_person=a.crm_person)
            remirror()
            print(f"opened {tid}")
    except NotAllowed as e:
        print(str(e))
        return 3
    except (KeyError, ValueError) as e:
        print(f"refused: {e}")
        return 2
    finally:
        store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
