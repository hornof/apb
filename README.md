# Agentic Programming Blueprint (apb)

A **software factory, packaged as a Claude Code plugin**: the roles, gates, and process for building software with AI agents. Humans direct and approve; agents build and check; the agent that writes code never reviews or merges it.

The workflow has been in use since July 2026 (it built [hornof.org](https://hornof.org) and eclipse-sim) and is the written, installable form of it; comparable to [gstack](https://github.com/garrytan/gstack) and [Superpowers](https://github.com/obra/superpowers) — see `docs/playbook.md` for how it differs.

## The process

```
Idea → PRD → TDD → Split the TDD into cards → Build loop → PR → Review → Merge → Deploy → Verify
        ▲gate1  ▲gate2                                            ▲gate3              (live product)
```
Humans supply the idea, comment at the gates, review and merge PRs. Claude does the rest: drafting the docs, writing tests and code, reviewing, verifying the deployed result. Full detail: `docs/playbook.md`.

## How the orchestrator and the agents talk

The orchestrator (the main Claude Code session, the one the human talks to) starts each builder and reviewer with a written brief. It can message one while it runs, gets each one's final report, and can read each one's full log. All of them run in Claude Code on the same laptop, so the orchestrator sees everything they do; an orchestrator in the cloud couldn't, and the human would have to copy chats across by hand. Where the logs are, and how to drill down when a step looks wrong: the playbook's "How the orchestrator and the agents talk".

## What's in the box

| Piece | What it does |
|---|---|
| `skills/apb-setup` | Walks you through repo, board, and (optional) @claude-bot setup — prompts you for the manual steps |
| `skills/apb-prd` | Turns the PRD input into a one-page PRD with checkable acceptance criteria |
| `skills/apb-tdd` | Turns an approved PRD into a TDD: architecture, test plan, PR-sized pieces |
| `skills/apb-board` | Turns an approved TDD into GitHub Issues on a Projects board |
| `skills/apb-build` | The orchestrator (the main Claude Code session) takes the next card from the board and spawns the builder subagent on it |
| `skills/apb-loop` | Runs the loop unattended over several cards — launch protocol, liveness by commit age, halt conditions |
| `skills/apb-review` | Runs one review round on every PR, code or docs, in a separate context that did not write it |
| `agents/builder`, `agents/reviewer` | The two subagents the orchestrator spawns; the separated roles |
| `hooks/` | Writes `.apb/events.jsonl` in each apb project: one line per prompt (the human's messages, and the ones Claude Code sends itself, such as a background agent reporting back), agent start and stop, shell command, checklist item, turn end and notification, so the orchestrator can see and time each step. A Stop hook makes the orchestrator say who holds the ball when its work runs out, and reminds the human once if they've held it for 30 minutes. It also sends back a message that states a PR number, version, count, or the word board or column without a lookup in the same turn: a `gh` or `git` command, or a run of the map or progress-page script. A brief check refuses a brief to a builder or reviewer that tells it how to format or post its work, and a report that says it "departed from" its file sends back every hand-off to the human, up to 3 times a turn, until the orchestrator runs `hooks/apb-brief.py resolve` |
| `scripts/apb-watch.py` | Runs in the background while the orchestrator waits. It exits when something needs it: a PR merged, opened or commented on, checks finished, a command stuck with no CPU, an agent gone quiet, or a check-in time |
| `scripts/card-times.py` | When a card closes, posts one comment on its issue with the minutes spent in build, in review and with the human, and how many of the human's messages were more than a routine reply. It reads the events file and GitHub; `--collect` lists every card's numbers for a retrospective |
| `scripts/decision-check.py` | Checks a PR's Decision threads: that each has the fixed shape before the hand-off, and whether each is answered, so a builder implements a Decision only once the human has chosen. It reads GitHub |
| `scripts/map-artifact.py` | Builds the map artifact, a Claude artifact, from a project's `docs/MAP.md`, so the two never disagree. It shows how many build cards are done, counted from GitHub |
| `scripts/progress-page.py` | Builds the progress page, a Claude artifact: who holds the ball, the stages, the board's cards by column, each running agent and its last step, each open PR and who it waits on, and how long recent agents took. It reads the events file and GitHub |
| `templates/` | PRD, TDD, repo-CLAUDE.md, README, LOG, LESSONS, MAP, HISTORY, reviewer-notes and .gitignore starters, and the CI workflow |
| `docs/collaborator-guide.md` | Step-by-step onboarding for a teammate new to AI-assisted building |
| `docs/system-design-guide.md` | The design steps for the TDD, with common patterns and red flags |

## Install

In your terminal:

```bash
claude plugin marketplace add hornof/apb
claude plugin install apb@apb
```

Or inside Claude Code (v2.1.275 or later): `/plugin install apb --marketplace hornof/apb`, then pick a scope. Older versions: `/plugin marketplace add hornof/apb` then `/plugin install apb@apb`. Both builder and reviewer run on a Claude subscription — the optional @claude PR bot does too, via an OAuth token ([docs](https://code.claude.com/docs/en/github-actions)).

## Core rules

Tests before code · one feature per flat PR · only humans merge · the reviewer is a separate session, read-only on what it audits · verify the **deployed** artifact, not just CI · every incident becomes a written rule in CLAUDE.md the same day. For web projects the default verifiers are Playwright (browser-behavior tests) and Lighthouse budgets (performance/accessibility scores); each TDD names its own.

