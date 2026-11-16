---
name: apb-review
description: Run the reviewer role on any PR, code or docs, or on the live product. Use on every PR before the human sees it. Must run in a session that did not write the work under review.
---
Every PR gets one review before the human sees it: code PRs, docs-only PRs, setup fixes, the PRD and the TDD alike.

1. Launch the plugin's `reviewer` agent in a fresh context, separate from whatever wrote the PR. Never review in-session work yourself. The brief names the PR and whether it holds code, docs or both, and nothing about format: the format is in `agents/reviewer.md`.
2. The reviewer reads `docs/reviewer-notes.md`, reviews once, and posts the review in three buckets: Fix, Note and Decision (see "How to post the review" in `agents/reviewer.md`).
3. Have the builder fix every Fix item straight away. If the PR had no builder (a PRD, TDD or docs PR the orchestrator wrote), the orchestrator makes the fixes. Check each fix yourself, then add its commit to the Fix post, or say there why it wasn't fixed. Don't run the reviewer a second time.
4. Check before the hand-off. If anything below fails, fix it first; the human sees nothing until it passes.
   - The review is in the format of `agents/reviewer.md`: an index at the top with the count per bucket and the Decision numbers, one post for the Fix items, Notes in one post or a section of the Fix post, and one thread per Decision. If not, fix the review.
   - If the reviewer's or the builder's report says it departed from its instructions, stop and put it right.
   - If the PR description says "Part of #N", the PR does only part of a card. Stop: split that part into its own card, and change the PR to close the new card.
5. Hand the PR to the human.
   - No Decisions: the hand-off is one line, "PR #N: nothing for you to decide, merge-ready." If the PR changes how the product looks, add the preview link: `PR #N: nothing for you to decide, merge-ready. Preview: <link>`. The merge accepts the Fix items. The human opens the PR only if they want to.
   - Decisions: say how many and which, with the PR link. The human rules on each one: resolving its thread with no comment accepts the recommendation. A comment on the thread is for you to read and act on.
   - If the project alerts the human when it's their turn, alert only when there is a Decision.
6. Add each override, and each new rule the human sets, as one line in `docs/reviewer-notes.md`, with the PR it came from.
