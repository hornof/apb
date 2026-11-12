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

## Gates: how the human reviews

Learned on sketch-gallery's PRD (2026-11-11/12), where the human didn't know where to look or what to do.

- **One review surface: the PR.** Every question, answer and comment for a gate lives on that PR. Material a question depends on is in the doc or linked from the question itself, never on another issue or tab.
- **One action, with a template.** The handoff gives the human one thing to do, never a choice of review mechanics. Open questions come with Claude's recommended answers, so "ok" is a valid reply.
- **Exact clicks.** Line comments need GitHub's *source diff* view, not the rich diff. The human can't approve their own PR, so a review is submitted as **Submit review → Comment**. Merging the PR is the approval.
- **Answers in chat count.** The orchestrator posts them on the PR word for word, so nothing important lives only in chat.
- **Handoff checklist**, run by the orchestrator before every "your turn": every place the human must go and every click; stale lines in the doc; every unanswered item from the human's last input; the latest version linked (a branch link, not a commit). Then one short message: what to do, one link per thing, nothing else.
- **Approval is explicit.** "Approved", "merge it". Praise ("looks good") is not approval; when unsure, ask once.

## Keeping the human in the picture

- **Project map.** A single page (`docs/MAP.md`, or a published page linked from it) listing every piece of the project by where it lives (GitHub, Claude pages, the human's computer, outside services, hosting), with what each is for and when it's used, and the current gate at the top. Created at setup; never introduce a new place (page, folder, scheduled task, outside account) without adding it in the same turn.
- **Project story.** `docs/STORY.md`: one entry per gate, with the date, the PR, the decision and the key links, so someone new can follow what happened.
- **One question at a time.** Finish every part of the current question before raising the next. Ask in plain chat, not in pop-up buttons, so the human can answer with a sentence.
- **Show every visual.** Anything visual (mockups, generated images, layouts) goes on a page the human can look at, including the items Claude rejected and why. Claude never makes the final aesthetic call alone.
- **One link per thing.** Never link a local file or folder the human doesn't need to open. If the app opens extra panes, say which one matters.

## Generated content

When a project needs a lot of placeholder content (images, text), treat it like a small pipeline (sketch-gallery made 161 drawings this way):
- **A subject list is the source of truth,** and the file name carries any metadata the product needs (for example the date: `YYYY-MM-DD.jpg`), never file timestamps, which git resets.
- **A review page** shows everything so far, with a set-aside pile and the reason for each rejection, updated after every batch.
- **A runbook and a log** live next to the content, so any session can continue the work.
- **When a service rate-limits, schedule it:** a scheduled task that runs the runbook every N minutes, one small batch per run. Under rate limits, image tools may cram several images into one picture; small batches in fresh chats avoid that.
- **Check processing on a real page:** color profiles and near-white backgrounds can show as gray boxes against a white page. Convert to sRGB and lift near-white to pure white.

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
