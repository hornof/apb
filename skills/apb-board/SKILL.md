---
name: apb-board
description: Turn an approved TDD's work pieces into GitHub Issues on a Projects board. Use only after TDD gate 2 is approved.
---
1. One issue per TDD work piece: title, acceptance criteria copied from the PRD/TDD (not paraphrased), and what the human will look at on the PR.
   - Copy the dependencies from the TDD into each issue: "Needs #N merged first", or "Needs nothing".
   - Name the issues it can be built at the same time as: "Can be built at the same time as #N, #M", or "Can be built at the same time as: none".
   - Create all the issues first, then add the Needs and Can be built at the same time lines, using the real issue numbers.
   - If the TDD marks the piece "Early look", add this line to the issue: "Early look: post the preview link here once a first version is up."
2. Add all issues to the repo's Projects board with columns: Todo / In progress / In review / Done. Create the board if missing (setup skill covers it). Use the same board that holds the Brain dump, PRD and TDD issues; a project has one board, never more.
   - Check that the board's "Item closed" automation is on (the board's Workflows menu). Turn it on if it isn't.
3. The board is the single source of truth for progress — no separate ROADMAP file.
   - A card moves to Done only when GitHub closes its issue. "Closes #N" in a merged PR closes it, and the "Item closed" automation moves the card. Nobody moves a card to Done by hand.
   - A PR closes exactly the cards it finishes. If a PR will do only part of a card, split that part into its own card before the PR opens.
