# Repo rules (apb)
## Who does what
- **Orchestrator** — the main Claude Code session, the one the human talks to. It drafts the PRD and TDD, keeps the board, and runs the build loop: take the top card → spawn the builder on it → when the PR is open, spawn the reviewer → hand the PR to the human → after the merge, check the live product. It does not write feature code itself and never merges.
- **Builder** — a subagent the orchestrator spawns (`agents/builder.md` in the apb plugin). Writes the tests, then the code, opens one flat PR. Starts with no memory of the chat.
- **Reviewer** — a subagent the orchestrator spawns in a fresh context (`agents/reviewer.md`). Reviews the PR and the live product; writes no code.
- **Advisor** — optional; a Claude session outside the repo (Cowork) with broad access: thinking through the idea, research, independent reads. Never writes to the repo; its input is a comment on the relevant issue.
- **Human** — approves the three gates and merges.
## Rules
- Tests before code; one feature per flat PR off freshly-fetched main; never stack branches or push to a merged branch.
- Only humans merge. The reviewer is a separate session; it writes no code.
- After every merge, check the live product (the site, the published package, the released app), not just CI.
- The Projects board is the single progress tracker; PRs reference their issue.
- When something goes wrong, add a rule here the same day.
- Approved PRD/TDD wording is immutable except by the human's explicit choice.
- Permissions: sessions run in auto mode (set in each human's `~/.claude/settings.json`; never put `defaultMode` in a repo file). At session start the orchestrator confirms the mode is auto (desktop: `get_session` "self"); if not, it tells the human in one line before anything else. One plain command per shell call: no `$(…)`, loops or `curl | grep` chains. Use WebFetch for web pages and a script file for multi-step logic. Every instruction to a builder, reviewer or research Claude repeats this command rule, since their permission prompts reach the human too. If a permission prompt appears mid-session, add the missing rule to `.claude/settings.json` the same day.
- `LOG.md` is append-only. A question for a human or the advisor goes on the card's issue — nothing important lives only in chat.
- Working with the human: follow the sections "How the human reviews" and "Helping the human keep track" in the playbook (`docs/playbook.md` in the apb plugin). In short: one question at a time, in plain chat; only "approved" is approval; show everything visual; one link per thing.
- When you add a new piece (a page, a folder, a scheduled task, an outside account), add it to `docs/MAP.md` at the same time. After each stage, add an entry to `docs/STORY.md`: the stage's decisions, surprises and changes of mind.
- Before showing the human a markdown doc, check how GitHub displays it: `gh api markdown -F mode=gfm -F text=@<file>`.
- In a public repo, never name anyone who isn't on the repo.
- Claude uses the human's GitHub login, so GitHub can't tell who did what. End every PR description and comment Claude writes with its role, for example `— Claude (orchestrator)`. Skip this once Claude has its own GitHub account.
- Problems with the apb process itself go on hornof/apb as one issue per problem, labeled `from-` followed by this repo's name.
## Project rules
(none yet; add one line per rule when something goes wrong)
