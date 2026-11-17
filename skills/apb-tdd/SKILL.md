---
name: apb-tdd
description: Turn an approved PRD into a technical design doc (TDD) — architecture, test plan, PR-sized work pieces. Use only after PRD gate 1 is approved.
---
Input: the approved PRD. Output: `docs/TDD.md` from `templates/TDD-template.md`.

Before step 1: open an issue called "TDD" and add it to the project's board in In progress. Move the card to the top of In progress (see apb-build step 1). The TDD's PR description says "Closes #N" for the TDD issue, so the merge at gate 2 moves it to Done.

1. Design the system with `docs/system-design-guide.md`. Choose the simplest stack that meets the PRD. Name the real hosting, CI and the store for each kind of data. Don't default to local files or in-memory data: hosts often wipe both on every deploy.
2. Test plan maps 1:1 to the PRD's acceptance criteria. Mark the tests that depend on screen width. Those run at every width; the rest run at one width. Every child process the code or tests start has a time limit (for example `timeout` on `spawnSync`, `execSync` or `subprocess.run`), so one stuck child can't stall a whole run.
3. Break work into PR-sized pieces (each shippable alone, ordered by dependency).
   - For each piece, name the pieces that must be merged before it starts.
   - Say which pieces can be built at the same time. They must touch different code.
   - Mark the pieces whose result is mostly a matter of taste, such as the look or how something feels, with "Early look". The human looks at these during the build.
   - For each piece, say what the human will look at on its PR: usually the preview link and one check that takes a minute or two. If there is no preview, name a command to try instead.
4. Get it reviewed, the same way as the PRD:
   - First, run `apb-review`: the reviewer checks the TDD against the PRD.
   - Then the human reviews the PR, following the steps in the playbook's "How the human reviews" section.
   - Get the human's yes before the merge. A clear yes is approval; praise that comes with open questions is not. Ask only when it's genuinely unclear.
   - At gates 1 and 2, the hand-off is "One thing for you right now: read PR #N and say yes, or what to change". Ask for the merge after the post-approval updates are in.
   - After the yes, in the same PR: mark the TDD approved at the top, add a line to `docs/STORY.md`, update `docs/MAP.md`, and ask the human to merge. Merging is gate 2.
   - If the human merges first, the merge is the approval: don't ask again, and make these updates in a follow-up PR.
5. Once the TDD is approved, set up what it named, before any build card starts: (a) CI — a workflow that runs the TDD's whole test suite (every browser, and every width for the width-specific tests) on every PR and push to main, opened as a PR for the human to merge. It skips the browser tests when a push changes only docs (`LOG.md`, `README.md`, `docs/`); (b) hosting — connect the repo to the host the TDD chose so main deploys and PRs get preview URLs. Do the parts that have a command line yourself; if the host needs a dashboard step a human must click, say exactly which step and why, and stop there.
6. Fill in the "Using it" section of README.md: how to install, run or reach the product (for example, the live address, an install command, or where to download the app). Keep it current after every merge that changes how the product is used.
7. Prove the setup works before any build item starts. Send one tiny change through every step: open a PR, let the automatic tests run, have the reviewer check it, ask the human to merge it, then check that it went live (deployed or published). The README change from step 6 can be this change. If any step fails, fix it now: it's far cheaper to find a broken deploy or a permission prompt on a one-line change than on the first real feature.
