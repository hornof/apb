# Collaborator guide — building with apb

You'll build a real project with AI doing the heavy lifting. You go through these steps in order, one at a time; this page is the whole map.

1. **GitHub account** — create one at github.com if needed and give the project owner your username; accept the invite when it arrives.
2. **Brain dump** — write down everything about what you want built, in any form. Also answer: Who will use it? What must it do on day one? Anything it must never do? How many users/how much traffic do you expect?
3. **Review the PRD** — Claude turns your dump into a one-page requirements doc. Read it; reply with anything wrong or missing. Iterate until you're happy. Your sign-off is gate 1; the TDD comes next.
4. **Review the TDD** — the system-design step: architecture, APIs, data, and how every requirement will be tested all live here. Read it and flag anything that worries you; the deep technical review is done by a separate Claude reviewer. You approve it (gate 2). (Implementation itself gets checked later, at every PR and against the live site.)
5. **Set up Claude Code** (hands-on starts here) — [quickstart](https://code.claude.com/docs/en/quickstart). Run `claude` in a fresh folder; from here on you do everything by *prompting*, not typing commands. Claude Code will ask permission for commands as it goes; answer 'Yes, and don't ask again' for build, test and git commands, and it saves those choices in `.claude/settings.local.json` (yours only; it is git-ignored).
6. **Session 0** — by prompting only: create the repo copy locally, make a commit, open a PR, have a reviewer who didn't write it approve, merge it, see the site deploy. ~2 hours; you'll have touched the whole pipeline.
7. **Build** — pick a card from the project board, prompt Claude to build it tests-first, a reviewer who didn't write it approves, you merge, check the live site.

The full process we're following: `docs/playbook.md`.
