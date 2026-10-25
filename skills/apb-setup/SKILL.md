---
name: apb-setup
description: Interactive setup for a new apb project — repo, board, collaborators, optional @claude review bot. Use once per project.
---
This skill belongs in the implementer surface (Claude Code, opened in the project directory) — it needs git and gh. Walk the user through these in order, doing everything you can yourself (create the repo with `gh repo create` when gh is authenticated) and prompting them only for truly manual steps, one at a time:

0. Prerequisites: verify `git` and `gh` are installed and `gh auth status` shows a logged-in account — fix these first (install, then `gh auth login`), because every later step that touches GitHub assumes Claude can act there without the human clicking through the website.
1. Repo: create it with `gh repo create`, push an initial commit with `templates/CLAUDE-template.md` copied to `CLAUDE.md` and `docs/collaborator-guide.md` adapted for this project.
2. Collaborators: ask for each teammate's GitHub username; invite them.
3. Projects board: create with columns Todo / In progress / In review / Done; link the repo.
4. CI: a workflow that runs the test suite on every PR.
5. Permissions: create `.claude/settings.local.json` in the repo — auto-accept within a qualified allow-list (build/test/git commands, no blanket shell), extra directories limited to the project's mailbox folder.
6. Hosting: connect the repo (e.g. Cloudflare Pages) so main deploys and PRs get preview URLs.
7. OPTIONAL @claude review bot on the user's subscription: run `/install-github-app` and choose the subscription-token path ([docs](https://code.claude.com/docs/en/github-actions)). Skip unless asked.

Never store tokens in the repo; confirm each step worked before the next.
