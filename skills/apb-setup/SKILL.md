---
name: apb-setup
description: Interactive setup for a new apb project — repo, board, collaborators, permissions, optional @claude review bot. Use once per project. CI and hosting are NOT set up here; they come after the TDD (apb-tdd step 5).
---
This skill belongs in the orchestrator (the main Claude Code session, opened in the project directory) — it needs git and gh. Walk the user through these in order, doing everything you can yourself (create the repo with `gh repo create` when gh is authenticated) and prompting them only for truly manual steps, one at a time:

0. Prerequisites: verify `git` and `gh` are installed and `gh auth status` shows a logged-in account with the `project` scope — fix these first (install, then `gh auth login`; if `gh auth status` lacks the `project` scope, run `gh auth refresh -h github.com -s project` — a one-time human step with a browser login; Claude cannot do it), because every later step that touches GitHub assumes Claude can act there without the human clicking through the website.
1. Repo: ask whether it should be public or private (never decide this yourself), then create it with `gh repo create`, then create the board (step 3) before the initial commit, so README.md's board link is filled in and no placeholder is ever committed. Push an initial commit with: templates/CLAUDE-template.md as CLAUDE.md; docs/collaborator-guide.md adapted for this project; templates/README-template.md as README.md with the project name and one-sentence description filled in; templates/LOG-template.md as LOG.md; templates/LESSONS-template.md as LESSONS.md; templates/gitignore-template as .gitignore; templates/MAP-template.md as docs/MAP.md; templates/STORY-template.md as docs/STORY.md. No angle-bracket placeholders may remain in any committed file, except inside a code block.
1b. Brain-dump issue: open an issue called "Brain dump" that lists these questions for the human to answer:
   - What should it do?
   - Who is it for?
   - Is this a trial run for someone else? If so, who builds the real one, and where?
   - What similar sites or apps do you like? Include links.
   - What's a must-have, and what's a nice-to-have?
   - Public or private?
   - What web address should it have?
   - Any deadline?

   Link the issue from docs/MAP.md and docs/STORY.md.
2. Collaborators: ask for each teammate's GitHub username; invite them.
3. Projects board: create with columns Todo / In progress / In review / Done; link the repo. Then put the board's URL into README.md's last line.
4. Permissions. The goal is zero mid-session prompts. Five parts:
   a. **Auto mode lives in the human's own settings, never in the repo.** Read `~/.claude/settings.json`. If `permissions.defaultMode` isn't `"auto"`, show the human that one line and offer to add it (it's their file, so ask first). Repo files can't turn auto on, and a `defaultMode` in a repo file overrides the user's `auto` (sketch-gallery, 2026-11-10: a repo-local `acceptEdits` caused 30+ prompts in one session). Never write `defaultMode` into any repo file.
   b. **In the desktop app, the mode picker remembers a mode per folder and outranks the settings.** Tell the human to pick **Auto** in the picker for this folder once.
   c. **Shared allow/deny list, checked in.** Copy `templates/claude-settings-template.json` to `.claude/settings.json` and commit it, so every collaborator and every worktree gets the same rules. It's the fallback when auto mode is unavailable; the deny list (no merge, no force push, no push to main) applies in every mode. `.claude/settings.local.json` stays git-ignored for personal additions, with no `defaultMode` and no one-off literal commands.
   d. Check: in the desktop app, read this session's permission mode (`get_session` with "self") and confirm it says `auto`. Otherwise ask the human what the mode picker shows.
   e. **Protect `main` on GitHub** (ask first; it's a repo setting): require a pull request before merging. Deny-list patterns can miss a spelling such as `git push -u origin HEAD:main`; branch protection can't be missed. Then auto mode can't push to main by accident.
5. OPTIONAL @claude review bot on the user's subscription: run `/install-github-app` and choose the subscription-token path ([docs](https://code.claude.com/docs/en/github-actions)). Skip unless asked.

Do NOT set up CI or hosting here: there is no stack yet, so a CI workflow has nothing to run and a hosting connection has nothing to build. Both happen right after the TDD is approved (apb-tdd, step 5).

Never store tokens in the repo; confirm each step worked before the next.
