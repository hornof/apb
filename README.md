# Agentic Programming Blueprint (apb)

A **software factory, packaged as a Claude Code plugin**: the roles, gates, and process for building software with AI agents. Humans direct and approve; agents build and check; the agent that writes code never reviews or merges it.

The workflow has been in use since July 2026 (it built [hornof.org](https://hornof.org) and eclipse-sim) and is the written, installable form of it; comparable to [gstack](https://github.com/garrytan/gstack) and [Superpowers](https://github.com/obra/superpowers) — see `docs/playbook.md` for how it differs.

## The process

```
Idea → PRD → TDD → Split the TDD into cards → Build loop → PR → Review → Merge → Deploy → Verify
        ▲gate1  ▲gate2                                            ▲gate3              (live product)
```
Humans supply the idea, comment at the gates, review and merge PRs. Claude does the rest: drafting the docs, writing tests and code, reviewing, verifying the deployed result. Full detail: `docs/playbook.md`.

## How apb checks quality

Each project's TDD picks its exact tools. These are the defaults for web projects, all well known and widely used.

| Check | Tool | What it catches | When it runs |
|---|---|---|---|
| Unit tests | Vitest | A function that gives a wrong answer | The unit tests run every time: on every push to a PR and to main |
| Random-input tests | fast-check, inside the Vitest tests, trying a rule on many random inputs and checking each answer against a second, simpler implementation | The cases no one thought to write a test for | With the unit tests |
| Browser tests | Playwright, in Chromium, Firefox and WebKit, at the screen widths the TDD names | A page that breaks in a real browser, or at one screen width | The quick set on every other push to a PR: the whole browser suite in one browser at one width, plus the changed test files in every browser. The full set, every browser and every width for the tests that depend on width, once per PR, when the `full-tests` label is added just before the human sees it, then on every later push to that PR and on every push to main. A change to docs only skips the browser tests |
| Speed and accessibility | Lighthouse, and axe inside the Playwright tests | A slow page, or one that is hard to use with a keyboard or screen reader | Lighthouse on the PR's preview, as the TDD sets it up; axe with the browser tests |
| Mutation testing | Stryker | Tests that pass but would miss a real bug: it plants small bugs in the code and checks that a test fails each time | Automatically, as the TDD sets it up |
| Deliberate-bug check | The builder, by hand: it puts one real bug back in | Tests that look thorough but miss the bug that actually happened | Once per project, on the first build PR with real logic |
| Hand check | The reviewer and the human, on the Cloudflare Pages preview of each PR | What the tests couldn't check; the builder lists these in the PR | On every PR, before the merge |

GitHub Actions runs the tests. After every merge, the orchestrator checks the live product too, not just the tests.

apb's own code is checked the same way. Every script and hook has its own test file with random-input tests, several checked against a second, simpler implementation. One test checks that every child process they start has a time limit, and the PRs that change them report a mutation check.

## How the orchestrator and the agents talk

The orchestrator (the main Claude Code session, the one the human talks to) starts each builder and reviewer with a written brief. It can message one while it runs, gets each one's final report, and can read each one's full log. All of them run in Claude Code on the same laptop, so the orchestrator sees everything they do; an orchestrator in the cloud couldn't, and the human would have to copy chats across by hand. Where the logs are, and how to drill down when a step looks wrong: the playbook's "How the orchestrator and the agents talk".

## What's in the box

| Piece | What it does |
|---|---|
| `skills/apb-setup` | Walks you through repo, board, Claude's own GitHub account, and (optional) @claude-bot setup — prompts you for the manual steps |
| `skills/apb-prd` | Turns the PRD input into a one-page PRD with checkable acceptance criteria |
| `skills/apb-tdd` | Turns an approved PRD into a TDD: architecture, test plan, PR-sized pieces |
| `skills/apb-board` | Turns an approved TDD into GitHub Issues on a Projects board |
| `skills/apb-build` | The orchestrator (the main Claude Code session) takes the next card from the board and spawns the builder subagent on it |
| `skills/apb-loop` | Runs the loop unattended over several cards — launch protocol, liveness by commit age, halt conditions |
| `skills/apb-review` | Runs one review round on every PR, code or docs, in a separate context that did not write it |
| `agents/builder`, `agents/reviewer` | The two subagents the orchestrator spawns; the separated roles |
| `hooks/` | Every hook acts in each apb project (a folder with `docs/MAP.md`) and in the apb repo itself; `hooks/apb_folder.py` holds that one rule. Writes `.apb/events.jsonl` there: one line per prompt (the human's messages, and the ones Claude Code sends itself, such as a background agent reporting back), agent start and stop, shell command, checklist item, turn end and notification, so the orchestrator can see and time each step. A Stop hook makes the orchestrator say who holds the ball when its work runs out, and reminds the human once if they've held it for 30 minutes. It also sends back a message that gives a PR, issue or card `#N`, or a count of them, a state (open, merged, closed, done, in review, on the board, or in a column) without a lookup in the same turn: a `gh` or `git` command, or a run of the map or progress-page script. A brief check refuses a brief to a builder or reviewer that tells it how to format or post its work, and a report that says it "departed from" its file sends back every hand-off to the human, up to 3 times a turn, until the orchestrator runs `hooks/apb-brief.py resolve`. A watcher hook keeps `scripts/apb-watch.py` running for the orchestrator alone (not in a build or review session's git worktree; the newest session started in the main checkout takes it over; a reopened old one doesn't), on the project's repo and its coordination issue (with a second watcher when that issue is in another repo), and wakes it when a watcher finds something. A version check says, at session start and in a watcher's wake after a merge, when main's apb plugin is newer than the session's, with the update commands; in the apb repo it also says when the checkout is off `main` or behind `origin/main`. A pop-up check refuses multiple-choice cards and the desktop app's end-of-turn question buttons, except during `/apb-setup`, and tells Claude to ask in chat |
| `scripts/apb-watch.py` | Runs in the background while the orchestrator waits. It exits when something needs it: a PR merged, opened or commented on, checks finished, a command stuck with no CPU, an agent gone quiet, or a check-in time. It reads GitHub every 2 minutes while the repo is active and every 15 minutes once it has been quiet for an hour, and all the watchers on a machine together stay under 1,000 of the account's 5,000 GraphQL points an hour. `--list` shows the running watchers and the budget left |
| `scripts/claude-account.py` | Stores the token of Claude's own GitHub account in a project's git-ignored settings, after checking it with GitHub, and sets the git author, so GitHub shows Claude's work as the account's. It never shows the token |
| `scripts/claude-account-sources.py` | Lists the earlier apb projects on this computer that already hold the token of Claude's GitHub account, oldest first, so setup can offer to reuse the account and copy the token from the oldest. It never shows the token |
| `scripts/card-times.py` | When a card closes, posts one comment on its issue with the minutes spent in build, in review and with the human, how many times the PR was handed to the human, and how many of the human's messages were more than a routine reply. It reads the events file and GitHub; `--collect` lists every card's numbers for a retrospective |
| `scripts/comment-author.py` | Tells Claude's GitHub comments from the human's, for `card-times.py` and `decision-check.py`: by the author login when Claude has its own account, and by the role signature, which still counts for older comments, the advisor and projects without the account |
| `scripts/decision-check.py` | Checks a PR's Decision threads: that each has the fixed shape before the hand-off, and whether each is answered, so a builder implements a Decision only once the human has chosen. It reads GitHub |
| `scripts/human-turn.py` | Lists the cards and PRs assigned to the human (the "your turn" flag) and reports each stale one: nothing open on it any more, such as a merged PR or an answered question. It prints the command that clears it. It reads GitHub |
| `scripts/map-artifact.py` | Builds the map artifact, a Claude artifact, from a project's `docs/MAP.md`, so the two never disagree. It shows how many build cards are done, counted from GitHub |
| `scripts/progress-page.py` | Builds the progress page, a Claude artifact: who holds the ball, the stages, the board's cards by column, each running agent and its last step, each open PR and who it waits on, and how long recent agents took. It reads the events file and GitHub |
| `scripts/version-bump.py` | The apb repo's own version step: `.github/workflows/plugin-version.yml` runs it to raise the plugin version by one per merge into main, and to fail a PR that changes the version |
| `templates/` | PRD, TDD, repo-CLAUDE.md, README, LOG, LESSONS, MAP, HISTORY, reviewer-notes and .gitignore starters, and the CI workflow |
| `docs/collaborator-guide.md` | Step-by-step onboarding for a teammate new to AI-assisted building |
| `docs/system-design-guide.md` | The design steps for the TDD, with common patterns and red flags |
| `docs/product-guide.md` | Product management for the human: whether a new idea belongs in the current work, the backlog of ideas that don't, ranking it, and choosing what to build next |
| `docs/projects.md` | The projects that use apb, whether each is in use, and where each one's checkout is; an apb change goes to every project marked in use, and to no other |

## Install

In your terminal:

```bash
claude plugin marketplace add hornof/apb
claude plugin install apb@apb
```

Or inside Claude Code (v2.1.275 or later): `/plugin install apb --marketplace hornof/apb`, then pick a scope. Older versions: `/plugin marketplace add hornof/apb` then `/plugin install apb@apb`. Both builder and reviewer run on a Claude subscription — the optional @claude PR bot does too, via an OAuth token ([docs](https://code.claude.com/docs/en/github-actions)).

## Core rules

Tests before code · one feature per flat PR · only humans merge · the reviewer is a separate session, read-only on what it audits · verify the **deployed** artifact, not just CI · every incident becomes a written rule in CLAUDE.md the same day. Testing tools: see [How apb checks quality](#how-apb-checks-quality).

