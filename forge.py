"""ProjectForge command line. No AI anywhere in here: plain Python.

  python forge.py serve [--port N]            open the web board
  python forge.py serve --stop                stop a board that is already running (the one from this folder)
  python forge.py make-copy ../projectforge-practice   a practice copy to change safely
  python forge.py list                        open cards in the terminal
  python forge.py projects                    projects and their ids
  python forge.py add-project "Title" --dept content --actor YOU
  python forge.py add-task PROJECT_ID "Title" --actor YOU [--status ready]
  python forge.py move TASK_ID STATUS --actor YOU
  python forge.py pass TASK_ID AGENT "what was done" [--result progressed]
  python forge.py handoff TASK_ID FROM TO --done ... --decisions ... \\
        --state ... --next-first ... --warnings ...
  python forge.py next [--json]               the ranked queue (orchestrator)
  python forge.py waiting                     how many cards are ready now
  python forge.py intake --actor orchestrator         triage the backlog
  python forge.py dispatch TASK_ID --actor orchestrator   take a card (Ready to In Progress)
  python forge.py commit TASK_ID AGENT "summary" --result completed --actor orchestrator
  python forge.py hygiene                     run the health check once
  python forge.py daemon [--interval 10]      same as serve, with its own check interval
  python forge.py mirror                      rewrite the summary note
  python forge.py metrics [--days 7]          board health numbers

Every write must say who made it (--actor). There is no default: an agent
that runs this file without naming itself is stopped, instead of being
counted as you. Your own name is "human" in config.json (default "you");
the orchestrator's is "orchestrator".
"""
import argparse
import json
import sys
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except AttributeError:
    pass

BASE = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE))

from engine import cards as cards_mod  # noqa: E402
from engine import mirror, server  # noqa: E402
from engine.config import PY, ConfigError, load_config  # noqa: E402
from engine.db import (PASS_RESULTS, PERFORMATIVES, STATUSES,  # noqa: E402
                       NotAllowed, open_store)


def _outs(s):
    return [o.strip() for o in (s or "").split(",") if o.strip()]


