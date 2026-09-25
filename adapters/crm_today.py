"""Turn your CRM's Today page into cards on the board.

Reads the ranked table on `Today.md` at the top of your CRM vault (the page
Layer 7 of the Outliers CRM writes each morning) and makes one card per
person, under a project called "CRM - Today". Each card links to that
person's note in `People/` when one exists.

Safe to run again and again: a person already on the board is updated, not
copied. It never sends a message to anyone. The cards land in
"Awaiting You" with you as the owner, because talking to people is your job,
not an agent's (set crm_today.owner in config.json to hand them to a
research agent instead; they then start in Backlog).

  python adapters/crm_today.py            do it
  python adapters/crm_today.py --dry-run  show what it would do
"""
import argparse
import re
import sys
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from engine.config import ConfigError, load_config  # noqa: E402
from engine.db import open_store  # noqa: E402

# rank | person | why | when   (a [[link|alias]] may carry a | of its own)
ROW = re.compile(
    r"^\|\s*(\d+)\s*\|((?:\[\[[^\]]*\]\]|[^|])+)\|(.+?)\|(.*?)\|\s*$")


def clean_person(cell):
    """The person's name from a table cell. Accepts Dan Pike, [[Dan Pike]],
    [[People/Dan Pike]], [[People/Dan Pike.md]] and [[Dan Pike|Dan]]."""
    s = cell.strip()
    m = re.search(r"\[\[([^\]]+)\]\]", s)
    if m:
        s = m.group(1)
    s = s.split("|")[0].strip()
    if s.lower().startswith("people/"):
        s = s[len("people/"):]
    if s.lower().endswith(".md"):
        s = s[:-3]
    return s.strip()


def read_today(crm_vault):
    """Return [{'rank','person','reason','when'}] from Today.md.

    The page needs a table whose rows look like this (the header row and the
    |---| row are skipped because they do not start with a number):

        | 1 | [[Dan Pike]] | Asked about payroll | Call back today |

    rank, the person's name (matching People/Dan Pike.md), why, when.
    """
    page = Path(crm_vault) / "Today.md"
    if not page.is_file():
        return None
    rows = []
    for line in page.read_text(encoding="utf-8",
                              errors="replace").splitlines():
        m = ROW.match(line.strip())
        if not m:
            continue
        person = clean_person(m.group(2))
        rows.append({"rank": int(m.group(1)), "person": person,
                     "reason": m.group(3).strip(), "when": m.group(4).strip()})
    return rows


def person_note(crm_vault, person):
    p = Path(crm_vault) / "People" / f"{person}.md"
    return f"People/{person}.md" if p.is_file() else ""


def build_payload(cfg, rows):
    human = cfg.get("human", "you")
    owner = (cfg.get("crm_today") or {}).get("owner") or human
    status = "awaiting_you" if owner == human else "backlog"
    tasks = []
    for r in rows:
        tasks.append({
            "ref": r["person"].lower(),
            "title": f"Speak to {r['person']} - {r['reason']}",
            "status": status, "assignee_agent": owner,
            "notes": f"From the CRM Today page ({date.today()}), rank "
                     f"{r['rank']}. Why: {r['reason']}. When: {r['when']}.",
            "crm_person": person_note(cfg["crm_vault"], r["person"]),
        })
    return {"source_app": "crm-today",
            "project": {"ref": "crm-today", "title": "CRM - Today",
                        "department": "sales",
                        "summary": "People your CRM ranked for today."},
            "tasks": tasks}


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    try:
        cfg = load_config()
    except ConfigError as e:
        print(e)
        return 1
    crm = cfg.get("crm_vault")
    if not crm:
        print("No CRM vault in config.json - run install.py first.")
        return 1
    rows = read_today(crm)
    if rows is None:
        print(f"No Today.md in {crm}. Build it first with your CRM's "
              f"today command.")
        return 1
    payload = build_payload(cfg, rows)
    if a.dry_run:
        for t in payload["tasks"]:
            print(f"would add: {t['title']}  [{t['status']}]  "
                  f"{t['crm_person'] or '(no person note found: the name must match a file in People/)'}")
        print(f"{len(payload['tasks'])} card(s). Nothing written.")
        return 0
    store = open_store(cfg)
    try:
        r = store.federate(payload, actor="crm-today")
        try:
            from engine import mirror
            mirror.write_mirrors(store, cfg, BASE)  # keep the summary note current
        except OSError:
            pass
    finally:
        store.close()
    made = sum(1 for t in r["tasks"] if t["created"])
    print(f"{made} new card(s), {len(r['tasks']) - made} already on the "
          f"board (brought up to date, never copied).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
