# What I stole

No outside code is included in this repository. Everything here is our own
code under the MIT licence (see `LICENSE`). These are the ideas it borrows.

| Idea | Where it comes from | Licence / status | What we took |
|---|---|---|---|
| The board itself | Ashley's own ProjectForge, built 2026-06-12 to 2026-07-19 | Our code, re-released here under MIT | The engine, viewer, orchestrator loop, health check and agents' tool, cleaned of personal data and paths |
| Columns and cards | Kanban boards, as popularised by Trello | An idea, not code | Columns, drag and drop, checklists, tags |
| Only one writer moves the state | The "single writer" principle from database and distributed-systems design | An idea, not code | Only the orchestrator (or you) may move a card |
| Typed messages (INFORM, PROPOSE, REFUSE...) | FIPA Agent Communication Language, a 1990s-2000s standard for software agents | A published standard; we reuse the word list only | The intent labels on work reports and handovers |
| The 5-field handover | Shift-handover practice in hospitals and operations teams; Ashley's ruling of 2026-07-19 | An idea, not code | Done, decisions and why, state, first step, warnings |
| Run only when there is work | The "check before you wake the expensive thing" pattern | An idea, not code | `tools/run_if_ready.py` counts ready cards with no AI before starting Claude |
| Hidden launcher on Windows | Windows Script Host `WScript.Shell.Run` with window style 0 | Part of Windows | The optional schedule never shows a window |
| Obsidian links | Obsidian's `obsidian://open?path=` link format | Documented by Obsidian | One-click open of a CRM person note from a card |

Libraries used at run time: the Python standard library only. The tests use
pytest (MIT). The guide's screenshots were taken with Playwright (Apache 2.0),
which is not needed to run the board.
