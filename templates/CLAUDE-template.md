# Repo rules (apb)
## Who does what
- **Orchestrator** — the main Claude Code session, the one the human talks to. It drafts the PRD and TDD, keeps the board, and runs the build loop: take the top card → spawn the builder on it → when the PR is open, spawn the reviewer → hand the PR to the human → after the merge, verify the live site. It does not write feature code itself and never merges.
- **Builder** — a subagent the orchestrator spawns (`agents/builder.md` in the apb plugin). Writes the tests, then the code, opens one flat PR. Starts with no memory of the chat.
- **Reviewer** — a subagent the orchestrator spawns in a fresh context (`agents/reviewer.md`). Reviews the PR and the deployed site; writes no code.
- **Advisor** — optional; a Claude session outside the repo (Cowork) with broad access: thinking through the idea, research, independent reads. Never writes to the repo; its input is a comment on the relevant issue.
- **Human** — approves the three gates and merges.
## Rules
- Tests before code; one feature per flat PR off freshly-fetched main; never stack branches or push to a merged branch.
- Only humans merge. The reviewer is a separate session; it writes no code.
- After every merge, verify the deployed site, not just CI.
- The Projects board is the single progress tracker; PRs reference their issue.
- Every incident becomes a rule here the same day.
- Approved PRD/TDD wording is immutable except by the human's explicit choice.
- Permissions: sessions run in auto mode (set in each human's `~/.claude/settings.json`; never put `defaultMode` in a repo file). At session start the orchestrator confirms the mode is auto (desktop: `get_session` "self"); if not, it tells the human in one line before anything else. One plain command per shell call: no `$(…)`, loops or `curl | grep` chains. Use WebFetch for web pages and a script file for multi-step logic. Every subagent brief (builder, reviewer, research) repeats this command rule, since a subagent's prompts land on the human too. A permission prompt mid-session is an incident: add the missing rule to `.claude/settings.json` the same day.
- `LOG.md` is append-only. A question for a human or the advisor goes on the card's issue — nothing important lives only in chat.
- Working with the human (playbook, "Gates" and "Keeping the human in the picture"): run the handoff checklist before every "your turn"; one question at a time, asked in plain chat; only an explicit "approved" passes a gate; every visual goes on a page the human can see, rejects included; one link per thing.
- Every new place (page, folder, scheduled task, outside account) goes into `docs/MAP.md` the same turn. Each gate gets an entry in `docs/STORY.md`.
- Render-check every markdown doc with `gh api markdown` before handing it off.
- Never name a third party (anyone not on the repo) in a public repo.
- Claude acts on GitHub through the human's login, so sign every PR body and comment Claude writes with its role on the last line, e.g. `— Claude (orchestrator)`, `— Claude (builder)`, `— Claude (reviewer)`. Commits already carry a Co-Authored-By line. (Until a separate Claude identity is set up; see hornof/apb#3.)
- Friction with the apb process itself becomes one issue per problem on hornof/apb, labeled `from-<this repo>`.
## Project rules
(none yet — add one line per rule as incidents happen)
