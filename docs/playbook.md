# apb playbook: building software with Claude

How a small team of humans and Claude builds software with apb. This file explains the process; links go to official docs for the tools. It ships with the plugin, so every project and every Claude session reads the same copy.

## The steps

```
Idea → PRD → TDD → Split the TDD into cards → Build → Review → Merge → Deploy → Check the live product
        gate 1  gate 2                                          gate 3
```

1. **PRD** (what and why). Claude writes it from the human's description: goals, user journeys, requirements that each end with a check ("loads in under 1 second", "works on a phone"), mockups, and the look. If a technical question could change the design, Claude first runs a spike: a short, time-boxed investigation whose findings go in `docs/spikes/`. The human reviews and approves: **gate 1**.
2. **TDD** (how it's built and tested). Architecture, where data lives, the test plan, risks, and the work split into PR-sized pieces. Use `docs/system-design-guide.md` for the design part. The human approves: **gate 2**.
3. **Split the TDD into cards.** Each piece of work becomes a GitHub issue on a Projects board (to do, in progress, in review, done). Merged PRs close their issues. The same board holds one issue each for the PRD input, the PRD and the TDD from setup on, so it always shows where the project stands.
4. **Build.** For each piece: Claude writes the tests first, then the code until the tests pass, then opens one small PR.
5. **Review and merge.** A second Claude reviews the PR. The human reads the review, checks the PR's preview site, and merges: **gate 3**. Tests must pass first.

   Every PR gets this review before the human sees it, whether it holds code or only docs. There is one review round. The reviewer sorts each item into one of three buckets:
   - **Fix**: the reviewer is sure. It is fixed straight away, and the orchestrator checks the fix.
   - **Note**: the reviewer isn't sure. The human skims it.
   - **Decision**: taste or a trade-off. It comes with a recommended answer. Nothing changes for it until the human answers. Resolving its thread with no comment accepts the recommendation. Its shape is fixed (see "How the human reviews").

   How the review looks on the PR is in "How the human reviews" below. The human's overrides and new rules go in the project's `docs/reviewer-notes.md`, which the reviewer reads before every review.
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

On GitHub, the orchestrator, the builders and the reviewers all act as Claude's own GitHub account, a second account the human makes once (apb-setup step 5). GitHub then shows which PRs, comments, commits and board moves are Claude's and which are the human's. The role signature at the end of each post, such as `— Claude (reviewer)`, tells the Claude roles apart. The advisor posts through its own connection as the human, with its signature. Without the account, Claude uses the human's login and only the signatures tell them apart.

The human never types git commands; the orchestrator does. The advisor never gives the human something to paste into Claude Code; it posts on the GitHub issue, and the orchestrator reads it there. Someone working alone can skip the advisor and do everything in Claude Code.

## How the orchestrator and the agents talk

The orchestrator, the builders and the reviewers all run in Claude Code on the same laptop. apb runs them all there on purpose: the orchestrator can see everything the others do. An orchestrator in the cloud, such as Cowork, can't see a builder running on the laptop, and the human ends up having to copy chats from one to the other by hand.

The orchestrator and each builder or reviewer communicate in four ways:
- **The brief.** The orchestrator starts each agent with a written brief. The brief gives the task only: the issue or PR, the worktree, the scratch folder, the command rule, and what to report back. How to work and how to post the work are in the agent's own file, `agents/builder.md` or `agents/reviewer.md`. The plugin's brief check, `hooks/apb-brief.py`, refuses a brief that tells an agent how to format or post its work.
- **Messages while it runs.** The orchestrator can send a running agent a message, for example to answer its question or to correct its course. The agent reads it and carries on.
- **The final report.** Each agent ends with one report to the orchestrator. The human doesn't see it; the orchestrator passes on what matters. If the report says the agent "departed from" its file, the brief check records it, and `hooks/apb-stop.py` holds every hand-off to the human until the orchestrator puts it right and runs `hooks/apb-brief.py resolve`.
- **The full log.** Claude Code saves every step of every agent on the laptop: each message, each command and its output. The orchestrator can read it at any time, while the agent runs or after it has finished.

Where to look:
- **The events file**, `.apb/events.jsonl` in the project (see "The events file"). Start here. It has one short line per event, for every session and agent: each shell command an agent ran, when, and its exit status, plus each agent's start and stop. The watcher (`scripts/apb-watch.py`), the progress page (`scripts/progress-page.py`) and the card times (`scripts/card-times.py`) all read it.
- **An agent's full log**: `~/.claude/projects/<project folder>/<session>/subagents/agent-<agent_id>.jsonl`. The project folder is the project's path with every character other than a letter or digit changed to `-`, for example `/Users/me/src/my.app` becomes `-Users-me-src-my-app`. The `session` and `agent_id` are on the agent's lines in the events file. The log is too big to read whole, so search it with a script, for example for tool results marked `is_error`.
- **The orchestrator's own log** is `<session>.jsonl` in the same project folder.

When something looks wrong, such as a step marked "failed", drill down:
1. Find the step in the events file: the agent, the command and its exit status.
2. Open that agent's full log and search it for the command, to read its output.
3. If the agent is still running, message it to ask.

The orchestrator never tells the human "I can't see what the agent did". It can always look. The human can look too: the progress page shows each running agent and its last step.

## Session settings

- **Check the model at the start of every session.** The model picker starts on the app's default, not the one you planned. For unattended runs, record the model in the first line of `LOG.md`.
- **Use auto mode.** Each human sets it once in their own `~/.claude/settings.json` (`"permissions": {"defaultMode": "auto"}`), and picks **Auto** once per folder in the desktop app. "Accept edits" mode only covers file edits, so every command still asks for permission. Never put `defaultMode` in a repo file: it overrides each person's own setting.
- **The repo's `.claude/settings.json`** holds the shared rules: an allow list (used when auto mode isn't available) and a deny list (no merging, no force-pushing, no pushing to main).
- **If a permission prompt appears mid-session,** add the missing rule to `.claude/settings.json` the same day.
- **Never type commands into the desktop app's Terminal panel.** It asks the human every time, even in auto mode. To run something in another folder, use the command's own folder option (`git -C <folder>`) or a script that changes folder itself.
- **Write commands auto mode can approve.** One plain command per shell call. Use WebFetch for web pages, not `curl | grep`. Put multi-step logic in a script file and run that file. Avoid `$(…)`, loops and chained commands: they trigger prompts, and "Always allow" on them saves the exact command, which never matches again.
- **The advisor** should run where it can reach files, a browser and email; that's its job. It still never writes to the repo.

