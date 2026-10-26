# Agentic Programming Blueprint (apb)

A **software factory, packaged as a Claude Code plugin**: the roles, gates, and process for building software with AI agents. Humans direct and approve; agents build and check; the agent that writes code never reviews or merges it.

The workflow has been in use since July 2026 (it built [hornof.org](https://hornof.org) and eclipse-sim) and is the written, installable form of it; comparable to [gstack](https://github.com/garrytan/gstack) and [Superpowers](https://github.com/obra/superpowers) — see `docs/playbook.md` for how it differs.

## The process

```
Idea → PRD → TDD → Task board → Build loop → PR → Review → Merge → Deploy → Verify
        ▲gate1  ▲gate2                              ▲gate3              (live site)
```
Humans supply the idea, comment at the gates, review and merge PRs. Claude does the rest: drafting the docs, writing tests and code, reviewing, verifying the deployed result. Full detail: `docs/playbook.md`.
## What's in the box

| Piece | What it does |
|---|---|
| `skills/apb-setup` | Walks you through repo, board, and (optional) @claude-bot setup — prompts you for the manual steps |
| `skills/apb-prd` | Turns a brain dump into a one-page PRD with checkable acceptance criteria |
| `skills/apb-tdd` | Turns an approved PRD into a TDD: architecture, test plan, PR-sized pieces |
| `skills/apb-board` | Turns an approved TDD into GitHub Issues on a Projects board |
| `skills/apb-build` | Takes the next card from the board and launches the builder agent on it |
| `skills/apb-loop` | Runs the loop unattended over several cards — launch protocol, liveness by commit age, halt conditions |
| `skills/apb-review` | Launches the reviewer agent — a separate context that did not write the code |
| `agents/builder`, `agents/reviewer` | The two separated roles |
| `templates/` | PRD, TDD, repo-CLAUDE.md, README, LOG, LESSONS, .gitignore, and MAILBOX starters |
| `docs/collaborator-guide.md` | Step-by-step onboarding for a teammate new to AI-assisted building |

## Install

In your terminal:

```bash
claude plugin marketplace add hornof/apb
claude plugin install apb@apb
```

Or inside Claude Code (v2.1.275 or later): `/plugin install apb --marketplace hornof/apb`, then pick a scope. Older versions: `/plugin marketplace add hornof/apb` then `/plugin install apb@apb`. Both builder and reviewer run on a Claude subscription — the optional @claude PR bot does too, via an OAuth token ([docs](https://code.claude.com/docs/en/github-actions)).

## Core rules (each one paid for)

Tests before code · one feature per flat PR · only humans merge · the reviewer is a separate session, read-only on what it audits · verify the **deployed** artifact, not just CI · every incident becomes a written rule in CLAUDE.md the same day. For web projects the default verifiers are Playwright (browser-behavior tests) and Lighthouse budgets (performance/accessibility scores); each TDD names its own.

