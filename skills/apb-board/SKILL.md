---
name: apb-board
description: Turn an approved TDD's work pieces into GitHub Issues on a Projects board. Use only after TDD gate 2 is approved.
---
1. One issue per TDD work piece: title, acceptance criteria copied from the PRD/TDD (not paraphrased), and what the human will look at on the PR.
   - Copy the dependencies from the TDD into each issue: "Needs #N merged first", or "Needs nothing".
   - Name the issues it can be built at the same time as.
   - If the TDD marks the piece as a matter of taste, say so on the issue: "Early look: post the preview link here once a first version is up."
2. Add all issues to the repo's Projects board with columns: Todo / In progress / In review / Done. Create the board if missing (setup skill covers it).
3. The board is the single source of truth for progress — no separate ROADMAP file. PRs reference their issue so merges move cards automatically.