def build_parser(cfg):
    human = cfg.get("human", "you")
    orch = cfg.get("orchestrator", "orchestrator")
    ACTOR_HELP = (f"who is making this change. You are '{human}' "
                  f"(config.json 'human').")
    ORCH_HELP = f"only the orchestrator ('{orch}') may do this"
    ap = argparse.ArgumentParser(prog="forge")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("serve")
    s.add_argument("--port", type=int, default=cfg.get("port", 3020))
    s.add_argument("--stop", action="store_true",
                   help="stop the board that belongs to this folder, whichever "
                        "port it is on, instead of opening one")
    s = sub.add_parser("make-copy",
                       help="a practice copy of this folder, with its own "
                            "port and its own copy of the cards")
    s.add_argument("folder")
    s.add_argument("--port", type=int, default=None)
    sub.add_parser("mirror")
    sub.add_parser("list")
    sub.add_parser("projects", help="every project and its id")
    sub.add_parser("hygiene")
    s = sub.add_parser("daemon", help="board + no-AI health check loop")
    s.add_argument("--port", type=int, default=cfg.get("port", 3020))
    s.add_argument("--interval", type=int, default=(cfg.get("hygiene") or {}).get("check_every_min", 10),
                   help="minutes between health checks (no AI)")
    s = sub.add_parser("metrics")
    s.add_argument("--days", type=int, default=7)
    s = sub.add_parser("cards", help="list / check the agent limit files")
    s.add_argument("--validate", action="store_true")

    s = sub.add_parser("add-project")
    s.add_argument("title")
    s.add_argument("--dept", required=True)
    s.add_argument("--summary", default="")
    s.add_argument("--actor", required=True, help=ACTOR_HELP)
    s = sub.add_parser("add-task")
    s.add_argument("project_id")
    s.add_argument("title")
    s.add_argument("--status", default="backlog", choices=STATUSES)
    s.add_argument("--agent", default="")
    s.add_argument("--notes", default="")
    s.add_argument("--context", default="")
    s.add_argument("--crm-person", dest="crm_person", default="",
                   help="path of a person note inside your CRM vault")
    s.add_argument("--actor", required=True, help=ACTOR_HELP)
    s = sub.add_parser("move")
    s.add_argument("task_id")
    s.add_argument("status", choices=STATUSES)
    s.add_argument("--actor", required=True, help=ACTOR_HELP)
    s = sub.add_parser("set", help="set due / priority / owner / CRM link")
    s.add_argument("task_id")
    s.add_argument("--due", default=None)
    s.add_argument("--priority", default=None,
                   choices=["low", "normal", "high", "urgent"])
    s.add_argument("--agent", default=None)
    s.add_argument("--crm-person", dest="crm_person", default=None)
    s.add_argument("--actor", required=True, help=ACTOR_HELP)
    s = sub.add_parser("archive")
    s.add_argument("task_id")
    s.add_argument("--restore", action="store_true")
    s.add_argument("--actor", required=True, help=ACTOR_HELP)
    s = sub.add_parser("pass", help="log a work report (never moves a card)")
    s.add_argument("task_id")
    s.add_argument("agent")
    s.add_argument("summary")
    s.add_argument("--outputs", default="")
    s.add_argument("--result", default="progressed", choices=PASS_RESULTS)
    s.add_argument("--next", dest="next_step", default="")
    s.add_argument("--intent", default=None, choices=PERFORMATIVES)
    s = sub.add_parser("comment")
    s.add_argument("task_id")
    s.add_argument("text")
    s.add_argument("--actor", required=True, help=ACTOR_HELP)
    s = sub.add_parser("handoff", help="hand a card on (all 5 fields)")
    s.add_argument("task_id")
    s.add_argument("from_agent")
    s.add_argument("to_agent")
    s.add_argument("--done", default="")
    s.add_argument("--decisions", default="")
    s.add_argument("--state", default="")
    s.add_argument("--next-first", dest="next_first", default="")
    s.add_argument("--warnings", default="")
    s.add_argument("--context", default="")
    s.add_argument("--intent", default="DELEGATE", choices=PERFORMATIVES)

    s = sub.add_parser("next", help="orchestrator: the ranked queue")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("waiting", help="cards an orchestrator pass could "
                       "hand out right now (writes nothing)")
    s.add_argument("--json", action="store_true")
    s = sub.add_parser("intake", help="orchestrator: triage the backlog")
    s.add_argument("--actor", required=True, help=ORCH_HELP)
    s = sub.add_parser("dispatch", help="orchestrator: take a card (Ready to In Progress)")
    s.add_argument("task_id")
    s.add_argument("--agent", default=None)
    s.add_argument("--actor", required=True, help=ORCH_HELP)
    s = sub.add_parser("commit", help="orchestrator: record + move")
    s.add_argument("task_id")
    s.add_argument("agent")
    s.add_argument("summary")
    s.add_argument("--result", default="progressed", choices=PASS_RESULTS)
    s.add_argument("--outputs", default="")
    s.add_argument("--next", dest="next_step", default="")
    s.add_argument("--key", default="")
    s.add_argument("--intent", default=None, choices=PERFORMATIVES)
    s.add_argument("--actor", required=True, help=ORCH_HELP)
    s = sub.add_parser("show", help="one card in full")
    s.add_argument("task_id")
    s.add_argument("--json", action="store_true")
    return ap


def main(argv=None):
    try:
        cfg = load_config()
    except ConfigError as e:
        print(e)
        return 1
    args = build_parser(cfg).parse_args(argv)
    store = open_store(cfg)

    def remirror():
        try:
            mirror.write_mirrors(store, cfg, BASE)
        except OSError as e:
            print(f"(summary note not written: {e})")

    try:
        return run(args, cfg, store, remirror)
    except NotAllowed as e:
        print(str(e))
        return 3
    except (KeyError, ValueError) as e:
        print(f"error: {e}")
        return 2
    finally:
        store.close()