## How the human reviews

- **Everything for a review goes on the PR.** Questions, answers and comments all go there. If a question needs background, put the background in the doc or link it from the question.
- **The review has few clicks.** Only Decisions get threads:
  - An index at the top gives the count of each kind of item and names the Decisions: "2 decisions for you: 16, 17."
  - Fix items are in one post, already fixed before the human sees the PR, each with its commit. To disagree with one, comment on that post with its number.
  - Note items are in one post, or a section of the Fix post. They are only things the human might act on.
  - Decision items get one thread each, on the line they concern. Resolving a thread with no comment accepts the recommended answer. This saves the human typing "ok" on every thread. A comment (a question, a disagreement, or a yes with a note) is for the orchestrator to read and act on. If the human resolves a thread that had nothing to accept, reopen it and ask.
  - Every Decision has the same shape: the question in one sentence, lettered options (a, b, …), `Recommended: <letter>` and `Resolve with no reply = <letter>`. A decision in this shape takes the human minutes; one without it can take much longer. A thread without all four parts holds up the hand-off until the reviewer rewrites it. `scripts/decision-check.py` checks this.
  - A builder implements a Decision only once it is answered: its thread is resolved, or the human's last reply names an option letter. Three or more replies from the human with no letter make a design discussion, not a decision. The orchestrator stops, says so in one line, and moves the question to the card, or to chat ending with "Waiting for your go".
- **PR titles name the card at the end,** in parentheses with the word Card: "Working conventions from the trial (Card #30)". GitHub shows the PR's own number next to the title, so a bare "(#30)" can't be told apart from it.
- **The hand-off is one line.** It uses the forms in "The human and Claude are never both idle at once" (see "Helping the human keep track"), with the PR as one link:
  - No Decisions: "1 thing for you to do right now: merge PR #N." If the PR changes how the product looks, the line ends with the preview link: `Preview: <link>`. The merge accepts the Fix items. The human opens the PR only if they want to.
  - Decisions: the count and a link to each Decision thread, for example "1 thing for you to do right now: 2 decisions on PR #N: <thread link>, <thread link>."
  - Two PRs ready at once: "2 things for you to do right now:" and a numbered list, one PR per line.
  - Say the hand-off once, when the PR is handed over, and again only when it changes, for example when a second PR is ready. Answer any question in the meantime with no first line.
  - Don't repeat the review items in chat. They are already on the PR, and repeating them doubles what the human reads.
  - Alert the human once when the PR is handed to them, with or without Decisions (see "Alert only when it's the human's turn").
