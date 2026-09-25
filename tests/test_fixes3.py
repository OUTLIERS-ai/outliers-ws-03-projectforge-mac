"""Faults found by reading the download the way a member reads it, on
2026-09-23. One section per fault, each starting with what caused it."""
import threading

import pytest

from conftest import HUMAN, REPO, WORKER, new_card


# ---- 11. escalate told the next agent the wrong column -------------------
# Cause: the message was written when an escalation only left a red mark.
# The card is moved into Awaiting You now, and the message still said it
# had not moved, so every agent reading it looked in the wrong column.

def test_escalate_says_the_card_has_moved_into_awaiting_you(
        cfg_env, monkeypatch, capsys):
    monkeypatch.setenv("FORGE_DIR", str(REPO))
    from adapters import forge_agent
    from engine.config import load_config
    from engine.db import open_store
    st = open_store(load_config())
    tid = new_card(st, status="in_progress")
    st.close()

    assert forge_agent.main(["escalate", "--card", tid, "--agent", WORKER,
                             "--note", "Please ring Oakfield."]) == 0
    said = capsys.readouterr().out

    st = open_store(load_config())
    where = st.task_detail(tid)["task"]["status"]
    st.close()
    assert where == "awaiting_you"          # what the board did
    assert "has not moved" not in said      # what the agent was told
    assert "Awaiting You" in said


def test_escalate_names_the_column_the_card_is_really_in(
        cfg_env, monkeypatch, capsys):
    """A finished card is marked but not moved, and the message has to say
    so: the next agent goes looking in the column it names."""
    monkeypatch.setenv("FORGE_DIR", str(REPO))
    from adapters import forge_agent
    from engine.config import load_config
    from engine.db import open_store
    st = open_store(load_config())
    tid = new_card(st, status="done")
    st.close()

    assert forge_agent.main(["escalate", "--card", tid, "--agent", WORKER,
                             "--note", "One more read, please."]) == 0
    said = capsys.readouterr().out
    assert "Done" in said and "Awaiting You" not in said


# ---- 12. the alerts panel forgot it was open -----------------------------
# Cause: the panel was put back to its remembered state once, behind a
# "wired" mark on the close button, and the remembered state was read
# without a guard. A board refresh every 10 seconds must never take away
# the "Open this card" link a member is reaching for.

VIEWER = REPO / "viewer"


def _alerts_js():
    js = (VIEWER / "app.js").read_text(encoding="utf-8")
    return js.split("function setAlertsPanel(")[1].split(
        "\nasync function renderAlerts(")[0]


def test_the_alerts_panel_is_put_back_on_every_refresh():
    wire = _alerts_js().split("function wireAlertsPanel(")[1]
    puts_back = [ln for ln in wire.splitlines()
                 if "setAlertsPanel(alertsWanted())" in ln]
    assert puts_back, "the panel is never put back the way you left it"
    # 2 spaces of indent is the body of the function; 4 is inside the
    # branch that only runs the first time round
    assert not puts_back[0].startswith("    "), \
        "the panel is only put back on the first render, not on a refresh"


def test_the_alerts_panel_survives_a_browser_that_refuses_storage():
    """A browser with site data switched off throws on the first read. It
    used to take the whole alerts render down with it, alert items, link
    and all."""
    js = (VIEWER / "app.js").read_text(encoding="utf-8")
    for ln in js.splitlines():
        if "localStorage" in ln:
            assert "try {" in ln, \
                f"storage is touched with no guard: {ln.strip()}"


# ---- 13. a refused write looked like a write that worked -----------------
# Cause: the client turned every HTTP answer it did not like into None, so
# a program whose name is not in federate_sources was told nothing at all.

