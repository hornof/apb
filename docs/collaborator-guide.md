# Collaborator guide — building with apb

You'll build a real project. AI agents do most of the design and coding; you give direction and details when needed. We're following [apb](https://github.com/hornof/apb/blob/main/docs/playbook.md), a workflow for building software with Claude (you don't need to read it now). Go through the steps below in order; this page is the whole map.

1. **Accept the invite** to the repo (email from GitHub, or github.com/notifications).

2. **Set up Claude Code.** Install it ([quickstart](https://code.claude.com/docs/en/quickstart)), clone the repo. Then, in your terminal (not inside Claude), run `claude plugin marketplace add hornof/apb` and `claude plugin install apb@apb`. Now run `claude` inside the repo. If it says `gh` is missing or not logged in, let it fix that (one browser login). It will ask permission for commands as it goes; answer "Yes, and don't ask again" for build, test and git commands — it saves those in `.claude/settings.local.json`, yours only, git-ignored. From here on you do everything by prompting Claude in this repo, including questions: "what do I do next?", "this isn't working", "what does X mean?" It has the repo and GitHub, so it knows the state.

3. **Brain dump.** Write everything you want built as a comment on the brain-dump ticket on the project board; the ticket lists the questions to answer. Then tell Claude: "I posted the brain dump on issue #N; draft the PRD." It writes `docs/PRD.md` and opens a PR.

4. **Review the PRD.** Read the PR. Comment on it, or tell Claude "I added comments, take a look," until it says what you mean. Merging the PR is your approval (gate 1). Nothing gets built before that.

5. **TDD.** Tell Claude: "PRD approved; draft the TDD." It writes `docs/TDD.md` — architecture, where data lives, how each requirement is tested — and opens a PR. Same review loop; merging is gate 2. Right after, Claude sets up hosting and CI per the TDD.

6. **Session 0.** By prompting only: make a small change, open a PR, have a reviewer who didn't write it approve, merge it, see it deploy. ~2 hours; you'll have touched the whole pipeline.

7. **Build.** Tell Claude "build the next card." It takes the top card from the project board, writes tests first, then code, and opens a PR. A reviewer who didn't write it approves; you merge; check the live site.