- **Give the human one way to review.** Don't offer a choice of ways to review. Give each open question a recommended answer, so "ok" is a valid reply.
- **Give exact clicks.**
  - To comment on a line, use the PR's "Files changed" tab in source view (not the formatted view).
  - When Claude has its own GitHub account, its PRs aren't the human's, so the human can submit a review with **Submit review → Approve**. Without the account, Claude's PRs count as the human's own, which GitHub doesn't let them approve, so they submit with **Submit review → Comment**.
- **Several items? Overview first, then one at a time.** List them in a numbered list, one sentence each. Then take item 1: one line of context, the exact files to look at, the question. Finish it before starting item 2, and say "item 2 of 10" each time. With several complex items in one message, the context for the later ones is lost by the time item 1 is settled.
- **A question gets an answer, not an action.** When the human asks "where is it?" or "why is that?", answer and stop. If a change seems useful, say so in one line and wait. Unasked changes, such as a rewritten PR description or a new PR, cost the human time to undo.
  - When the choice doesn't matter, say "Either option is fine." first, then the default in a few words, for example "harmless: leave them".
  - Don't recommend one way, then the other, following how each question is worded. Change an answer only for a new fact, not a new question.
- **Answers in chat count.** Claude copies them to the PR.
- **Check before asking the human to act.** Before every "your turn", Claude checks:
  - every place the human needs to go, and every click
  - anything out of date in the doc
  - anything the human asked that hasn't been answered
  - that the link points to the latest version
  - every link opens, checked by a script where possible
  - the PR description matches the latest push

  Then Claude sends one short message: what to do, one link per thing, nothing else.
- **A merge or a clear yes is approval.** Praise that comes with open questions is not. Ask only when it's genuinely unclear.
- **At gates 1 and 2 (PRD and TDD), get the yes before the merge.** The hand-off is "1 thing for you to do right now: read PR #N and say yes, or what to change." Then the updates that follow approval (the approved marker, the `docs/HISTORY.md` entry, the map) go into the same PR, and the merge closes the gate's issue. Ask for the merge after the post-approval updates are in.

## Helping the human keep track

- **The human and Claude are never both idle at once.** When the orchestrator stops working, its message says who holds the ball. Stopping means the turn in which the work runs out: the last builder, reviewer or watcher action has finished. A later turn that only answers a question is not a stop. Then the message starts with one of four lines:
  - `Nothing for you to do right now. Waiting on <what>, ETA <time>.`, for example "Waiting on GitHub checks for PR #40, ETA 7:10 PM."
  - `1 thing for you to do right now: <the thing>.`
  - `N things for you to do right now:` followed by a numbered list, one thing per line, for any N.
  - `All clear: <boards> empty, no open PRs, as of <time>.`, for example "All clear: both boards empty, no open PRs, as of 9:05 PM."

  The count is the real count. Nothing is left for the human to work out from other messages, PRs or the board. The trigger is "did the work just run out?", not "is this a hand-off?": a hand-off that isn't recognized as one reads like a progress report, and then the human and Claude each wait on the other.
  - Say the line once, when the orchestrator stops working. Say it again only when the thing changes: a new PR is ready, a decision appears, or the wait ends. Repeated on every message, it turns into noise and stops being read.
  - In between, an answer to a question is just the answer, with no first line.
  - "Nothing for you to do right now" means something is running or waiting on GitHub.
  - Under the first line, one line per card in In progress and In review: what's running or what it waits on, its ETA as a clock time, and what follows. For example: "#41: the builder is applying both fixes. ETA 7:10 PM, then the PR goes to you to merge." These lines match the board; a card left out makes the message disagree with the board.
  - **All clear.** Send it once, when the last PR merges or the last card closes and nothing is open on any board or repo the orchestrator watches: no card in Todo, In progress or In review, no open PR, no agent running. Check the boards, the open PRs and the agents first. Send one desktop alert with it, the same as a hand-off. Nothing goes under it: the next step is the human's by definition, so no recommendation and never "or stop here". Don't repeat it on later stops while everything is still clear.
  - Status messages are about now. Past timings go in the card's closing comment, not in a status message. If a past number must come up, use the past tense and never "waiting on you", for example "on card #N, your part took 14 minutes". "Nothing for you to do" next to "14 minutes waiting on you" reads as a contradiction.
  - Normal case, never all caps.
  - Never a list of maybes. No "left to do" or "optional" sections.
  - The apb plugin's Stop hook checks this. When the orchestrator's work has just run out and its message doesn't start with one of the four lines, the hook sends it back once, with a quiet note to Claude that starts "Say who holds the ball". The human sees it as hook feedback, not as an error. A wake-up that changes nothing for the human is let through with no line: when a watcher or a background command started the turn, an earlier message already said who holds the ball, and the message is empty or one word. If the human sends no message for 30 minutes after a "1 thing" or "N things" line, the hook sends one desktop alert (Mac only). After "All clear" it sends none: the orchestrator's one alert is enough, and nothing else comes until the human writes.