def _boot(store, cfg):
    from engine.server import make_server
    httpd = make_server(store, cfg, REPO, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


PROJECT = {"ref": "spring-launch", "title": "Spring launch",
           "department": "content"}


def test_a_refused_write_raises_with_the_boards_own_words(store):
    from adapters.forge_client import ForgeClient, ForgeRefused
    httpd, url = _boot(store, {"human": HUMAN, "departments": [],
                               "summary_note": ""})
    try:
        forge = ForgeClient("my-script", base_url=url)
        with pytest.raises(ForgeRefused) as e:
            forge.federate(project=PROJECT)
        said = str(e.value)
        assert "refused" in said and "federate_sources" in said
    finally:
        httpd.shutdown()


def test_a_write_the_board_accepts_still_returns_the_id_map(store):
    from adapters.forge_client import ForgeClient
    httpd, url = _boot(store, {"human": HUMAN, "departments": [],
                               "summary_note": ""})
    try:
        forge = ForgeClient("tracker", base_url=url)
        r = forge.card(project=PROJECT, ref="post-1",
                       title="Draft launch post")
        assert r["tasks"][0]["created"] is True
    finally:
        httpd.shutdown()


def test_a_board_that_is_not_running_is_still_quiet(store):
    """The other half of the promise: a program carries on when the board
    is switched off. Only a refusal raises."""
    from adapters.forge_client import ForgeClient
    forge = ForgeClient("tracker", base_url="http://127.0.0.1:1", timeout=1)
    assert forge.federate(project=PROJECT) is None
    assert forge.ping() is False


def test_the_client_file_says_what_a_refusal_does():
    src = (REPO / "adapters" / "forge_client.py").read_text(encoding="utf-8")
    head = src.split("Usage")[0]
    assert "refus" in head and "None" in head


# ---- 14. the alerts and activity panel would not fold away ---------------
# Asked for on 2026-09-23: "in the Alerts and Activity section, I'd like to
# be able to minimise that." It had two bare x buttons instead, one on
# Alerts and one on Activity, and neither said what it did. Folded, the
# panel has to leave the width to the board columns, remember the choice,
# survive the 10-second refresh, and say on the control itself when
# something has arrived behind it.

NL = chr(10)


def _index_html():
    return (VIEWER / "index.html").read_text(encoding="utf-8")


def _style_css():
    return (VIEWER / "style.css").read_text(encoding="utf-8")


def _app_js():
    return (VIEWER / "app.js").read_text(encoding="utf-8")


def test_one_control_folds_the_alerts_and_activity_panel_and_says_so():
    html = _index_html()
    assert 'id="activity-fold"' in html, "there is no control to fold the panel"
    assert 'id="activity-show"' in html, "there is no control to bring it back"
    fold = html.split('id="activity-fold"')[1].split("</button>")[0]
    show = html.split('id="activity-show"')[1].split("</button>")[0]
    assert ">Hide" in fold, "the fold control does not say Hide"
    assert "Show alerts and activity" in show, (
        "the control that brings it back does not say what it shows")
    assert 'id="activity-close"' not in html, (
        "the old bare x control is still there, so there are two controls")
    assert 'id="activity-reopen"' not in html, (
        "the old edge tab is still there, so there are two controls")


def test_the_folded_panel_leaves_the_width_to_the_board_columns():
    assert "#activity.folded { display: none; }" in _style_css(), (
        "a folded panel still takes its 270 px of the screen")


def test_the_ten_second_refresh_cannot_unfold_the_panel():
    """The board redraws every 10 seconds. The redraw may update the count
    on the control; it may never put the panel back on screen."""
    js = _app_js()
    for name in ("async function load(", "async function renderEvents(",
                 "async function renderAlerts("):
        rest = js.split(name)[1]
        body = rest.split(NL + "function ")[0].split(NL + "async function ")[0]
        assert "setActivityFold(" not in body, (
            name.strip() + " folds or unfolds the panel on every refresh")
    reads = [ln for ln in js.splitlines()
             if '"pf-activity-open"' in ln and "pfRead" in ln]
    assert len(reads) == 1, (
        "whether the panel is folded is read from the browser more than once")


def test_the_control_says_what_arrived_while_the_panel_was_folded():
    assert 'id="activity-new"' in _index_html(), (
        "the control has nowhere to say what arrived")
    js = _app_js()
    assert "function noteRows(" in js, "nothing counts what arrived"
    assert 'noteRows("ev"' in js and 'noteRows("al"' in js, (
        "activity rows and alerts are not both counted")
    assert '" new)"' in js, "the count is never said on the control"


def test_both_fold_controls_are_reachable_from_the_keyboard():
    html = _index_html()
    for cid in ('id="activity-fold"', 'id="activity-show"'):
        tag = html.split(cid)[0].rsplit("<", 1)[1]
        assert tag.startswith("button"), cid + " is not a button, so Tab skips it"
        attrs = html.split(cid)[1].split(">")[0]
        assert "aria-expanded" in attrs and "aria-controls" in attrs, (
            cid + " does not tell a screen reader what it opens")
    js = _app_js()
    assert "show.focus()" in js and "fold.focus()" in js, (
        "folding leaves the keyboard on a control that is no longer on screen")


def test_the_fold_state_survives_a_browser_that_refuses_storage():
    init = _app_js().split("function initActivityFold(")[1].split("})();")[0]
    assert "pfRead(" in init and "localStorage" not in init, (
        "the fold state is read straight from storage, which throws when it "
        "is refused")
