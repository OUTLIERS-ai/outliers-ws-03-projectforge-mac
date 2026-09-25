"""Who may do what on the board. Checked inside the store, on every write.

The single-writer rule, set as a working rule on 2026-06-14 and built into
the store here:

  * YOU (the human) may do anything.
  * MANAGERS may open new cards and edit card details.
  * WORKERS (every other agent) may only APPEND: a work report (a "pass"),
    a handover to the next agent, or a comment. They never open a card and
    never move one.
  * The ORCHESTRATOR is the only agent that moves a card between columns.

  * AWAITING YOU is yours. The orchestrator may put a card in it, but only
    you take a card out of it.
  * Programs that push cards in from elsewhere (for example the CRM Today
    reader) must be named in config "federate_sources".

This is a guard rail, not a lock. An agent that lies about its name gets
through; one that follows its instructions and names itself honestly is
stopped before it can make a mess. Every refusal is written to the board's
activity list, so you can see who tried what.
"""


class NotAllowed(Exception):
    """A write that the single-writer rule refuses."""


def _n(name):
    return (name or "").strip().lower()


class Roles:
    SYSTEM = {"hygiene"}  # the no-AI health check (returns hung cards)

    def __init__(self, human="you", orchestrator="orchestrator",
                 managers=(), outward_owners=(), agents=(),
                 federate_sources=()):
        self.human = _n(human) or "you"
        self.orchestrator = _n(orchestrator) or "orchestrator"
        self.managers = {_n(m) for m in managers if _n(m)}
        self.outward = {_n(o) for o in outward_owners if _n(o)}
        # every agent the installer found; empty = no check
        self.agents = {_n(a) for a in agents if _n(a)}
        if self.agents:
            self.agents |= self.managers | self.outward
        self.federate_sources = {_n(f) for f in federate_sources if _n(f)}

    def known_agent(self, name):
        """False only when we have a list of agents and the name is not on
        it (a typo such as "writerbot" for "writer-bot")."""
        if not self.agents:
            return True
        return _n(name) in self.agents

    def can_federate(self, actor):
        return self.kind(actor) == "you" or             _n(actor) in self.federate_sources

    def kind(self, actor):
        a = _n(actor)
        if not a:
            return "nobody"
        if a == self.human:
            return "you"
        if a == self.orchestrator:
            return "orchestrator"
        if a in self.SYSTEM:
            return "system"
        if a in self.managers:
            return "manager"
        return "worker"

    def can_move(self, actor):
        return self.kind(actor) in ("you", "orchestrator", "system")

    def can_open(self, actor):
        return self.kind(actor) in ("you", "manager")

    def can_edit(self, actor):
        return self.kind(actor) in ("you", "manager", "orchestrator")

    def is_orchestrator(self, actor):
        return self.kind(actor) == "orchestrator"

    def is_outward(self, owner):
        toks = set(_n(owner).replace("(", " ").replace(")", " ").split())
        return bool(toks & self.outward) or _n(owner) in self.outward

    def require(self, ok, actor, what):
        if not _n(actor):
            raise NotAllowed(
                f"refused: {what} needs a name. Pass actor=<who you are>.")
        if not ok:
            raise NotAllowed(
                f"refused: {actor} is {self._article(actor)} and may not {what}."
                f" {self.hint(actor)}")

    def _article(self, actor):
        k = self.kind(actor)
        return f"an {k}" if k[:1] in "aeiou" else f"a {k}"

    def hint(self, actor):
        k = self.kind(actor)
        if k == "worker":
            return ("Workers can only add to a card: a work report, a"
                    " handover or a comment. The orchestrator (/forge-run)"
                    " moves cards; a manager opens them.")
        if k == "manager":
            return "Managers open cards; only the orchestrator moves them."
        if k == "orchestrator":
            return "Only you take a card out of Awaiting You."
        return ""


# ---- the 5-field handover standard (ruled 2026-07-19, built here) --------

HANDOVER_FIELDS = [
    ("done", "what was done (name the files)"),
    ("decisions", "decisions made and why"),
    ("state", "where the work stands, including what is NOT done"),
    ("next_first", "what the next agent should do first"),
    ("warnings", "warnings (write 'none' if there are none)"),
]

_PLACEHOLDERS = {"", "-", "--", "n/a", "na", "tbd", "todo", "?", "...", ".",
                 "x", "see above", "handed over", "done"}


def check_handover(fields, flags=True):
    """Return a list of problems. Empty list = a real handover.

    flags=True is the copy that goes back to the AGENT's own terminal: it
    names the command-line option the agent must fill in. flags=False is the
    copy shown to YOU on the board, where those options are noise - you will
    never type them.
    """
    problems = []
    for key, label in HANDOVER_FIELDS:
        flag = f" (--{key.replace('_', '-')})" if flags else ""
        v = (fields.get(key) or "").strip()
        if not v or v.lower() in ("-", "--", "?", "...", "."):
            problems.append(f"missing {label}{flag}")
        elif v.lower() in _PLACEHOLDERS:
            problems.append(f"too thin{flag}: '{v}' does not say {label}")
    dec = (fields.get("decisions") or "").strip().lower()
    if dec and dec.lower() not in _PLACEHOLDERS and dec != "none" \
            and "because" not in dec:
        problems.append("decisions must say why: include the word 'because'"
                        " (or write 'none' if no decision was made)")
    return problems


# the short name of each field, for the copy shown on the board
SHORT = {"done": "what was done", "decisions": "the decisions and why",
         "state": "where it stands", "next_first": "what to do first",
         "warnings": "warnings"}


def _bad_fields(fields):
    """Which of the 5 fields were not good enough, by their short names."""
    bad = []
    for key, _label in HANDOVER_FIELDS:
        v = (fields.get(key) or "").strip()
        if not v or v.lower() in _PLACEHOLDERS:
            bad.append(SHORT[key])
        elif key == "decisions" and v.lower() != "none" \
                and "because" not in v.lower():
            bad.append(SHORT[key] + " (it must say why)")
    return bad


def handover_refusal_for_the_board(agent, fields):
    """The short refusal the board shows YOU: how many of the 5 fields were
    wrong and which ones. 2 lines, not the 10-line red wall the agent's own
    copy needs, and with none of the command-line options in it."""
    bad = _bad_fields(fields)
    n = len(bad)
    return (f"{agent or 'an agent'}'s handover: {n} of the 5 fields "
            f"{'was' if n == 1 else 'were'} not good enough "
            f"({', '.join(bad)})")


def handover_summary(fields):
    return (f"DONE: {fields.get('done','').strip()} | "
            f"NEXT: {fields.get('next_first','').strip()}")