- **Say when you notice something the human did.** A comment on a card, an answer or a resolved thread on a PR, a merge, a board move: add a line in chat, for example "I saw your merge of <link>" or "Read your comment on <link>", and what Claude is doing about it. Then the human never wonders whether Claude saw it or needs a nudge.
- **Never mention a wake-up.** A wake-up is the watcher finding something and starting a turn. The human never sees it, so "that alert" or "that wake-up" points at nothing they know. If a wake-up changes nothing for the human, for example when the watcher found a comment Claude or an agent wrote, say nothing about it: end the turn with no message, or one word. If it does change something, say what changed, for example "The reviewer posted on PR #N: one decision for you."
- **Discuss a card on the card.** Claude's answers and questions about a card go in a comment on the card or its PR, where everyone working on it, the advisor included, sees them as they come. In chat, give one line with the link, for example "My answer is in the decision 3 thread: <link>".
  - Never repeat the card's content in chat.
  - Before answering, read the card's latest comments.
  - Steps left over from a closed card (a builder's "Not done", something only the human can do) get their own card in the same turn, never only a reminder in chat.
  - Name the repo with every card or PR number when more than one repo is in play, for example `owner/repo#3`.
- **Hand-offs are short and complete.** Every place the human must go and every link they must open, and nothing they don't need: no extra context, no menu of options. Short enough to read, not skim.
  - When a message holds a question, put nothing before it that a short reply could answer. A short reply that doesn't address the question is unclear: ask again.
  - Before any "what's left for you" list, read every open PR's threads again. The human may have answered some already.
  - Mention a rebuilt map or page only when what the human would see changed, and say what changed.
  - A broad instruction never overrides an agreed step of the process. When it could, ask first.
- **Check before you state a GitHub fact.** The state of a PR, issue or card, a version, or a count such as "3 of 5" or "13 PRs" in a message to the human comes from a lookup in the same turn, never from memory. A lookup is a `gh` or `git` command, or a run of `scripts/map-artifact.py` or `scripts/progress-page.py`, which read GitHub themselves. The Stop hook checks the first kind on every message: if a sentence gives a PR, issue or card with a number (`#N`, or a link to one), or a count of them such as "13 PRs", a state ("open", "merged", "closed", "done", "in review", "on the board" or in a column) and the orchestrator ran no lookup that finished without error in that turn, it sends the message back once, with a quiet note that starts "You stated a GitHub fact without checking it this turn." A number with no state, such as "the builder opened PR #N", and the word "board" or "column" with no number aren't checked. Nor is a quoted fact (in quotation marks, in backticks or on a line starting with `>`), or a state in a plan, from "after", "once", "when", "if", "until" or "before" to the next comma, such as "once PR #N is merged, I'll rebase". The hook checks only that some lookup ran in that turn, not which one, and not whether the fact is true. A command an agent ran doesn't count.
- **A decision for the human lives on GitHub.** It goes on the PR (a review thread) or on the card, and the hand-off only links to it. The Stop hook checks this on every message: if the message asks the human for a decision and has no link to a GitHub PR or issue, it sends the message back once, with a quiet note that starts "A decision was asked without a GitHub link." A message asks for a decision when a "1 thing" or "N things for you to do right now" line names one, when it says "answer decision N", "N decisions for you", "which do you want" or "your call", when a question names one ("decide …?"), or when it lists lettered options such as "(a)" and "(b)". A line that only mentions a decision, quotes one or talks about the check asks nothing, and nothing under "Nothing for you to do right now" asks anything. A link in a quoted line (one starting with `>`) doesn't count. Under an "N things for you to do right now:" first line, each numbered item that names a decision needs the link on its own line, for example "1. 2 decisions on PR #N: <thread link>, <thread link>". "No decisions", "0 decisions" or "zero decisions" names none.
- **A hand-off never asks for something already done.** The human may resolve a thread or merge a PR between your lookup and your message. The Stop hook checks the "1 thing" line, and each numbered item under "N things", against GitHub: it looks up each decision thread the line links (`…/pull/N#discussion_rM`), and, when the line says "merge", each PR it links, or else each "PR #N" it names, in the project's own repo. If a thread is already resolved, or a PR to merge is already merged or closed, it sends the message back once, with a quiet note that starts "A hand-off asks the human for something that is already done." So link the thread itself when you ask for a decision, not only the PR. Anything it can't look up within a few seconds is let through.
- **An apb change reaches the projects already using apb in the same round.** A PR on the apb repo that touches `templates/`, `hooks/`, `agents/`, a skill's `SKILL.md` or `docs/playbook.md` can change how a project runs: its CI, its `CLAUDE.md`, its scripts, its reviewer notes. Before the hand-off, apply the change to every project in use, each in its own PR on that project's repo, and add one line to the apb PR's description: `Projects: <links to those PRs>`, or `Projects: none` when no project needs a change (a reason may follow). A project change listed under "Not in this PR" gets forgotten; the Projects line replaces it. The Stop hook checks this: when the first line hands something to the human and the message links an open apb PR that touches those paths, it looks the PR up with `gh`, and if the description has no Projects line, it sends the message back once, with a quiet note that starts "An apb PR in this hand-off changes how projects run." A PR it can't look up within a few seconds is let through.
- **Alert only when it's the human's turn.** Send one desktop alert with a sound with a "1 thing", "N things" or "All clear" line, when the human gets something new to do, in the same turn as the message. Never alert with "Nothing for you to do right now." On a Mac: `osascript -e 'display notification "Your turn: <what>" with title "Claude Code" sound name "Glass"'`. The human works in other windows while agents run, and the alert tells them when to come back.
  - Never alert for progress, check-ins or agent reports. Alerts that fire on every step look the same as the real one, so the real one gets ignored.
  - If an alert doesn't arrive, check the Mac's Focus mode first: it silences notifications.
- **Board: the latest move on top.** When a card moves to In progress, In review or Done, put it at the top of that column, so the human sees the latest movement without scrolling.
  - Done: the board's automation drops a closed card at the bottom, so after each merge, move the card to the top of Done.
  - Todo is the order the cards will be built in, with the next card on top. New cards go in oldest first, until they're sorted.
- **Never comment on the time of day or suggest stopping.** The human decides when to work.
- **Project map** (`docs/MAP.md`): every piece of the project, where it lives (GitHub, Claude artifacts, the developer's computer, outside services), what it's for and when it's used. It lists only things that rarely change. It never copies a status, such as the current stage. The board shows where the project stands. Claude checks every row after every merge, whenever a PR opens, at the end of each stage, and whenever the human asks where things stand. Whenever Claude adds a new piece (a Claude artifact, a folder, a scheduled task, an outside account), it updates the map at the same time.
  - The map lives in two places: `docs/MAP.md` and the map artifact, a Claude artifact the human can open from anywhere. Claude edits only `docs/MAP.md`, then builds the map artifact from it with `scripts/map-artifact.py` in the apb plugin and publishes it to the same link. So the two never disagree. Never edit the map artifact by hand.
  - The script's links to repo files point at the branch it was built from, and it stops if a linked file isn't on GitHub. Build it again from main after each merge.
  - The build stage in the map artifact shows how many build cards are done, for example "7 of 9 cards done". The script counts the issues labeled `build` on GitHub each time it runs; nobody copies the number by hand. A card is done when its issue is closed as completed. A card dropped by closing its issue as "not planned" counts in neither number: 10 cards, 7 finished and 1 dropped give "7 of 9".
  - At the same four moments, Claude checks the board too: every issue is in the right column, and there is an issue for the current stage.
- **Progress page:** one page that shows what's done, what's running and what's left, built by `scripts/progress-page.py` in the apb plugin and published to its own Claude artifact. The map lists the pieces; the progress page shows the status.
  - It shows who holds the ball, the stages with the build count, how many board items are in Done, the board's cards by column, each running agent with its last step and how long ago that was, each open PR with its checks and who it waits on (you, CI, the reviewer or the builder), and how long recent agents took.
  - It reads the events file and GitHub, and changes nothing. Merging, commenting and approving stay on GitHub; the page links there.
  - It is a snapshot. The orchestrator builds and publishes it again each time it wakes up (a watcher exits, an agent reports back), each time it says who holds the ball, and whenever the human asks where things stand. The page shows when it was built.
- **New ideas that don't belong in the current work** go in the backlog: one issue each, labeled `backlog`, not on the board. How to decide whether an idea belongs, write it up, go back over the backlog, rank it and choose what to build next: `docs/product-guide.md` in the apb plugin.
- **Project history** (`docs/HISTORY.md`): one entry per stage, oldest first, with the date, the PR and what was decided, so someone new can see what happened.
- **One question at a time.** Settle every part of the current question before asking the next. Ask in the chat, not with pop-up buttons, so the human can answer in their own words.
- **Plain English.** No made-up terms, no unexplained jargon, one instruction per sentence. This holds for docs that only Claude reads too.
  - Keep one name for each thing. A new word for something already named makes the human think there is something new to learn.
  - When explaining a plan or a trade-off, give every step in order. One step left out makes a simple point confusing.
- **Show everything visual.** Mockups, generated images and layouts go on a page the human can see, including the ones Claude rejected and why. The human makes the final call on how things look.
- **One link per thing.** Don't link files or folders the human doesn't need to open.

## What the orchestrator does itself

- **Run commands; don't hand them to the human.** Only approvals, merges, sign-ins and app restarts are the human's. Handing over a command the orchestrator could run costs the human's time for nothing.
- **Before calling a step the human's, check whether the orchestrator can do it.** Steps sitting "waiting on you" that Claude could do are lost time.
  - Web dashboards can be done in Claude in Chrome, where the human is usually signed in. If a site needs the human's login, ask them to log in, then do the rest.
  - The repo's own commands run from the orchestrator's shell.
  - If a step waits on the human only because of a process rule, say "our rule says only you do X; you can overrule it". Never say "I can't".

## Work that has to be spread out over time

Some work can't be done in one sitting. Claude does it in small pieces on a schedule instead.

When that happens:
- **A service limits how much you can do in a given time.** For example, making lots of generated content, such as placeholder images. ChatGPT's image tool made a limited number of images in a row, then refused until a reset time it named, often an hour or more later. Near the limit it also got worse, putting several images into one picture. A batch of up to four images every 20 minutes stayed under the limit.
- **Waiting on something outside the project.** For example, an app store review, a new domain name starting to work, or a slow job at another service. A task checks every so often and carries on when it's ready.
- **Recurring checks.** For example, a weekly check for broken links on the live product, or for outdated libraries.
- **A long job that outlasts one session.** For example, processing thousands of files in chunks, or downloading from a site that allows only so many requests a minute.

How to set it up:
- **Use a scheduled task.** The Claude desktop app has a Scheduled tasks page. Tasks that run on your computer run only while it's awake and online.
- **Write down the steps (a runbook) and keep a log.** Every run starts fresh with no memory of the last one. The task's instructions point to the runbook, and each run adds a line to the log.
- **Keep a list of every item to do.** That list is the record of what's done and what's next.
- **Do one small batch per run, and check what already exists first.** Then a run that happens twice does no harm.
- **On a limit or an error, stop at once.** Log it, and leave the work for the next run.
- **Give the task a stop condition.** Each run first checks whether everything is finished. If it is, the run pauses the task itself (a scheduled task can do that) and notifies the human. Logging "done" isn't enough: nobody reads the log, and the task keeps running.
- **Add a backup stop.** If several runs in a row find nothing to do, pause and notify anyway, in case the finish check is wrong.

For generated content, also:
- **Put information the product needs in the file name,** for example the date: `2025-10-07.jpg`. Don't rely on file dates; git changes them.
- **Keep a review page** that shows everything made so far, plus the rejected items and why, updated after each batch.
- **Check images on a real page.** Color profiles and near-white backgrounds can show as a gray box on a white page. Convert to sRGB and turn near-white into pure white.

## While Claude waits

The orchestrator acts only when something wakes it: a message from the human, an agent's report, or a background command finishing. A merge on GitHub or a frozen test run wakes nothing. So the orchestrator never waits without the watcher, and never states anything it hasn't just checked.

- **The watcher** is `scripts/apb-watch.py` in the apb plugin. The watcher checks every 20 seconds. It exits, saying what it found, when:
  - a PR is opened, merged or closed, its checks finish, it gets new comments, one of its review threads is resolved (it names the thread with a link), or all of them are. A merged or closed PR stays watched while it has an unresolved thread, since the human may resolve a Decision after the merge.
  - an issue named with `--issue N` gets a new comment
  - a command started from Claude's shells has used no CPU for 10 minutes
  - a running agent has written nothing to the events file for 15 minutes
  - 5 minutes have passed, with `--check-in 5`. Use it while agents run, to compare their progress with the time they should take.
  - an hour has passed with no change, so it never runs forever.
- **The plugin starts it.** A hook, `hooks/apb-watch-start.py`, keeps the watcher running for the orchestrator's session. It starts the watcher at the session's start, and again on the next prompt, tool call or stop whenever it isn't running. Builders' and reviewers' tool calls don't start it.
  - The coordination issue is the issue where the orchestrator and the advisor talk. It comes from a line in the project's `CLAUDE.md`: `coordination: #N` for an issue in the project's repo, or `coordination: owner/name#N` for an issue in another repo.
  - With `#N`, or with no such line, the hook runs one watcher, on the project's repo and the issue, if there is one. A line that names the project's own repo as `owner/name#N` counts as `#N`.
  - With `owner/name#N`, the hook runs two watchers: one on the project's repo (its PRs, stuck commands and quiet agents), and one on the other repo (its PRs and the issue). Only one copy of each runs.
  - When a watcher reports something, the hook wakes the orchestrator with the report. When it reports no change, the hook starts it again at once. When it fails, the hook waits a minute before trying that watcher again.
  - Each start writes a `watcher-start` line to the events file, and each exit a `watcher-exit` line with the reason. Both name the watcher: `project` or `coordination`.
- **The orchestrator's own watcher.** While agents run, the orchestrator also runs the watcher itself, in the background in its own shell, with `--check-in 5` and `--issue N` for the card's issue, to compare the agents' progress with the time they should take.
- **What the watcher doesn't do.** It only reads. Stopping a stuck process is the orchestrator's call. It saves what it saw in its `--state` file, so a change made while it was stopped is reported on the next start, and nothing is reported twice.
- **After each exit,** the orchestrator acts on what the watcher printed. It starts its own watcher again; the hook restarts its watchers by itself.
- **Fix it, then tell the human.** If something is stuck, the orchestrator stops it, restarts the step, and then tells the human. It never waits for the human to ask.
- **Check before stating.** Every status, time or "still waiting" comes from a check in the same turn: `gh pr view` for a PR, `date` for a clock time. If nothing was checked, say "I don't know yet".
- **Say when each step should finish.** Each status line gives the time the step should finish, worked out from `date`: an ETA as a clock time, for example "ETA 2:40 PM", not "about 10 minutes". The "Nothing for you to do right now" first line gives its ETA the same way. GitHub shows no progress while checks run, so without it the human can't tell whether to wait 2 minutes or 20.
  - Base the guess on how long the same kind of step took last time. The events file has the times.
  - When the card closes, run `scripts/card-times.py` in the apb plugin, from the project's root: `card-times.py N --post --guessed "<the times you guessed>"`. It posts one comment on the issue with the card's times, worked out from the events file and GitHub: build (the builder's start to the PR opening), review (the PR opening to the first hand-off to the human), human (the sum of the human's waits: from each hand-off to the human's next message or comment, and from the last hand-off to the merge, so a builder's second pass after a hand-off is not the human's time), how many hand-offs to the human the PR had (a card with a Decision usually has 2, one for the Decision and one for the merge, so more than that means the PR came back after a hand-off), and how many of the human's messages from the first hand-off to the merge were more than "go", a decision letter, "resolved" or "merged". Later guesses improve from these, and `card-times.py --collect` lists every card's numbers in one table for a retrospective.
  - Run `date` in the same turn as any clock time you state. A time from an earlier turn is stale.
- **An honest task list.** If the app shows a task list, keep it current on every turn while any agent runs: what's done, what's running and what's next, with one step in progress. The human follows the work there. When the orchestrator stops, the task list shows no step running. Its last line says what it's waiting for, and since when.
- **Time limits in code.** Every child process the code or tests start has a time limit. Without one, a single stuck child stalls the whole run, and the test runner's own limit can't fire. The TDD's test plan says how each one gets its limit, and the reviewer checks it.
- **Deadlines for background runs.** Before a builder or reviewer starts a run in the background, it notes how long the run should take and a deadline, about twice that. Past the deadline, it checks the run's processes, stops what's stuck, and reports it.

## Unattended builds

The build can run with the human watching, or unattended, for example overnight. Unattended runs need these rules:

- **Saying "go" doesn't start anything.** The kickoff must actually start a background worker (Agent Teams, or a worktree per feature), and Claude names what it started.
- **A new commit every 30–60 minutes is the only sign the work is running.** Not a status message, not "the process is alive".
- **Write the stopping rules into the kickoff:** stop when every feature is accepted, or after N tries with no test improvement (write down why), or at a maximum number of tries.
- **A scheduled check watches the run:** it looks at how recent the last commit is and answers open questions on the issues. If it can restart a stuck worker, it uses the same commit-age rule.
- **Questions go on the issue.** A question for the human or the advisor is a comment on the card's GitHub issue. A question asked only in chat gets lost. Claude never passes messages through the human.
- **Only the worker writes to the build log** while it runs.

## The build record

Every project keeps a public record of how it was built:

- **`LOG.md`**: one line per build step (what changed, test results). Never edit old lines. The line goes in the same commit as the code: pushed on its own, it starts GitHub's checks again.
- **`LESSONS.md`**: what the debugging and testing taught, written as it happens.
- **A final writeup**: how long it took, what it cost, number of PRs and tests, what worked and what didn't.

## The events file

The apb plugin keeps a log of what every Claude session and agent does in the project: `.apb/events.jsonl`. It writes it only in an apb project, which is a project with `docs/MAP.md`. Git ignores the folder.

- **What it holds:** one line each time a prompt starts a turn: a message from the human, or one Claude Code sends itself, such as a background agent reporting back; an agent starts or stops; a shell command finishes, a checklist item is created or done, a turn ends, or Claude Code shows a notification, such as a permission prompt. The watcher hook adds a line each time a watcher starts or exits (`watcher-start`, `watcher-exit`).
- **What each line holds:** the time, the event, the session, and the agent's id and type. For a shell command, also the command and its exit status, but not its output. For a prompt, not its text: only whether Claude Code sent it itself, such as a notice or an agent's report (`automatic`), or, for the human's, whether it is a routine reply (`routine`): "go", a decision letter, "resolved" or "merged". For a turn's end, who holds the ball, if the message said so, whether the Stop hook sent the message back, and, if it was for an unchecked GitHub fact, which facts (`unchecked`), or, if it was for a decision asked without a link, `decision-without-link`, or, if it was for a hand-off of something already done, what was done (`already-done`), or, if it was for apb PRs with no Projects line, which PRs (`projects-line-missing`). Count those lines to see how often stale facts, unlinked decisions, stale hand-offs or forgotten projects nearly reached the human.
- **Who reads it:** the orchestrator, the watcher, the progress page and the card times (`scripts/card-times.py`). The orchestrator reads the file to see what the builder and reviewer are doing, to notice a step that has gone quiet, and to time each step. Agents' own time estimates are often wrong; the file's times are not.

## Testing and models

- **For logic-heavy code, go beyond example tests:**
  - property-based tests checked against an independent second implementation
  - mutation testing to measure how good the tests are. It runs automatically, as the TDD sets it up.
  - a deliberate-bug check: by hand, separate from mutation testing, put one real bug back in, to prove the tests catch it. Do it once per project, on the first build PR with real logic. It shows early whether the tests catch real bugs, while there is still time to fix them.
- **Run each test only where it's needed.** Most of a build's time goes to running tests.
  - While building, the builder runs only the tests for the code it's changing, in one browser at one screen width.
  - Before pushing, it runs the test files it changed in every browser, at one width.
  - On each push to a PR, GitHub runs the quick set: the whole browser suite in one browser at one width, plus the changed test files in every browser at one width.
  - GitHub runs the full set (every browser, every width for the width-specific tests) once per PR: the orchestrator adds the `full-tests` label just before the human sees the PR. It runs again on every push to main.
  - The reviewer doesn't run the tests again; it checks the builder's untested points on the preview.
  - Tests that depend on screen width run at every width. The rest run at one width.
  - A push that changes only docs skips the browser tests.
  - Never start a test suite while another one is running.
- **Strongest model for thinking, faster model for typing.** Planning, the PRD and TDD, and reviews run on the strongest model available. Writing lots of code runs fine on a faster model. Claude Code usually picks well by default.

## One-time setup

1. **Claude Code:** [quickstart](https://code.claude.com/docs/en/quickstart). Worth reading once: [Claude Code best practices](https://www.anthropic.com/engineering/claude-code-best-practices).
2. **GitHub:** [Hello World guide](https://docs.github.com/en/get-started/start-your-journey/hello-world) for repos, commits, PRs and merges. In apb, Claude does all of these when asked.
3. **Hosting:** chosen in the TDD and connected right after it's approved. [Cloudflare Pages](https://developers.cloudflare.com/pages/) is the usual choice: free, deploys `main` automatically, and gives every PR a preview site.

## Related approaches

apb's PRD and TDD are a written spec, the same idea as "spec-driven development" and [GitHub Spec Kit](https://github.com/github/spec-kit). apb writes the spec as plain prose with checkable requirements instead of using Spec Kit's templates.

Claude Code features apb can use but doesn't depend on: Agent Teams, hooks that run lint and tests automatically, and plugins.
