# Projects using apb

The projects that run on apb, whether each is in use, and where its checkout is. The playbook's rule "An apb change reaches the projects already using apb in the same round" reads this list: a change to how projects run goes to every project marked "yes" under In use, and to no other, in that project's checkout. Projects and their checkouts are found here, never by searching the computer's folders for an apb-style `CLAUDE.md`, which also finds projects no longer in use.

- Checkout is the folder on the Mac that holds the project's repo; `~` is the home folder. Work there from a fresh fetch, on a new branch or worktree.
- "clone" under Checkout means the project has no checkout on this Mac: clone it with `gh repo clone <repo>` beside the apb checkout, then write that folder into the row, in the same apb PR as the change.
- apb-setup adds a new project's row as one of its steps, with "yes" and the project folder it set up. To add a project by hand, add a row with its repo, "yes" and its checkout or "clone".
- When a project stops being used, change its In use to "no" and keep the row: it records that the project is no longer in use.

| Repo | In use | Checkout |
|---|---|---|
| hornof/sketch-gallery | yes | `~/src/claude/sketch-gallery` |
| hornof/tahoe-tracker | yes | `~/src/claude/tahoe-tracker` |
