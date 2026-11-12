---
name: apb-prd
description: Turn a stakeholder's brain dump into a PRD with checkable acceptance criteria, mockups, and supporting files. Use when starting a new project or feature set.
---
Input: the stakeholder's brain dump (the brain-dump issue). Output: `docs/PRD.md` from `templates/PRD-template.md`, a mockup canvas, and supporting files in `docs/`, opened as one PR.

1. **Read the brain dump for who it's for.** Who is the real stakeholder? Is this a trial run or stand-in for someone else? If the dump doesn't say, ask before drafting. Never name a third party in a public repo.
2. **Draft every section of the template.** Every requirement gets a check a machine or a glance can verify ("loads under 1 s", "works at 375 px"), never "works well". User journeys cover the owner/admin too: their commands or screens are product, not engineering. Anything the dump doesn't answer becomes an open question with your recommended answer. Never invent an answer.
3. **Make mockups before review.** Build the main screens on a Claude Design canvas (clickable where interaction matters) and link it from the PRD. Words alone let a build match the document but not what the human pictured.
4. **Keep the PRD short; put detail in `docs/`.** Long lists, research and comparisons go in supporting files the PRD links to. Never rely on private links (artifacts, chats) as the record: the repo must stand alone.
5. **Render-check before every handoff:** `gh api markdown -F mode=gfm -F text=@docs/PRD.md -F context=<owner>/<repo>`. Look for broken lists (a list starting at 11 right after a text line becomes one paragraph) and `#N` auto-linking to the wrong repo.
6. **Hand off for review with the review template** (see the playbook, "Gates: how the human reviews"): one surface (the PR), exact clicks, open questions with recommended answers. Run the handoff checklist first.
7. **Iterate.** Answers given in chat are posted on the PR word for word. Each decision is folded into the right section, not left as a Q&A log; section 11 keeps a one-line index.
8. **Approval is explicit.** Only words like "approved" or "merge it" pass the gate. "Looks good" isn't approval; if unsure, ask. Then: export the mockups as PNGs into `docs/mockups/` with a README, set the status line to APPROVED with name and date, add the gate to `docs/STORY.md`, update the project map, and hand the PR over to merge. Merging is gate 1; no TDD work before it.
