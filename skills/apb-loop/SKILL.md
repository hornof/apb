---
name: apb-loop
description: Run the build loop unattended over several board cards (worktrees / Agent Teams). Use for overnight or multi-feature runs; apb-build is the interactive version, with the human watching.
---
1. Confirm the kickoff names: which cards, the halt conditions (all accepted / N stuck iterations / max iterations), and that questions go on the card's issue.
2. Launch real background workers — one worktree and one scratch folder per feature, builder-agent rules apply. Each scratch folder is named after its issue (for example `scratchpad/issue-14/`), and the worker keeps every file it writes outside the repo there. Name the mechanism in your confirmation; a status message is not a launch. Start an issue only when every issue it needs is merged. Run issues at the same time only when their "Can be built at the same time as" lines name each other.
3. Workers append one `LOG.md` line per iteration (single writer) and raise questions as comments on the card's issue (ones that need a human get surfaced in chat too).
   - When a question on the card, or a PR, waits on the human, assign the card's issue to them, and the PR too: `gh issue edit N --add-assignee <login>` and `gh pr edit M --add-assignee <login>`. When the human answers or merges, unassign them in the turn you notice, with `--remove-assignee`. See the playbook's "Assign the card to the human while it waits on them".
   - On every status check, run `python3 <apb plugin folder>/scripts/human-turn.py` from the project's root and clear each stale flag with the command it prints. The apb plugin folder is two folders above this skill's base directory.
4. Liveness is commit age: no commit in ~45–60 min = stalled; whatever has relaunch authority checks commit age, not process state.
5. PRs queue for review as usual — unattended building never means unattended merging.
