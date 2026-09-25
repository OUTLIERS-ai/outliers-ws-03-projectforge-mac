"""Build a demo board full of made-up work, in its own folder, so you can
see ProjectForge working before you put your own agents on it.

    python tools/demo_board.py --out demo
    python tools/demo_board.py --out demo --serve      (then open the link)

Every name here is invented: Sam Carter Bookkeeping, Priya Shah Design and
Oakfield Joinery are not real businesses. Your real board is not touched:
the demo has its own config.json and database inside --out.
"""
import argparse
import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

AGENTS = ["content-lead", "writer-bot", "editor-bot", "research-bot",
          "social-poster", "inbox-helper"]


def build(out: Path):
    out.mkdir(parents=True, exist_ok=True)
    # a made-up CRM vault, so the person link on the demo card opens a note
    crm = out / "CRM"
    (crm / "People").mkdir(parents=True, exist_ok=True)
    note = crm / "People" / "Dan Pike.md"
    if not note.exists():
        note.write_text("# Dan Pike\n\nOakfield Joinery. Made-up demo "
                        "person.\n", encoding="utf-8")
    cfgp = out / "config.json"
    cfg = {
        "workspace": "Sam Carter Bookkeeping - demo",
        "human": "you", "managers": ["content-lead"], "agents": AGENTS,
        "outward_owners": ["social-poster"], "db_path": "data/forge.db",
        "summary_note": "", "crm_vault": str(crm),
        "departments": [
            {"id": "content", "name": "Content", "lead": "content-lead",
             "color": "#ff3c00"},
            {"id": "sales", "name": "Sales", "lead": "", "color": "#3ccf6e"},
            {"id": "delivery", "name": "Client work", "lead": "",
             "color": "#5b8def"},
        ],
    }
    from engine.config import atomic_write
    atomic_write(cfgp, json.dumps(cfg, indent=2))
    os.environ["FORGE_CONFIG"] = str(cfgp)
    from engine.config import load_config
    from engine.db import open_store
    c = load_config(cfgp)
    db = Path(out / "data" / "forge.db")
    for f in (db, db.with_name("forge.db-wal"), db.with_name("forge.db-shm")):
        if f.exists():
            os.replace(f, f.with_name(f.name + ".old"))
    s = open_store(c)
    Y, O, M = "you", "orchestrator", "content-lead"

    launch = s.add_project("Year-end tax season campaign", "content",
                           summary="Get 10 new sole-trader clients booked "
                                   "before 31 January.", actor=Y)
    clients = s.add_project("Client onboarding", "delivery",
                            summary="Every new client set up inside a week.",
                            actor=Y)
    sales = s.add_project("Follow-ups", "sales",
                          summary="People who asked about bookkeeping.",
                          actor=Y)

    t1 = s.open_card(M, "Year-end tax season campaign", "content",
                     "Write 3 posts: the 5 receipts sole traders forget",
                     assignee_agent="writer-bot", status="ready")
    t2 = s.open_card(M, "Year-end tax season campaign", "content",
                     "Research what sole traders ask in January",
                     assignee_agent="research-bot", status="ready")
    s.set_tags(t2, ["research"], actor=Y)
    t3 = s.open_card(M, "Year-end tax season campaign", "content",
                     "Email to past enquiries: book before the rush",
                     assignee_agent="writer-bot", status="ready")
    s.update_task(t3, {"priority": "high", "due": "2026-10-02"}, actor=Y)
    t4 = s.add_task(launch, "Pick the lead magnet: checklist or calculator",
                    status="backlog", actor=Y)
    t5 = s.add_task(clients, "Set up Priya Shah Design's bank feeds",
                    status="ready", assignee_agent="inbox-helper", actor=Y)
    t6 = s.add_task(clients, "Oakfield Joinery: chase missing VAT receipts",
                    status="ready", assignee_agent="inbox-helper", actor=Y)
    t7 = s.add_task(sales, "Call back Dan at Oakfield Joinery about "
                    "payroll", status="awaiting_you", assignee_agent=Y,
                    actor=Y, crm_person="People/Dan Pike.md")
    t8 = s.add_task(launch, "Schedule the 3 posts", status="backlog",
                    assignee_agent="social-poster", actor=Y)

    # a real run of the loop on 3 cards
    s.dispatch(t1, actor=O)
    s.commit_pass(t1, "writer-bot", "Drafted 3 posts on the receipts sole "
               "traders forget: mileage, home office, phone.",
               outputs=["Content/Drafts/receipts-post-1.md",
                        "Content/Drafts/receipts-post-2.md",
                        "Content/Drafts/receipts-post-3.md"],
               result="needs-review", next_step="Editor checks tone and "
               "the figures on post 2")
    s.handoff(t1, "writer-bot", "editor-bot",
              done="Wrote Content/Drafts/receipts-post-1.md, -2.md, -3.md",
              decisions="Left out the tax-rate numbers because they change "
                        "in April and would date the posts",
              state="3 drafts written. No images yet. Nothing scheduled.",
              next_first="Check post 2: the mileage rate needs a source",
              warnings="Post 3 names a real software brand - check you "
                       "are happy to mention it")

    s.dispatch(t2, actor=O)
    s.commit_pass(t2, "research-bot", "Read 40 public forum threads. Top "
                  "3 questions: what can I claim, when is the deadline, "
                  "do I need an accountant.",
                  result="completed", outputs=["Research/january-questions.md"],
                  next_step="Use the 3 questions as post hooks", actor=O)

    s.add_task(clients, "Sign off the quarterly VAT figures",
               status="ready", assignee_agent=Y, actor=Y)
    s.add_task(clients, "Tidy the client folder names", status="ready",
               actor=Y)
    t9 = s.add_task(clients, "Draft the welcome pack for new clients",
                    status="ready", assignee_agent="writer-bot", actor=Y)
    s.dispatch(t9, actor=O)
    s.add_pass(t9, "writer-bot", "First half of the welcome pack drafted.",
               outputs=["Clients/welcome-pack-draft.md"],
               result="progressed", next_step="Add the document checklist")

    s.dispatch(t6, actor=O)
    s.commit_pass(t6, "inbox-helper", "4 receipts still missing; the "
                  "client has not replied in 6 days.", result="blocked",
                  next_step="You may need to phone them", actor=O)

    # an agent asking for a person: the card gets a red NEEDS YOU chip, the
    # header counts it, and the card joins Awaiting You
    s.escalate(t6, "The client has not replied in 6 days. Please ring "
                   "Oakfield Joinery and ask for the receipts.",
               actor="inbox-helper")

    s.set_checklist(t3, [{"text": "Subject line", "done": True},
                         {"text": "Body", "done": False},
                         {"text": "Booking link", "done": False}], actor=Y)
    s.comment(t4, "Checklist is quicker to make. Let's do that first.",
              actor=Y)
    s.close()
    return cfgp


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="demo")
    ap.add_argument("--serve", action="store_true")
    ap.add_argument("--port", type=int, default=3029)
    a = ap.parse_args(argv)
    cfgp = build(Path(a.out).resolve())
    print(f"Demo board built: {cfgp.parent}")
    if a.serve:
        from engine.config import load_config
        from engine.db import open_store
        from engine import server
        c = load_config(cfgp)
        server.serve(open_store(c), c, BASE, port=a.port)
    else:
        print(f"See it with:  {'python' if os.name == 'nt' else 'python3'} tools/demo_board.py --out {a.out} "
              f"--serve")


if __name__ == "__main__":
    sys.exit(main())
