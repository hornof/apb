---
name: apb-board
description: Turn an approved TDD's work pieces into GitHub Issues on a Projects board. Use only after TDD gate 2 is approved.
---
1. One issue per TDD work piece: title, acceptance criteria copied from the PRD/TDD (not paraphrased), and what the human will look at on the PR.
   - Copy the dependencies from the TDD into each issue: "Needs #N merged first", or "Needs nothing".
   - Name the issues it can be built at the same time as: "Can be built at the same time as #N, #M", or "Can be built at the same time as: none".
   - Create all the issues first, then add the Needs and Can be built at the same time lines, using the real issue numbers.
   - If the TDD marks the piece "Early look", add this line to the issue: "Early look: post the preview link here once a first version is up."
   - Label every build issue `build` (create the label first if it's missing: `gh label create build --force`). The map artifact counts the build issues by this label, to show "7 of 9 cards done" for the build stage. To drop a card, close its issue as "not planned"; it then counts in neither number. Only build issues get it; the PRD input, PRD and TDD issues don't.
   - Create the `backlog` label too: `gh label create backlog --force`. Ideas that come up later but don't belong in the current work become issues with this label, off the board, until one is chosen and becomes a build card (`docs/product-guide.md`).
2. Add all issues to the repo's Projects board with columns: Todo / In progress / In review / Done. Create the board if missing (setup skill covers it). Use the same board that holds the PRD input, PRD and TDD issues; a project has one board, never more.
   - Add the cards to Todo oldest first, so the first card to build is on top. Todo stays in the order the cards will be built in, with the next card on top.
   - Check that the board's "Item closed" automation is on. It isn't always on by default, and GitHub's API can't turn it on. In the browser, on the board: Workflows → Item closed → Edit → set Status to Done → Save and turn on. The orchestrator can do this itself.
3. The board is the single source of truth for progress — no separate ROADMAP file.
   - A card moves to Done only when GitHub closes its issue. "Closes #N" in a merged PR closes it, and the "Item closed" automation moves the card. Nobody moves a card to Done by hand.
   - A PR closes exactly the cards it finishes. If a PR will do only part of a card, split that part into its own card before the PR opens.
