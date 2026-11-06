# Repo rules (apb)
## Who does what
- **Orchestrator** — the main Claude Code session, the one the human talks to. It drafts the PRD and TDD, keeps the board, and runs the build loop: take the top card → spawn the builder on it → when the PR is open, spawn the reviewer → hand the PR to the human → after the merge, verify the live site. It does not write feature code itself and never merges.
- **Builder** — a subagent the orchestrator spawns (`agents/builder.md` in the apb plugin). Writes the tests, then the code, opens one flat PR. Starts with no memory of the chat.
- **Reviewer** — a subagent the orchestrator spawns in a fresh context (`agents/reviewer.md`). Reviews the PR and the deployed site; writes no code.
- **Navigator** — a Claude session outside the repo (Cowork) with broad access: research, drafting, reading. Read-only here except appending to `MAILBOX.md`.
- **Human** — approves the three gates and merges.
## Rules
- Tests before code; one feature per flat PR off freshly-fetched main; never stack branches or push to a merged branch.
- Only humans merge. The reviewer is a separate session; it writes no code.
- After every merge, verify the deployed site, not just CI.
- The Projects board is the single progress tracker; PRs reference their issue.
- Every incident becomes a rule here the same day.
- Approved PRD/TDD wording is immutable except by the human's explicit choice.
- `LOG.md` is append-only; `MAILBOX.md` is the agent mailbox — nothing important lives only in chat.
## Project rules
(none yet — add one line per rule as incidents happen)
