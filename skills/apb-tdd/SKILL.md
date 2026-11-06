---
name: apb-tdd
description: Turn an approved PRD into a technical design doc (TDD) — architecture, test plan, PR-sized work pieces. Use only after PRD gate 1 is approved.
---
Input: the approved PRD. Output: `docs/TDD.md` from `templates/TDD-template.md`.

1. Choose the simplest stack that meets the PRD; name real infrastructure decisions explicitly (stores, hosting, CI) — "filesystem and dicts" by default is a known failure.
2. Test plan maps 1:1 to the PRD's acceptance criteria.
3. Break work into PR-sized pieces (each shippable alone, ordered by dependency).
4. Deliver for comment; iterate to explicit approval — gate 2. A reviewer session should read it against the PRD before the human gates it.
5. Once the TDD is approved, set up what it named, before any build card starts: (a) CI — a workflow that runs the TDD's test suite on every PR and push to main, opened as a PR for the human to merge; (b) hosting — connect the repo to the host the TDD chose so main deploys and PRs get preview URLs. Do the parts that have a command line yourself; if the host needs a dashboard step a human must click, say exactly which step and why, and stop there.
