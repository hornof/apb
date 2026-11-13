# AI-Team Playbook — how we build software with Claude

> The apb process, for a small team of humans and Claude. Links go to official docs — this file explains the process, not the tools. It ships with the plugin, so every project and every Claude session reads the same copy.

## The process at a glance

```
Idea → PRD → TDD → Task board → Build loop → PR → Review → Merge → Deploy → Verify
        ▲gate1  ▲gate2                              ▲gate3              (live site)
```
The three gates are human approvals. Humans supply the idea, comment at the gates, and merge; Claude drafts the docs, writes tests and code, reviews, and verifies the deployed result.

## What runs where

- **Claude Code**, opened in the project repo — the whole pipeline. One main session, the **orchestrator**, runs the skills, keeps the board, and spawns two subagents: the **builder** (tests, code, one PR per card) and the **reviewer** (reviews the PR and the live site in its own context). All git happens here. Nothing passes through the human between steps.
- **Cowork** (Claude chat with broad access) — optional. An **advisor**: thinking through the idea before a repo exists, research, anything outside the repo (local files, email, calendar, a real browser), and an independent read of the PRD, TDD or live site when asked. It never writes to the repo; its input goes on the relevant GitHub issue as a comment.
- **The human** — the idea, the three gates, every merge.

## Roles

| Who | Does | Never does |
|---|---|---|
| Humans (director) | Set intent, approve the 3 gates, review & merge every PR, judge taste | — |
| Claude (**orchestrator** — the main Claude Code session) | Drafts PRD/TDD, runs the skills, manages the board, spawns the builder and reviewer | merge to main |
| Claude (**builder** — a subagent the orchestrator spawns from `agents/builder.md`) | Writes tests then code for one issue at a time, opens PRs | merge to main |
| Claude (**reviewer** — a subagent the orchestrator spawns from `agents/reviewer.md`, in its own context) | Reviews PRs and the *deployed* site | write code |
| Claude (**advisor** — a Cowork session, optional) | Thinks through the idea with the human, researches, reads the repo and the live site, comments on issues | write to the repo |

On a shared project the project owner approves the gates and merges; they pull in another human by assigning an issue or requesting a PR review — nobody reviews by default.

Names, used the same way everywhere in apb: the **orchestrator** is the main Claude Code session the human talks to; the **builder** and **reviewer** are subagents it starts — separate conversations with their own instructions and no view of each other's work, each returning one summary to the orchestrator; the **advisor** is a Cowork session outside the repo, used when its broad access or a second opinion helps. “Spawn” means the orchestrator starts one of those subagents.

The core safety rule: the agent that writes code never reviews or merges it.

## Two surfaces

The roles above typically run on two differently-shaped Claude surfaces, and the split is itself a safety feature:

| Surface | Access | Power | Runs |
|---|---|---|---|
| **Advisor** (Claude chat/Cowork) | Broad — files, web, email, calendars | Limited — no repo writes; comments on GitHub issues | Idea stage, research, independent reads of PRD/TDD/live site |
| **Orchestrator** (Claude Code, opened in the repo) | Narrow — just the repo directories | Full — git, gh, deletes, pushes | Repo setup, the build loop (spawning builder and reviewer), all git operations |

### Session settings

- **Check the model at every session start, in both surfaces** — model selectors come up on app defaults, not on what you planned. For loop runs, record the actual model in `LOG.md`'s first line.
- **Orchestrator permissions: auto mode plus a checked-in deny list.** Every session runs in Claude Code's **auto** mode, set once in each human's `~/.claude/settings.json` (`"permissions": {"defaultMode": "auto"}`) and picked once per folder in the desktop app's mode picker. Auto mode, not "accept edits": accept-edits mode auto-approves file edits only, so every shell command and every new website prompts. Repo files can't enable auto, and a `defaultMode` in a repo file overrides the user's auto, so repos never set one. The repo's checked-in `.claude/settings.json` holds the shared allow list (the fallback when auto is unavailable) and the deny list (no merge, no force push, no push to main). A loop's blast radius is its own repo. A prompt mid-session is an incident: add the missing rule to `.claude/settings.json` the same day.
- **Write commands auto mode can approve.** One plain command per shell call. Fetch web pages with WebFetch, not `curl … | grep` pipelines. Put multi-step shell logic in a script file and run that one file. `$(…)`, loops and chained one-offs are what prompted most in the 2026-11-10 incident, and an "Always allow" on them saves a literal command that never matches again.
- **Advisor: use the surface with broad access** (local files, a real browser, email/calendar where relevant) — that reach is its job. By policy it stays read-only on the repo; no setting should "fix" that.

Humans never type git commands in this process — the orchestrator does them. The advisor never hands the human something to paste into Claude Code: its input goes on the GitHub issue, where the orchestrator reads it. The role separation is the invariant; the advisor is optional, and a human working alone runs everything in Claude Code.

## How the human reviews

Written after sketch-gallery's PRD review (November 2026), where the human didn't know what to look at or what to do.

- **Everything for a review goes on the PR.** Questions, answers and comments all go there. If a question needs background, put the background in the doc or link it from the question.
- **Give the human one thing to do.** Don't offer a choice of ways to review. Give each open question a recommended answer, so "ok" is a valid reply.
- **Give exact clicks.**
  - To comment on a line, use the PR's "Files changed" tab in source view (not the formatted view).
  - GitHub doesn't let you approve your own PR, so submit reviews with **Submit review → Comment**.
- **Several items? Overview first, then one at a time.** List them with one sentence each. Then explain item 1, with the exact files to look at, and stop. Finish item 1 before starting item 2, and say "item 2 of 10" each time.
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
- **Show everything visual.** Mockups, generated images and layouts go on a page the human can see, including the ones Claude rejected and why. The human makes the final call on how things look.
- **One link per thing.** Don't link files or folders the human doesn't need to open.

