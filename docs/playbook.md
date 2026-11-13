# apb playbook: building software with Claude

How a small team of humans and Claude builds software with apb. This file explains the process; links go to official docs for the tools. It ships with the plugin, so every project and every Claude session reads the same copy.

## The steps

```
Idea → PRD → TDD → Board → Build → Review → Merge → Deploy → Check the live product
        gate 1  gate 2                       gate 3
```

1. **PRD** (what and why). Claude writes it from the human's description: goals, user journeys, requirements that each end with a check ("loads in under 1 second", "works on a phone"), mockups, and the look. The human reviews and approves: **gate 1**.
2. **TDD** (how it's built and tested). Architecture, where data lives, the test plan, risks, and the work split into PR-sized pieces. Use `docs/system-design-guide.md` for the design part. The human approves: **gate 2**.
3. **Board.** Each piece of work becomes a GitHub issue on a Projects board (to do, in progress, in review, done). Merged PRs close their issues.
4. **Build.** For each piece: Claude writes the tests first, then the code until the tests pass, then opens one small PR.
5. **Review and merge.** A second Claude reviews the PR. The human reads the review, checks the PR's preview site, and merges: **gate 3**. Tests must pass first.
6. **Check the live product** after every merge: for example, the site's content, layout and links, or that the package installs and runs. Passing tests don't prove the product works.
7. **Turn mistakes into rules.** When something goes wrong, add one line to the project's `CLAUDE.md` the same day, so every later session follows it.

A gate passes only when the human explicitly approves that document. After approval, its wording changes only if the human decides to change it.

## Who does what

| Who | Does | Never does |
|---|---|---|
| **The human** | Sets the goal, approves the three gates, merges every PR, judges how things look | — |
| **Orchestrator**: the main Claude Code session, the one the human talks to | Writes the PRD and TDD, keeps the board, starts the builder and reviewer | Merges |
| **Builder**: a separate Claude the orchestrator starts, from `agents/builder.md` | Writes tests, then code, for one issue; opens the PR | Merges |
| **Reviewer**: a separate Claude the orchestrator starts, from `agents/reviewer.md` | Reviews the PR and the live product | Writes code |
| **Advisor** (optional): Claude in Cowork, outside the repo | Talks through the idea, does research, gives a second opinion; posts on GitHub issues | Writes to the repo |

The builder and reviewer each start fresh, with their own instructions, and can't see each other's work. The main safety rule: whoever writes the code never reviews or merges it.

On a shared project, the project owner approves the gates and merges. To bring in another person, assign them an issue or ask them to review a PR.

The human never types git commands; the orchestrator does. The advisor never gives the human something to paste into Claude Code; it posts on the GitHub issue, and the orchestrator reads it there. Someone working alone can skip the advisor and do everything in Claude Code.

## Session settings

- **Check the model at the start of every session.** The model picker starts on the app's default, not the one you planned. For unattended runs, record the model in the first line of `LOG.md`.
- **Use auto mode.** Each human sets it once in their own `~/.claude/settings.json` (`"permissions": {"defaultMode": "auto"}`), and picks **Auto** once per folder in the desktop app. "Accept edits" mode only covers file edits, so every command still asks for permission. Never put `defaultMode` in a repo file: it overrides each person's own setting.
- **The repo's `.claude/settings.json`** holds the shared rules: an allow list (used when auto mode isn't available) and a deny list (no merging, no force-pushing, no pushing to main).
- **If a permission prompt appears mid-session,** add the missing rule to `.claude/settings.json` the same day.
- **Write commands auto mode can approve.** One plain command per shell call. Use WebFetch for web pages, not `curl | grep`. Put multi-step logic in a script file and run that file. Avoid `$(…)`, loops and chained commands: they trigger prompts, and "Always allow" on them saves the exact command, which never matches again.
- **The advisor** should run where it can reach files, a browser and email; that's its job. It still never writes to the repo.

## How the human reviews

- **Everything for a review goes on the PR.** Questions, answers and comments all go there. If a question needs background, put the background in the doc or link it from the question.
- **Give the human one thing to do.** Don't offer a choice of ways to review. Give each open question a recommended answer, so "ok" is a valid reply.
- **Give exact clicks.**
  - To comment on a line, use the PR's "Files changed" tab in source view (not the formatted view).
  - GitHub doesn't let you approve your own PR, so submit reviews with **Submit review → Comment**.