def run(args, cfg, store, remirror):
    c = args.cmd
    if c == "serve":
        if args.stop:
            return server.stop(cfg)
        return server.serve(store, cfg, BASE, port=args.port)
    elif c == "make-copy":
        from engine import practice
        code, lines = practice.make_copy(cfg, BASE, args.folder, args.port)
        print("\n".join(lines))
        return code
    elif c == "mirror":
        try:
            written = mirror.write_mirrors(store, cfg, BASE)
        except mirror.VaultMissing as e:
            print(f"refused: {e}")
            return 2
        print("\n".join(written) or
              "no summary_note set in config.json - nothing written")
    elif c == "list":
        state = store.state()
        titles = {p["id"]: p["title"] for p in state["projects"]}
        rows = [t for t in state["tasks"]
                if t["status"] != "done" and not t["archived"]]
        if not rows:
            print("The board is empty. Add a card with add-task, or open "
                  f"the web board with: {PY} forge.py serve")
        for t in rows:
            print(f"[{t['status']:>12}] {t['id']}  {t['title']}  "
                  f"({titles.get(t['project_id'], '?')}) "
                  f"{t['assignee_agent'] or '-'}")
    elif c == "projects":
        state = store.state()
        if not state["projects"]:
            print(f"No projects yet. Make one with:  {PY} forge.py "
                  "add-project \"Content week 39\" --dept content "
                  f"--actor {cfg.get('human', 'you')}")
        for p in state["projects"]:
            n = sum(1 for t in state["tasks"] if t["project_id"] == p["id"]
                    and not t["archived"])
            print(f"{p['id']}  {p['title']}  [{p['department']}, "
                  f"{p['status']}]  {n} card(s)")
    elif c == "hygiene":
        from engine import hygiene
        try:
            r = hygiene.run(store, cfg, BASE)
        except Exception as e:  # noqa: BLE001 - say it, never swallow it
            print(f"THE HEALTH CHECK FAILED: {type(e).__name__}: {e}")
            print("Nothing on the board was changed. Until this is fixed the "
                  "board is not watching for overdue, stale or stuck cards.")
            store.raise_alert("health-check", "board",
                              server.HEALTH_FAILED.format(
                                  why=f"{type(e).__name__}: {e}"), "alert")
            return 2
        alerts = store.open_alerts()
        titles = {t["id"]: t["title"] for t in store.state()["tasks"]}
        print(f"checked {r['checked']} card(s): {r['open_alerts']} alert(s), "
              f"{r['auto_archived']} old Done card(s) archived, "
              f"{r['reaped']} card(s) whose agent never reported sent back "
              f"to Ready")
        for a in alerts:
            card = a["task_id"] if a["task_id"] in titles else ""
            print(f"  [{a['kind']}] {a['message']}"
                  f"{'  (' + card + ')' if card else ''}")
    elif c == "daemon":
        return server.serve(store, cfg, BASE, port=args.port,
                            health_every_min=max(args.interval, 1))
    elif c == "metrics":
        print(json.dumps(store.metrics(args.days), indent=2))
    elif c == "cards":
        loaded = cards_mod.load_cards(BASE)
        if args.validate:
            bad = cards_mod.validate_all(BASE)
            print(f"{len(loaded)} limit files loaded, {len(bad)} with problems")
            for slug, probs in bad.items():
                print(f"  {slug}: {'; '.join(probs)}")
        else:
            for slug, cd in sorted(loaded.items()):
                print(f"  {slug}: {json.dumps(cd.get('scopes', {}))} "
                      f"max/day={cd.get('max_activations_per_day')}")
            if not loaded:
                print("no limit files in cards/ - every agent is unlimited")
    elif c == "add-project":
        pid = store.add_project(args.title, args.dept, summary=args.summary,
                                actor=args.actor)
        remirror()
        print(pid)
        print(f"  add a card to it:  {PY} forge.py add-task {pid} "
              f"\"Card title\" --actor {args.actor}")
    elif c == "add-task":
        print(store.add_task(args.project_id, args.title, status=args.status,
                             assignee_agent=args.agent, notes=args.notes,
                             context_ref=args.context, actor=args.actor,
                             crm_person=args.crm_person))
        remirror()
    elif c == "move":
        store.move_task(args.task_id, args.status, actor=args.actor)
        remirror()
        print(f"moved {args.task_id} -> {args.status}")
    elif c == "set":
        fields = {k: v for k, v in (
            ("due", args.due), ("priority", args.priority),
            ("assignee_agent", args.agent), ("crm_person", args.crm_person))
            if v is not None}
        store.update_task(args.task_id, fields, actor=args.actor)
        remirror()
        print("updated")
    elif c == "archive":
        store.set_archived(args.task_id, not args.restore, actor=args.actor)
        remirror()
        print("restored" if args.restore else "archived")
    elif c == "pass":
        store.add_pass(args.task_id, args.agent, args.summary,
                       outputs=_outs(args.outputs), result=args.result,
                       next_step=args.next_step, intent=args.intent)
        remirror()
        print("work report logged (the card has not moved - only the "
              "orchestrator moves cards)")
    elif c == "comment":
        store.comment(args.task_id, args.text, actor=args.actor)
        print("commented")
    elif c == "handoff":
        store.handoff(args.task_id, args.from_agent, args.to_agent,
                      done=args.done, decisions=args.decisions,
                      state=args.state, next_first=args.next_first,
                      warnings=args.warnings, context=args.context,
                      intent=args.intent)
        remirror()
        print(f"handed over to {args.to_agent}")
    elif c in ("next", "waiting"):
        loaded = cards_mod.load_cards(BASE)
        if c == "waiting":
            w = store.work_waiting(cfg, cards=loaded)
            if args.json:
                print(json.dumps(w))
            else:
                print(f"{w['total']} card(s) an orchestrator pass could hand "
                      f"out now ({w['ready']} ready, {w['from_backlog']} "
                      f"from backlog){'  [in-progress limit reached]' if w['at_cap'] else ''}")
            return 0
        wip = cfg.get("hygiene", {}).get("wip_limits", {})
        q = store.next_actionable(args.limit, wip, cards=loaded)
        if args.json:
            print(json.dumps(q, indent=2, default=str))
            return 0
        w = q["wip"]
        print(f"in progress {w['in_progress']}"
              f"{'/' + str(w['cap']) if w['cap'] else ''}"
              f"{'  [AT LIMIT - holding]' if w['at_cap'] else ''}"
              f"   |   {q['ready_count']} ready, "
              f"{q['dispatchable_count']} can be handed out")
        if not q["next"]:
            print("  (nothing ready)")
        for n in q["next"]:
            flag = "->" if n["dispatchable"] else f"[hold: {n['hold_reason']}]"
            print(f"  {flag} [{n['priority']:>6}] {n['title']}  "
                  f"({n['project']}) - {n['assignee_agent'] or 'NO OWNER'}  "
                  f"{n['task_id']}")
    elif c == "intake":
        leads = {d["id"]: d.get("lead", "")
                 for d in cfg.get("departments", [])}
        ic = cfg.get("intake", {})
        r = store.run_intake(leads, ic.get("hold_tags", []),
                             ic.get("routing", []), actor=args.actor)
        remirror()
        print(f"intake: {r['assigned']} given an owner, "
              f"{r['promoted']} moved to ready")
    elif c == "dispatch":
        d = store.dispatch(args.task_id, agent=args.agent, actor=args.actor,
                           cards=cards_mod.load_cards(BASE))
        remirror()
        print(json.dumps(d, indent=2, default=str))
    elif c == "commit":
        r = store.commit_pass(args.task_id, args.agent, args.summary,
                              result=args.result, outputs=_outs(args.outputs),
                              next_step=args.next_step, dedup_key=args.key,
                              actor=args.actor, intent=args.intent)
        remirror()
        print(f"committed: {args.result} -> {r['status']}"
              f"{' (moved)' if r['moved'] else ''}")
    elif c == "show":
        d = store.task_detail(args.task_id)
        if args.json:
            print(json.dumps(d, indent=2, default=str))
        else:
            t = d["task"]
            print(f"{t['id']}  [{t['status']}]  {t['title']}")
            print(f"  owner: {t['assignee_agent'] or '-'}   project: "
                  f"{(d['project'] or {}).get('title', '?')}")
            if t.get("crm_person"):
                print(f"  CRM person: {t['crm_person']}")
            for pa in d["passes"]:
                print(f"  pass [{pa['result']}] {pa['agent']}: "
                      f"{pa['summary']}")
            for h in d["handoffs"]:
                print(f"  handover {h['from_agent']} -> {h['to_agent']}: "
                      f"{h['summary']}")
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
