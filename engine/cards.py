"""Agent limit files (optional).

One JSON file per agent in the `cards/` folder, named after the agent
(writer-bot.json limits the agent called writer-bot), says
which departments it may be handed work in and how many times a day. The
orchestrator refuses a hand-out that breaks either limit. An agent with no
file has no limits. Only YOU write these files: an agent must never widen
its own limits.

Example: cards/example-agent.json.example
"""
import json
from pathlib import Path

# scopes.statuses uses the work-lifecycle statuses; an empty list = no limit.
REQUIRED = ("slug", "dept", "autonomy_tier", "scopes")
AUTONOMY_TIERS = ("supervised", "trusted", "autonomous")


def cards_dir(base_dir):
    return Path(base_dir) / "cards"


def load_cards(base_dir):
    """Return {slug: card} for every valid card json in cards/.
    Best-effort: a malformed file is skipped, never fatal."""
    d = cards_dir(base_dir)
    out = {}
    if not d.is_dir():
        return out
    for fp in sorted(d.glob("*.json")):
        try:
            c = json.loads(fp.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            continue
        if isinstance(c, dict):
            # the FILE NAME names the agent (writer-bot.json limits
            # writer-bot), so a copied example cannot limit the wrong agent
            c["_file"] = fp.name
            out[fp.stem] = c
    return out


def validate_card(c):
    """Return a list of problems with a card (empty = valid)."""
    problems = []
    stem = Path(c.get("_file", "")).stem
    if stem and c.get("slug") and c["slug"] != stem:
        problems.append(f"slug says '{c['slug']}' but the file is named "
                        f"'{c['_file']}'. The file name decides which agent "
                        f"is limited ('{stem}'); set slug to '{stem}' too")
    for k in REQUIRED:
        if k not in c:
            problems.append(f"missing required field: {k}")
    if c.get("autonomy_tier") and c["autonomy_tier"] not in AUTONOMY_TIERS:
        problems.append(f"bad autonomy_tier: {c['autonomy_tier']}")
    sc = c.get("scopes")
    if sc is not None and not isinstance(sc, dict):
        problems.append("scopes must be an object")
    if not isinstance(c.get("max_activations_per_day", 0), int):
        problems.append("max_activations_per_day must be an integer")
    return problems


def validate_all(base_dir):
    """Validate every card; return {slug: [problems]} for any with problems."""
    bad = {}
    for slug, c in load_cards(base_dir).items():
        p = validate_card(c)
        if p:
            bad[slug] = p
    return bad
