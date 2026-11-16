---
name: apb-build
description: Build the next feature from the project board, tests-first, as one flat PR. Use during the build loop, after gates 1 and 2.
---
1. Take the top unblocked issue from the board's Todo column; move its card to In progress. An issue is unblocked only when every issue it needs is merged.
2. Launch the plugin's `builder` agent on it (tests from the acceptance criteria first, then code until green, one flat PR referencing the issue).
3. Build independent issues in parallel. If other unblocked issues can be built at the same time (the issue says so), start a builder on each one now.
   - Give each builder its own git worktree, made from freshly fetched main.
   - Give each builder its own scratch folder.
   - Don't start an issue early, even if its dependency's PR is open. Wait for the merge.
4. For an issue marked "Early look", the builder posts the preview link on the issue as soon as a first version is up. Don't stop the build to wait for the human. If they comment, the builder takes it into account.
5. When the PR is open, move the card to In review and run `apb-review`.
6. When you hand the PR to the human, point to its "What to look at" section. If the human may have watched the preview along the way, don't present it as new. Say what changed since the early look.
