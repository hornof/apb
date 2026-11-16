---
name: apb-review
description: Run the reviewer role on any PR, code or docs, or on the live product. Use on every PR before the human sees it. Must run in a session that did not write the work under review.
---
Every PR gets one review before the human sees it: code PRs, docs-only PRs, setup fixes, the PRD and the TDD alike.

1. Launch the plugin's `reviewer` agent in a fresh context, separate from whatever wrote the PR. Never review in-session work yourself. Tell it the PR number and whether the PR holds code, docs or both.
2. The reviewer reads `docs/reviewer-notes.md`, reviews once, and posts every item on the PR in three buckets: Fix, Note and Decision (see `agents/reviewer.md`).
3. Have the builder fix every Fix item straight away. If the PR had no builder (a PRD, TDD or docs PR the orchestrator wrote), the orchestrator makes the fixes. Check each fix yourself, then reply on its item with "fixed in commit X" or "not fixed, because …". Don't run the reviewer a second time.
4. Hand the PR to the human. They skim the Notes and rule on the Decisions. Wait for each Decision: a clear answer, or its thread resolved with no reply, which accepts the recommendation. A reply that is a question is not an answer.
5. Add each override, and each new rule the human sets, as one line in `docs/reviewer-notes.md`, with the PR it came from.
