# AI-Team Playbook — how we build software with Claude

> The apb process, for a small team of humans and Claude. Links go to official docs — this file explains the process, not the tools. It ships with the plugin, so every project and every Claude session reads the same copy.

## The process at a glance

```
Idea → PRD → TDD → Task board → Build loop → PR → Review → Merge → Deploy → Verify
        ▲gate1  ▲gate2                              ▲gate3              (live site)
```
The three gates are human approvals. Humans supply the idea, comment at the gates, and merge; Claude drafts the docs, writes tests and code, reviews, and verifies the deployed result.

## Roles

| Who | Does | Never does |
|---|---|---|
| Humans (director) | Set intent, approve the 3 gates, review & merge every PR, judge taste | — |
| Claude (main session) | Drafts PRD/TDD, runs the skills, manages the board | merge to main |
| Claude (builder agent) | Writes tests then code for one issue at a time, opens PRs | merge to main |
| Claude (reviewer) | Reviews PRs and the *deployed* site — a separate session from the builder | write code |

On a shared project the project owner approves the gates and merges; they pull in another human by assigning an issue or requesting a PR review — nobody reviews by default.

The core safety rule: the agent that writes code never reviews or merges it.

## Two surfaces

The roles above typically run on two differently-shaped Claude surfaces, and the split is itself a safety feature:

| Surface | Access | Power | Runs |
|---|---|---|---|
| **Navigator** (Claude chat/Cowork) | Broad — files, web, email, calendars | Limited — no repo writes except appending to QUESTIONS.md, no deletes | PRD/TDD drafting, research, the reviewer role, deployed-site verification |
| **Implementer** (Claude Code, opened in the repo) | Narrow — just the repo directories | Full — git, gh, deletes, pushes | Repo setup, the build loop, all git operations |

### Session settings

- **Check the model at every session start, in both surfaces** — model selectors come up on app defaults, not on what you planned. For loop runs, record the actual model in `LOG.md`'s first line.
- **Implementer permissions: scoped, not global.** Auto-accept mode *inside* a per-repo allow-list (`.claude/settings.local.json`): no blanket shell access. A loop's blast radius is its own repo. A permission prompt stalling an overnight run is the accepted failure mode — add the missing rule next morning rather than widening permissions in advance.
- **Navigator: use the surface with broad access** (local files, a real browser, email/calendar where relevant) — that reach is its job. By policy it stays read-only on the repo host except for appending to QUESTIONS.md; no setting should "fix" that.

Humans never type git commands in this process — the implementer session does them. When one surface needs the other to act, it writes a self-contained plain-English paste block (what to do, why, where the context lives) — never a list of shell commands for the human to relay. The role separation is the invariant; how you map roles to surfaces can vary (a solo builder might run everything in Claude Code with the reviewer as a fresh session or the @claude PR bot).

## Unattended builds (the loop)

The build loop can run interactively (one card at a time) or unattended — several features overnight. Unattended runs have their own failure modes, each of these rules paid for:

- **"Go" in chat is not a running loop.** The kickoff must actually launch a background worker (Agent Teams / a worktree per feature), and the agent confirms by naming the mechanism, not describing intent.
- **Proof of life = a commit on the branch within 30–60 minutes.** Liveness is always commit age — never "the process is alive" or a status message. No commit ≈ no loop.
- **Halt conditions are written into the kickoff**: all features accepted, OR N iterations without test improvement (stop and write down why), OR a max-iteration cap.
- **A monitor watches the loop** (a scheduled check): commit cadence, plus answering the mailbox below. Whatever can relaunch a stalled worker must use the commit-age rule itself — a report-only watchdog serves nobody at 3am.
- **Agent↔agent questions go in a mailbox file** (`QUESTIONS.md` (at the repo root), append-only, status OPEN → ANSWERED → CLOSED; NEEDS-HUMAN items get surfaced in chat). Direct session-to-session messaging, where available, is fine as the notification — but the file is the record, because messages die with sessions. Agents never relay through the human, and if it was only said in chat, it doesn't exist.
- **Single writer:** the worker alone appends to the build log while running.

## The build record

Every project produces a public how-it-was-built record — for many builders the record is half the point:

- **`LOG.md`** in the repo: append-only, one line per iteration (what changed, test status). The audit trail; git timestamps prove the schedule.
- **`LESSONS.md`**: what the debugging and testing taught, written as it happens.
- A closing **writeup**: duration, cost, PR count, test counts, what worked and what didn't. The brief, log, lessons, and tests stay in the repo as provenance.

## One-time setup (links, not tutorials)

1. **Claude Code** — install and first run: [quickstart](https://code.claude.com/docs/en/quickstart). Worth skimming once: [Claude Code best practices](https://www.anthropic.com/engineering/claude-code-best-practices) (Anthropic's own guide).
2. **GitHub** — repo, commits, PRs, merges: [Hello World guide](https://docs.github.com/en/get-started/start-your-journey/hello-world). In this process you'll do all of it *by prompting Claude*, not by typing git commands.
3. **Hosting** — [Cloudflare Pages](https://developers.cloudflare.com/pages/) (free; auto-deploys `main`, gives every PR a preview URL).

## The steps

1. **PRD** (what & why) — Claude drafts from your description; features get *checkable acceptance criteria written in advance* ("page loads < 1s", "works on phone"). You comment, iterate, approve → **gate 1**.
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
