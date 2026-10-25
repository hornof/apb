---
name: apb-build
description: Build the next feature from the project board, tests-first, as one flat PR. Use during the build loop, after gates 1 and 2.
---
1. Take the top unblocked issue from the board's Todo column; move its card to In progress.
2. Launch the plugin's `builder` agent on it (tests from the acceptance criteria first, then code until green, one flat PR referencing the issue).
3. When the PR is open, move the card to In review and run `apb-review`.
