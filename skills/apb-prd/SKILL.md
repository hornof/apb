---
name: apb-prd
description: Turn a stakeholder's brain dump into a PRD with checkable requirements, mockups and supporting docs. Use when starting a new project or feature set.
---
Input: the brain-dump issue. Output: one PR with `docs/PRD.md` (from `templates/PRD-template.md`), mockups, and any supporting docs in `docs/`.

1. **Find out who it's for.** Who is the real stakeholder? Is this a trial run for someone else? If the brain dump doesn't say, ask before writing anything. Never name someone who isn't on the repo in a public repo.
2. **Fill in every section of the template.**
   - Every requirement ends with a check you can test or see: "loads in under 1 second", "works on a 375 px phone screen". Never "works well".
   - Include the owner's journeys too. The owner's tools and commands are part of the product.
   - If the brain dump doesn't answer something, make it an open question with your recommended answer. Don't guess.
3. **Make mockups before the review.** Draw the main screens on a Claude Design canvas, clickable where it matters, and link it from the PRD. Without pictures, a build can match the words but not what the human imagined.
4. **Keep the PRD short.** Long lists, research and comparisons go in separate files in `docs/`, linked from the PRD. Private links (Claude pages, chats) can't be the only record, because other people can't open them.
5. **Check how GitHub displays it** before showing it to the human: `gh api markdown -F mode=gfm -F text=@docs/PRD.md -F context=<owner>/<repo>`. Look for broken numbered lists and for `#5`-style links pointing to the wrong repo.
6. **Ask for the review** as described in the playbook's "How the human reviews" section.
7. **Update the PRD as answers come in.** If the human answers in chat, copy the answer to the PR. Put each decision where it belongs in the PRD, and add a one-line entry to section 11.
8. **Wait for "approved".** "Looks good" isn't approval; if unsure, ask. After approval:
   - Export each mockup screen as a PNG into `docs/mockups/`, with a short README.
   - Change the status line at the top to APPROVED, with the name and date.
   - Add a line to `docs/STORY.md` and update `docs/MAP.md`.
   - Ask the human to merge. Merging is gate 1. No TDD work before that.