## Making lots of placeholder content

How sketch-gallery made 161 placeholder drawings:
- **Keep a list of every item to make.** That list is the record of what exists and what's next.
- **Put information the site needs in the file name,** for example the date: `2025-10-07.jpg`. Don't use file dates; git changes them.
- **Keep a review page** that shows everything made so far, plus the rejected items and why, updated after each batch.
- **Write down the steps** (a runbook) and keep a log, so any Claude session can pick up the work.
- **If a service limits how much you can make, schedule it.** Set up a scheduled task that makes a small batch every 20 minutes or so. Large batches under a limit came back as several images squeezed into one.
- **Check the result on a real page.** Some images showed a faint gray box on a white page. The fix: convert to standard sRGB color and turn near-white into pure white.

## Unattended builds (the loop)

The build loop can run interactively (one card at a time) or unattended — several features overnight. Unattended runs have their own failure modes, each of these rules paid for:

- **"Go" in chat is not a running loop.** The kickoff must actually launch a background worker (Agent Teams / a worktree per feature), and the agent confirms by naming the mechanism, not describing intent.
- **Proof of life = a commit on the branch within 30–60 minutes.** Liveness is always commit age — never "the process is alive" or a status message. No commit ≈ no loop.
- **Halt conditions are written into the kickoff**: all features accepted, OR N iterations without test improvement (stop and write down why), OR a max-iteration cap.
- **A monitor watches the loop** (a scheduled check): commit cadence, plus answering open questions on the issues. Whatever can relaunch a stalled worker must use the commit-age rule itself — a report-only watchdog serves nobody at 3am.
- **Questions go on the issue.** The orchestrator, builder and reviewer share one process and need no file to talk. A question for the human or the advisor is a comment on the card's GitHub issue — the board is the record, and a question that was only said in chat doesn't exist. Agents never relay through the human.
- **Single writer:** the worker alone appends to the build log while running.

## The build record

Every project produces a public how-it-was-built record — for many builders the record is half the point:

- **`LOG.md`** in the repo: append-only, one line per iteration (what changed, test status). The audit trail; git timestamps prove the schedule.
- **`LESSONS.md`**: what the debugging and testing taught, written as it happens.
- A closing **writeup**: duration, cost, PR count, test counts, what worked and what didn't. The brief, log, lessons, and tests stay in the repo as provenance.

## One-time setup (links, not tutorials)

1. **Claude Code** — install and first run: [quickstart](https://code.claude.com/docs/en/quickstart). Worth skimming once: [Claude Code best practices](https://www.anthropic.com/engineering/claude-code-best-practices) (Anthropic's own guide).
2. **GitHub** — repo, commits, PRs, merges: [Hello World guide](https://docs.github.com/en/get-started/start-your-journey/hello-world). In this process you'll do all of it *by prompting Claude*, not by typing git commands.
3. **Hosting** — chosen in the TDD and connected right after it is approved; [Cloudflare Pages](https://developers.cloudflare.com/pages/) is the usual pick (free; auto-deploys `main`, gives every PR a preview URL).

## The steps

1. **PRD** (what & why) — Claude drafts from your description: goals, user journeys, requirements with *checkable acceptance criteria written in advance* ("page loads < 1s", "works on phone"), clickable mockups, and the look and feel. You comment, iterate, approve → **gate 1**.
2. **TDD / engineering doc** (how & tested) — architecture, test plan, risks, work broken into PR-sized pieces. Approve → **gate 2**. A gate opens only on explicit sign-off of the named document — never on momentum or silence — and once approved, wording changes only by the human's explicit choice.
3. **Task board** — the TDD's pieces become **GitHub Issues** on a Projects board (todo / in progress / in review / done). This replaces old burndown lists: humans and Claude both read and write it, and merged PRs close their issues automatically.
4. **Build loop** — per feature: Claude writes the tests first, then code until green, then opens a small PR. One feature per PR.
5. **Review & merge** — reviewer-Claude comments; a human reads and **merges** → **gate 3**. CI must be green; look at the PR's preview URL, not just the code.
6. **Verify the live site** — after each merge, check what's actually deployed (content, layout, links). Tests pass ≠ site works; this step has caught the worst bugs.
7. **Lessons become rules** — anything that goes wrong gets one line in the repo's `CLAUDE.md` (standing rules every future Claude session inherits) the same day.

## Models

Where verification matters most (logic-heavy code), reach past example tests: property-based tests against an independent oracle, mutation testing to score the suite, and a meta-test that reintroduces a real bug to prove the harness catches it — the eclipse-sim recipe.

Rule of thumb: **strongest model for thinking, fast model for typing.** Plan, PRD/TDD drafting, and reviews run on the strongest available model (Opus-tier); bulk implementation runs fine on the fast tier (Sonnet) — Claude Code picks sensibly by default, so this mostly needs no action.

## Where "spec-driven development" fits

SDD is the industry name for exactly this shape (Specify → Plan → Tasks → Implement) — [GitHub Spec Kit](https://github.com/github/spec-kit) is the formal tooling. Our PRD + TDD **is** the spec, written as prose with hard acceptance criteria instead of Spec Kit's templates. So yes: when you ask for a PRD, you're asking for a spec. We skip the formal tooling because its known failure mode is drowning in markdown.

## Advances baked in (vs. early 2026)

Claude Code now natively does what used to need homemade scripts: parallel work in **worktrees**, **Agent Teams** with assignments, **hooks** that auto-run lint/tests, **plugins** that package a team's whole methodology. We use them where they help; the process above doesn't depend on any of them.
