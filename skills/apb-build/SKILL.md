---
name: apb-build
description: Build the next unblocked issues from the project board, tests-first, one flat PR each. Use during the build loop, after gates 1 and 2.
---
1. Take the top unblocked issue from the board's Todo column; move its card to In progress. An issue is unblocked only when every issue it needs is merged.
2. Launch the plugin's `builder` agent on it (tests from the acceptance criteria first, then code until green, one flat PR referencing the issue). The brief gives the builder its own scratch folder, named after its issue (for example `scratchpad/issue-14/`), and says to keep every file it writes outside the repo in that folder. Agents running at the same time otherwise overwrite each other's files, such as a PR description.
3. Build independent issues in parallel. If other unblocked issues can be built at the same time (its "Can be built at the same time as" line names them), start a builder on each one now.
   - Give each builder its own git worktree, made from freshly fetched main.
   - Don't start an issue early, even if its dependency's PR is open. Wait for the merge.
4. Once per project, on the first build PR with real logic, tell its builder to run the deliberate-bug check (see the playbook's "Testing and models"). Builders don't run it otherwise.
5. For an issue marked "Early look", the builder posts the preview link on the issue as soon as a first version is up. Don't stop the build to wait for the human. If they comment, the builder takes it into account.
6. Before the builder opens the PR, check that it finishes the whole card. A PR closes exactly the cards it finishes, with "Closes #N". If it will do only part of a card, split that part into its own card first, and have the PR close the new card.
7. When the PR is open, move the card to In review and run `apb-review`. It runs one review round, and the builder fixes the Fix items before the human sees the PR.
8. If an agent's report says it departed from its instructions, stop and put it right before the human sees anything.
9. Hand the PR over as `apb-review` says. When there are Decisions, also point to the PR's "What to look at" section. The human may have watched along the way, so don't present the PR as new. If the issue had an early look, say what changed since then.
10. After the merge, the card moves to Done on its own: "Closes #N" closes the issue, and the board's "Item closed" automation moves the card. Nobody moves a card to Done by hand. If the card hasn't moved, find out why the issue didn't close.
