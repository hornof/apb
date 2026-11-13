---
name: apb-tdd
description: Turn an approved PRD into a technical design doc (TDD) — architecture, test plan, PR-sized work pieces. Use only after PRD gate 1 is approved.
---
Input: the approved PRD. Output: `docs/TDD.md` from `templates/TDD-template.md`.

1. Design the system with `docs/system-design-guide.md`. Choose the simplest stack that meets the PRD. Name the real hosting, CI and the store for each kind of data. Don't default to local files or in-memory data: hosts often wipe both on every deploy.
2. Test plan maps 1:1 to the PRD's acceptance criteria.
3. Break work into PR-sized pieces (each shippable alone, ordered by dependency).
4. Get it reviewed, the same way as the PRD:
   - First, a second Claude that didn't write the TDD checks it against the PRD.
   - Then the human reviews the PR, following the steps in the playbook's "How the human reviews" section.
   - Only "approved" counts as approval. "Looks good" doesn't.
   - After approval: mark the TDD approved at the top, add a line to `docs/STORY.md`, update `docs/MAP.md`, and ask the human to merge. Merging is gate 2.
5. Once the TDD is approved, set up what it named, before any build card starts: (a) CI — a workflow that runs the TDD's test suite on every PR and push to main, opened as a PR for the human to merge; (b) hosting — connect the repo to the host the TDD chose so main deploys and PRs get preview URLs. Do the parts that have a command line yourself; if the host needs a dashboard step a human must click, say exactly which step and why, and stop there.
