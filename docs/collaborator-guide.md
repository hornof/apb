# Collaborator guide — building with apb

This guide is for someone invited to join a project that already uses apb. Starting your own project? Install apb and follow its [README](https://github.com/hornof/apb#readme) instead.

You'll build a real project with Claude. Claude writes the plans and the code. You decide what to build, answer its questions, and approve each step. The process is called [apb](https://github.com/hornof/apb/blob/main/docs/playbook.md); you don't need to read about it now. Go through the steps below in order.

Claude proposes every change as a pull request (PR) on GitHub. You approve a PR by merging it. At three points, called **gates**, Claude stops and waits for your approval before going on:
- **Gate 1, the PRD** (product requirements document): what the product does and for whom.
- **Gate 2, the TDD** (technical design document): how it's built and tested.
- **Gate 3, each feature's PR**: the code for one feature.

1. **Accept the invite** to the repo (email from GitHub, or github.com/notifications).

2. **Set up Claude Code.**
   1. Install Claude Code ([quickstart](https://code.claude.com/docs/en/quickstart)).
   2. Clone the repo.
   3. In your terminal (not inside Claude), run `claude plugin marketplace add hornof/apb`.
   4. Then run `claude plugin install apb@apb`.
   5. Run `claude` inside the repo.
   6. If it says `gh` is missing or not logged in, let it fix that (one browser login).
   7. Turn on auto mode, so Claude doesn't stop to ask permission for every command. Tell Claude "set my default permission mode to auto", and approve that one change to your `~/.claude/settings.json`.
   8. In the desktop app, also pick **Auto** in the mode picker under the message box. In the terminal, restart `claude` instead.
   9. If permission prompts keep coming, tell Claude. That's a bug in the setup, not something to click through. The repo's `.claude/settings.json` already blocks the dangerous actions (merging, force-pushing, pushing to main).

   From here on, you do everything by asking Claude in this repo. That includes questions like "what do I do next?", "this isn't working" or "what does X mean?" Claude can see the repo and GitHub, so it can answer them.

3. **Brain dump.** The repo has an issue called "Brain dump" (on GitHub, under the repo's **Issues** tab). It lists questions to answer. Write your answers, and anything else you want built, as a comment on it. Then tell Claude: "I posted the brain dump; draft the PRD." Claude writes `docs/PRD.md`, makes clickable mockups of the main screens if the product has screens, and opens a PR.

4. **Review the PRD.** Claude tells you what to read and where to click.
   - Answer the open questions in chat or on the PR. Each one has Claude's suggestion, so "ok" is a fine answer.
   - To comment on a specific line, use the PR's "Files changed" tab.
   - When the PRD says what you mean, tell Claude "approved", then click **Merge**. That's gate 1. Nothing gets built before it.
   - Lost track of what's where? `docs/MAP.md` lists every piece of the project.

5. **TDD.** Tell Claude: "PRD approved; draft the TDD." Claude writes `docs/TDD.md`: the parts of the system, where data is stored, and how each requirement is tested. It opens a PR. Review it the same way as the PRD. Saying "approved" and merging is gate 2. Then Claude sets up two things the TDD chose: where the product runs or is published, and automatic tests that run on every PR.

6. **Practice run.** Before real work, take one small change all the way from idea to live, only by asking Claude: make the change, open a PR, have a reviewer who didn't write it approve, merge it, and see it go live (deployed or published). It takes about 2 hours.

7. **Build.** The repo's project board lists the work as items, one per feature. Tell Claude "build the next item." Claude takes the top item, writes the tests first, then the code, and opens a PR. A separate Claude reviewer that didn't write the code checks it. You merge it (gate 3), then check the live product.
