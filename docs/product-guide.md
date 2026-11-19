# Product guide: the backlog, priorities and the next piece of work

New ideas come up all the time while a project is being built: in a review, while reading the PRD, while checking the live product. Most of them don't belong in the work under way. This guide helps the human decide which ones do, keep the rest without losing them, and choose what to build next. The human makes every product decision here; Claude proposes, with a recommended answer, and does the writing and the GitHub steps.

## Out of scope or not

Test each new idea against the approved PRD, not against how good the idea is. Ask, in this order:
1. **Can the current card meet its acceptance criteria without it?** If not, it is part of the current card. Do it in the current PR.
2. **Does a requirement in the approved PRD need it?** If yes, but the current card can pass without it, it is a separate piece of work. It becomes its own build card (see "The next piece of work"), not an addition to the current PR.
3. **Neither?** It is out of scope. Write it up in the backlog and carry on with the current card.

An idea that changes the approved PRD or TDD is out of scope until the human decides to change them. Their approved wording changes only by the human's explicit choice, so a good idea doesn't slip in through a PR.

The builder doesn't decide this alone. When it finds an idea outside its issue, it leaves it out of the PR and names it in its final report (`agents/builder.md` says so). The orchestrator raises these ideas only in the card's hand-off to the human, never in the middle of a card: the hand-off is the one place a card's new ideas come up. There it asks about each idea in one line with a recommended answer, and writes the idea up in the backlog if the human agrees it is out of scope. Writing it up takes a minute and loses nothing; arguing about it in the middle of a card costs the card its focus.

## The backlog

The backlog is the list of ideas the project might do later. Each idea is one GitHub issue labeled `backlog`. Create the label once per project: `gh label create backlog --force`. Then write each idea up in a file and create its issue with `gh issue create --label backlog --title "<the result>" --body-file <file>`.

A backlog issue is written so that someone who wasn't there can act on it months later:
- **Title:** the result someone gets, for example "Visitors can filter the gallery by month", not "filter idea".
- **The idea:** one or two sentences on what would change.
- **Where it came from:** a link to the comment, PR, review item or issue where it came up, so nothing depends on a chat that is gone.
- **Why it matters:** who gains what. If nobody gains much, say so; it helps the ranking later.
- **Size:** small (one card), medium (a few cards) or large (a PRD of its own). Compare it with closed cards of the same kind (see "Priorities").
- **Open questions:** what isn't known yet, if anything.

One idea per issue. Two ideas in one issue can't be ranked or closed separately.

## Grooming

Grooming means going back over the whole backlog and keeping it true. Do it at the end of each stage, and before choosing the next piece of work. Claude does the reading and proposes each change; the human says yes or no in one reply.

For each open backlog issue:
- **Update** it when something has changed: a closed card now does part of it, the size looks different, or an open question has an answer. Edit the issue body, and add a comment saying what changed.
- **Merge** two issues that describe the same idea. Keep the clearer one, copy anything missing into it, and close the other as "not planned" with a comment linking the one kept.
- **Drop** an idea nobody needs any more. Close it as "not planned", with one line saying why. A closed issue keeps the record of what was decided, so the same idea isn't argued again.

List the open backlog with `gh issue list --label backlog`.

## Priorities

Rank by value first. For each idea, ask:
- Who gains, and how much? An idea that every user meets beats one that a few users meet once.
- Does it remove a risk, such as data loss, a slow page or a broken step?
- Does other work wait on it?

Then check the size. Find closed cards like it and look at what they took: `python3 <apb plugin folder>/scripts/card-times.py --collect`, run from the project's root, lists the build, review and human minutes of every card whose times were posted. "How long did cards like this take" is then a lookup, not a guess. Between two ideas of about the same value, the smaller one goes first.

Write the ranking down as one numbered list, best first, one line per idea: the link, the value in a few words, the size. Keep it in the body of one pinned issue titled "Backlog ranking", and update it at each grooming; GitHub keeps every earlier version. Add the ranking issue to the project's `docs/MAP.md`. The human sets the final order. For example:

```
1. #41 Visitors can filter the gallery by month: every visitor, small
2. #38 Pages load in under a second on a phone: every visitor on a phone, medium
3. #45 The owner can upload several images at once: the owner only, small
```

## The next piece of work

Choose the next piece of work when Todo on the board is down to one card, or when the human asks what to build next. Waiting until Todo is empty would leave a builder with nothing to start. Then:
1. Groom the backlog (see "Grooming") and take the top of the ranking.
2. Check that it is still wanted and that nothing it needs is missing.
3. Check its size. A small idea becomes one build card. A medium one is split into cards, one PR each. A large one starts its own round: a PRD, then a TDD, with their gates (see the playbook's "The steps").
4. If it changes the approved PRD or TDD, the human approves that change first.
5. The orchestrator proposes it to the human in one line with a recommended answer, and waits for the yes.
6. Turn it into a build card: `gh issue edit N --add-label build --remove-label backlog`, then add it to the board with `gh project item-add`, in Todo, in the place the build order needs. Add the acceptance criteria, and the Needs and "Can be built at the same time as" lines, as `skills/apb-board/SKILL.md` describes for every build card.

An issue is either a backlog idea or a build card, never both. The map artifact counts the issues labeled `build` as the build stage's cards, so an idea must not carry `build` until it is chosen.

## How the backlog shows on the board

The board's columns are Todo, In progress, In review and Done. The backlog gets no column of its own, and backlog issues are not on the board. The board shows the work that has been chosen; the backlog holds the work that hasn't. A board with every idea on it no longer shows where the project stands.

To see the backlog, open the repo's issue list filtered by the label: `https://github.com/<owner>/<repo>/issues?q=is:open+label:backlog`. Link that filtered list from the project's `docs/MAP.md`, as a live list rather than a copy. An idea goes on the board, in Todo, only once it is chosen (see "The next piece of work").
