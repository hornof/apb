---
name: apb-review
description: Run the reviewer role on any PR, code or docs, or on the live product. Use on every PR before the human sees it. Must run in a session that did not write the work under review.
---
Every PR gets one review before the human sees it: code PRs, docs-only PRs, setup fixes, the PRD and the TDD alike.

1. Launch the plugin's `reviewer` agent in a fresh context, separate from whatever wrote the PR. Never review in-session work yourself. The brief names the PR, whether it holds code, docs or both, and the reviewer's own scratch folder, named after the PR (for example `scratchpad/pr-27/`). It says to keep every file it writes outside the repo in that folder. It says nothing about format: the format is in `agents/reviewer.md`. The plugin's hook refuses a brief that does (words like "as a single comment", "post as", "format", "leave out items", "one thread", "no index"). If it refuses yours, take those words out and launch the reviewer again.
2. The reviewer reads `docs/reviewer-notes.md`, reviews once, and posts the review in three buckets: Fix, Note and Decision (see "How to post the review" in `agents/reviewer.md`).
3. Have the builder fix every Fix item straight away. If the PR had no builder (a PRD, TDD or docs PR the orchestrator wrote), the orchestrator makes the fixes. Check each fix yourself, then add its commit to the Fix post, or say there why it wasn't fixed. Don't run the reviewer a second time.
4. Check before the hand-off. If anything below fails, fix it first; the human sees nothing until it passes.
   - The review is in the format of `agents/reviewer.md`: an index at the top with the count per bucket and the Decision numbers, one post for the Fix items, Notes in one post or a section of the Fix post, and one thread per Decision. If not, fix the review.
   - Every Decision thread has the shape in `agents/reviewer.md`: the question in one sentence, lettered options, `Recommended: <letter>` and `Resolve with no reply = <letter>`. Check with `python3 <apb plugin folder>/scripts/decision-check.py N` (the apb plugin folder is two folders above this skill's base directory); it exits with 1 and names what's missing if a thread lacks a part. Have the reviewer rewrite each such thread, then run the check again.
   - If the reviewer's or the builder's report says it departed from its instructions, stop and put it right. The plugin's hook catches "departed from" and "as you asked, instead of" in a report, and sends back every hand-off until you run `python3 <apb plugin folder>/hooks/apb-brief.py resolve "<what you did>"` from the project's root.
   - If the PR description says "Part of #N", the PR does only part of a card. Stop: split that part into its own card, and change the PR to close the new card.
   - If the PR changes code, start the full test run once the fixes are pushed: `gh pr edit N --add-label full-tests`. Hand the PR over when it passes. If it fails, have the builder fix it, and hand the PR over once the run passes. Every later push to the PR runs the full set again.
5. Hand the PR to the human in one line, in the forms of the playbook's "The human and Claude are never both idle at once", with the PR as one link.
   - No Decisions: "1 thing for you to do right now: merge PR #N." If the PR changes how the product looks, add the preview link: `1 thing for you to do right now: merge PR #N. Preview: <link>`. The merge accepts the Fix items. The human opens the PR only if they want to.
   - Decisions: "1 thing for you to do right now: 2 decisions on PR #N." The human rules on each one: resolving its thread with no comment accepts the recommendation. A comment on the thread is for you to read and act on. If the human resolves a thread that had nothing to accept, reopen it and ask.
   - Two PRs ready at once: "2 things for you to do right now:" and a numbered list, one PR per line.
   - Say the hand-off once, when you hand the PR over, and again only when it changes, for example when a second PR is ready. Answer any question in the meantime with no first line.
   - Don't repeat the review items in chat; they are on the PR.
   - Alert the human once, in the same turn, with or without Decisions (see the playbook's "Alert only when it's the human's turn"). Never alert for progress.
6. Send the builder to implement a Decision only once it is answered. Run `python3 <apb plugin folder>/scripts/decision-check.py N` first. A Decision is answered when its thread is resolved, or when the human's last reply on it names an option letter.
   - Three or more replies from the human with no letter make a design discussion, not a decision. Stop and say so in one line. Move the question to the card, or to chat ending with "Waiting for your go".
   - A thread resolved with no letter to accept: reopen it and ask, as in step 5.
7. Add each override, and each new rule the human sets, as one line in `docs/reviewer-notes.md`, with the PR it came from.
