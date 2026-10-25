---
name: apb-board
description: Turn an approved TDD's work pieces into GitHub Issues on a Projects board. Use only after TDD gate 2 is approved.
---
1. One issue per TDD work piece: title, acceptance criteria copied from the PRD/TDD (not paraphrased), dependency notes.
2. Add all issues to the repo's Projects board with columns: Todo / In progress / In review / Done. Create the board if missing (setup skill covers it).
3. The board is the single source of truth for progress — no separate ROADMAP file. PRs reference their issue so merges move cards automatically.
