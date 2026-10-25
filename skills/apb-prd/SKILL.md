---
name: apb-prd
description: Turn a stakeholder's brain dump into a one-page PRD with checkable acceptance criteria. Use when starting a new project or feature set.
---
Input: the stakeholder's raw description (any form). Output: `docs/PRD.md` in the repo, from `templates/PRD-template.md`, ONE page.

1. Draft all five sections. Every feature gets an acceptance criterion a machine or a glance can check ("loads under 1s", "works at phone width") — never "works well".
2. Anything the dump doesn't answer goes in Open Questions — never invent or assume an answer.
3. Deliver for comment; iterate until the stakeholder says approved (silence is not approval). Record approval date in the doc header. That is gate 1 — no TDD work before it.
