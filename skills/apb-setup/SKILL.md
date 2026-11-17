---
name: apb-setup
description: Interactive setup for a new apb project — repo, board, collaborators, permissions, optional @claude review bot. Use once per project. CI and hosting are NOT set up here; they come after the TDD (apb-tdd step 5).
---
This skill belongs in the orchestrator (the main Claude Code session, opened in the project directory) — it needs git and gh. Walk the user through these in order, doing everything you can yourself (create the repo with `gh repo create` when gh is authenticated) and prompting them only for truly manual steps, one at a time:

0. Prerequisites: verify `git` and `gh` are installed and `gh auth status` shows a logged-in account with the `project` scope — fix these first (install, then `gh auth login`; if `gh auth status` lacks the `project` scope, run `gh auth refresh -h github.com -s project` — a one-time human step with a browser login; Claude cannot do it), because every later step that touches GitHub assumes Claude can act there without the human clicking through the website.
1. Repo: ask whether it should be public or private (never decide this yourself), then create it with `gh repo create`, then create the board (step 3) before the initial commit, so README.md's board link is filled in and no placeholder is ever committed. Push an initial commit with: templates/CLAUDE-template.md as CLAUDE.md; templates/README-template.md as README.md with the project name and one-sentence description filled in; templates/LOG-template.md as LOG.md; templates/LESSONS-template.md as LESSONS.md; templates/gitignore-template as .gitignore; templates/MAP-template.md as docs/MAP.md; templates/reviewer-notes-template.md as docs/reviewer-notes.md; templates/HISTORY-template.md as docs/HISTORY.md, keeping the intro and the Setup entry and leaving out the examples and later stages. Fill in every placeholder (text like `<project>`) before committing; none may remain in any committed file. Once `docs/MAP.md` exists, the plugin's hooks start writing `.apb/events.jsonl` (see the playbook's "The events file"); the `.gitignore` keeps `.apb/` out of git.
1a. Map artifact: the map lives in two places, `docs/MAP.md` and the map artifact, a Claude artifact built from it. Claude edits only `docs/MAP.md`.
   - Publish a first version of the map artifact to get its link. Put the link in `docs/MAP.md` (the intro and the map artifact's row), commit and push.
   - Then build the map artifact from `docs/MAP.md` and publish it to that same link: `python3 <apb plugin folder>/scripts/map-artifact.py <output.html> <map artifact link>`. The apb plugin folder is two folders above this skill's base directory.
   - From then on, build and publish it again after every change to `docs/MAP.md`, and again from main after each merge. Never edit the artifact by hand.
1b. PRD input issue: open an issue called "PRD input" that lists these questions for the human to answer:
   - What should it do?
   - Who is it for?
   - What similar products do you like? Include links.
   - What's a must-have, and what's a nice-to-have?
   - Public or private?
   - Where will people use it: a website, a phone app, a command-line tool, something else? If it's online, what address?
   - Any deadline?

   Link the issue from docs/MAP.md and docs/HISTORY.md. Add the issue to the board (step 3) in In progress. Move the card to the top of In progress (see apb-build step 1). When the PRD stage starts, close the issue; the board moves it to Done.
2. Collaborators: ask whether anyone else will work on the project.
   - If not, remove the collaborator-guide row from README.md and go on to step 3.
   - If so, ask for each teammate's GitHub username and invite them. Then copy docs/collaborator-guide.md into the project's docs/ folder, adapted for this project.
3. Projects board: create with columns Todo / In progress / In review / Done; link the repo. Then put the board's URL into README.md's last line.
   - A project has one board, never more.
   - Turn on the board's "Item closed" automation, so a closed issue moves its card to Done. It isn't always on by default, and GitHub's API can't turn it on. In the browser, on the board: Workflows → Item closed → Edit → set Status to Done → Save and turn on. The orchestrator can do this itself. A card moves to Done only when its issue closes; nobody moves a card to Done by hand.
   - From setup on, the board holds one issue per early stage: the PRD input issue (step 1b), then the PRD issue (apb-prd) and the TDD issue (apb-tdd). After the TDD, apb-board adds the build issues to the same board. So the board always shows where the project stands.
   - Each stage issue moves through the columns like any other issue. The PRD and TDD issues close when their gates' PRs merge. The PRD input issue is closed when the PRD stage starts.4. Permissions. The goal is zero mid-session prompts. Five parts:
   a. **Auto mode lives in the human's own settings, never in the repo.** Read `~/.claude/settings.json`. If `permissions.defaultMode` isn't `"auto"`, show the human that one line and offer to add it (it's their file, so ask first). Repo files can't turn auto on, and a `defaultMode` in a repo file overrides the user's `auto`, which brings back a permission prompt for every command. Never write `defaultMode` into any repo file.
   b. **In the desktop app, the mode picker remembers a mode per folder and outranks the settings.** Tell the human to pick **Auto** in the picker for this folder once.
   c. **Shared allow/deny list, checked in.** Copy `templates/claude-settings-template.json` to `.claude/settings.json` and commit it, so every collaborator and every worktree gets the same rules. It's the fallback when auto mode is unavailable; the deny list (no merge, no force push, no push to main) applies in every mode. `.claude/settings.local.json` stays git-ignored for personal additions, with no `defaultMode` and no one-off literal commands.
   d. Check: in the desktop app, read this session's permission mode (`get_session` with "self") and confirm it says `auto`. Otherwise ask the human what the mode picker shows.
   e. **Protect `main` on GitHub** (ask first; it's a repo setting): require a pull request before merging. Deny-list patterns can miss a spelling such as `git push -u origin HEAD:main`; branch protection can't be missed. Then auto mode can't push to main by accident.
5. OPTIONAL @claude review bot on the user's subscription: run `/install-github-app` and choose the subscription-token path ([docs](https://code.claude.com/docs/en/github-actions)). Skip unless asked.

Do NOT set up CI or hosting here: there is no stack yet, so a CI workflow has nothing to run and a hosting connection has nothing to build. Both happen right after the TDD is approved (apb-tdd, step 5).

Never store tokens in the repo; confirm each step worked before the next.
