---
name: apb-loop
description: Run the build loop unattended over several board cards (worktrees / Agent Teams). Use for overnight or multi-feature runs; apb-build is the one-card interactive version.
---
1. Confirm the kickoff names: which cards, the halt conditions (all accepted / N stuck iterations / max iterations), and where questions go (`QUESTIONS.md`).
2. Launch real background workers — one worktree per feature, builder-agent rules apply. Name the mechanism in your confirmation; a status message is not a launch.
3. Workers append one `LOG.md` line per iteration (single writer) and raise questions in `QUESTIONS.md` (append-only; NEEDS-HUMAN items get surfaced in chat).
4. Liveness is commit age: no commit in ~45–60 min = stalled; whatever has relaunch authority checks commit age, not process state.
5. PRs queue for review as usual — unattended building never means unattended merging.