- **Several items? Overview first, then one at a time.** List them with one sentence each. Then take item 1: one line of context, the exact files to look at, the question. Finish it before starting item 2, and say "item 2 of 10" each time.
- **Answers in chat count.** Claude copies them to the PR.
- **Check before asking the human to act.** Before every "your turn", Claude checks:
  - every place the human needs to go, and every click
  - anything out of date in the doc
  - anything the human asked that hasn't been answered
  - that the link points to the latest version

  Then Claude sends one short message: what to do, one link per thing, nothing else.
- **Only "approved" is approval.** "Looks good" is not. If unsure, ask.

## Helping the human keep track

- **Project map** (`docs/MAP.md`): every piece of the project, where it lives (GitHub, Claude pages, the human's computer, outside services), what it's for and when it's used. The current stage is at the top. Whenever Claude adds a new piece (a page, a folder, a scheduled task, an outside account), it updates the map at the same time.
- **Project story** (`docs/STORY.md`): one entry per stage, with the date, the PR and what was decided, so someone new can see what happened.
- **One question at a time.** Settle every part of the current question before asking the next. Ask in the chat, not with pop-up buttons, so the human can answer in their own words.
- **Plain English.** No made-up terms, no unexplained jargon, one instruction per sentence.
- **Show everything visual.** Mockups, generated images and layouts go on a page the human can see, including the ones Claude rejected and why. The human makes the final call on how things look.
- **One link per thing.** Don't link files or folders the human doesn't need to open.

## Making lots of generated content

When a project needs many generated items, such as placeholder images or sample text:
- **Keep a list of every item to make.** That list is the record of what exists and what's next.
- **Put information the product needs in the file name,** for example the date: `2025-10-07.jpg`. Don't rely on file dates; git changes them.
- **Keep a review page** that shows everything made so far, plus the rejected items and why, updated after each batch.
- **Write down the steps** (a runbook) and keep a log, so any Claude session can pick up the work.
- **If a service limits how much you can make, schedule it.** A scheduled task makes a small batch every 20 minutes or so. Under a limit, image tools may squeeze several images into one picture; small batches avoid that.
- **Check images on a real page.** Color profiles and near-white backgrounds can show as a gray box on a white page. Convert to sRGB and turn near-white into pure white.

## Unattended builds

The build can run one piece at a time with the human, or unattended, for example overnight. Unattended runs need these rules:

- **Saying "go" doesn't start anything.** The kickoff must actually start a background worker (Agent Teams, or a worktree per feature), and Claude names what it started.
- **A new commit every 30–60 minutes is the only sign the work is running.** Not a status message, not "the process is alive".
- **Write the stopping rules into the kickoff:** stop when every feature is accepted, or after N tries with no test improvement (write down why), or at a maximum number of tries.
- **A scheduled check watches the run:** it looks at how recent the last commit is and answers open questions on the issues. If it can restart a stuck worker, it uses the same commit-age rule.
- **Questions go on the issue.** A question for the human or the advisor is a comment on the card's GitHub issue. A question asked only in chat gets lost. Claude never passes messages through the human.
- **Only the worker writes to the build log** while it runs.

## The build record

Every project keeps a public record of how it was built:

- **`LOG.md`**: one line per build step (what changed, test results). Never edit old lines.
- **`LESSONS.md`**: what the debugging and testing taught, written as it happens.
- **A final writeup**: how long it took, what it cost, number of PRs and tests, what worked and what didn't.

## Testing and models

- **For logic-heavy code, go beyond example tests:**
  - property-based tests checked against an independent second implementation
  - mutation testing to measure how good the tests are
  - one test that puts a real bug back in, to prove the tests catch it
- **Strongest model for thinking, faster model for typing.** Planning, the PRD and TDD, and reviews run on the strongest model available. Writing lots of code runs fine on a faster model. Claude Code usually picks well by default.

## One-time setup

1. **Claude Code:** [quickstart](https://code.claude.com/docs/en/quickstart). Worth reading once: [Claude Code best practices](https://www.anthropic.com/engineering/claude-code-best-practices).
2. **GitHub:** [Hello World guide](https://docs.github.com/en/get-started/start-your-journey/hello-world) for repos, commits, PRs and merges. In apb, Claude does all of these when asked.
3. **Hosting:** chosen in the TDD and connected right after it's approved. [Cloudflare Pages](https://developers.cloudflare.com/pages/) is the usual choice: free, deploys `main` automatically, and gives every PR a preview site.

## Related approaches

apb's PRD and TDD are a written spec, the same idea as "spec-driven development" and [GitHub Spec Kit](https://github.com/github/spec-kit). apb writes the spec as plain prose with checkable requirements instead of using Spec Kit's templates.

Claude Code features apb can use but doesn't depend on: worktrees for parallel work, Agent Teams, hooks that run lint and tests automatically, and plugins.
