"""Moves a card on the board as part of the step that needs it, so the move can't be skipped (#121).

Where a pair of actions must always happen together, one command does both. The orchestrator owns
every board move, and builders never run this script (the playbook's "Who does what"); the one move
made for them is the plugin's hook moving a card when `gh pr ready` succeeds (#156).

Usage, from the project's repo root:
    python3 <apb plugin folder>/scripts/board-move.py start-build <issue> [--repo owner/name]
        The first half of starting a build: moves the card to the top of In progress, then says to
        launch the builder (an agent or a separate session). It refuses a closed card or one not on
        the board. Run it before every build; there is no other way to start one.
    python3 <apb plugin folder>/scripts/board-move.py pr-ready <PR> [--repo owner/name]
        When a builder marks its PR ready for review: moves each card the PR closes to the top of In
        review. It refuses a draft (#156: a draft's card stays In progress), a PR that isn't open, and
        one that closes no card. The plugin's hook (hooks/apb-pr-ready.py) runs it the moment
        `gh pr ready` succeeds, wherever it runs; run it by hand for a PR marked ready another way.
    python3 <apb plugin folder>/scripts/board-move.py pr-draft <PR> [--repo owner/name]
        The other direction (#156): when a PR is a draft again (`gh pr ready --undo`, or its card went
        to In review too early), moves each card it closes back to the top of In progress. It refuses
        a PR ready for review, one that isn't open, and one that closes no card.

Each card goes to the top of its new column ("Board: the latest move on top"): it sets the Status field
with updateProjectV2ItemFieldValue, then moves the card with updateProjectV2ItemPosition and no
afterId. The board is the repo's first project, as for the startup self-check. It exits with 1 when it
refuses, 2 when gh fails or times out.
"""
import argparse
import json
import subprocess
import sys
import time

TIME_LIMIT = 30  # seconds for each gh call

BOARD = """projectsV2(first: 1) { nodes { id field(name: "Status") {
      ... on ProjectV2SingleSelectField { id options { id name } } } } }"""
ISSUE = """query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {
  %s
  issue(number: $number) { state projectItems(first: 20) { nodes { id project { id } } } } } }""" % BOARD
PR = """query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {
  %s
  pullRequest(number: $number) { state isDraft closingIssuesReferences(first: 10) { nodes { number } } } } }""" % BOARD
SET_STATUS = """mutation($project: ID!, $item: ID!, $field: ID!, $option: String!) {
  updateProjectV2ItemFieldValue(input: {projectId: $project, itemId: $item, fieldId: $field,
    value: {singleSelectOptionId: $option}}) { clientMutationId } }"""
TO_TOP = """mutation($project: ID!, $item: ID!) {
  updateProjectV2ItemPosition(input: {projectId: $project, itemId: $item}) { clientMutationId } }"""


class Refused(Exception):
    """The move doesn't fit what GitHub says; nothing was changed."""


def asker(gh="gh", budget=None, time_limit=TIME_LIMIT):
    """A function that asks GitHub one GraphQL query with gh and returns its answer: each call within
    time_limit seconds and, with a budget, all of them within budget seconds from now (after that,
    subprocess.TimeoutExpired)."""
    end = None if budget is None else time.time() + budget

    def ask(query, **fields):
        limit = time_limit
        if end is not None:
            limit = min(limit, end - time.time())
            if limit <= 0:
                raise subprocess.TimeoutExpired(gh, budget)
        args = [gh, "api", "graphql", "-f", "query=" + query]
        for k, v in fields.items():
            args += ["-F" if isinstance(v, int) else "-f", "%s=%s" % (k, v)]
        out = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True,
                             timeout=limit)
        answer = json.loads(out.stdout)
        if answer.get("errors"):
            raise subprocess.CalledProcessError(1, args, stderr=json.dumps(answer["errors"])[:300])
        return answer
    return ask


def gh_graphql(query, **fields):
    """GitHub's answer to one GraphQL query, from gh."""
    return asker()(query, **fields)


def _board(repository):
    projects = ((repository.get("projectsV2") or {}).get("nodes")) or []
    if not projects or not (projects[0].get("field") or {}).get("options"):
        raise Refused("the repo has no board with a Status field")
    return projects[0]


def _option(board, column):
    for o in board["field"]["options"]:
        if o["name"].lower() == column.lower():
            return o["id"]
    raise Refused("the board has no %s column" % column)


def _move(ask, board, item, column):
    ask(SET_STATUS, project=board["id"], item=item, field=board["field"]["id"], option=_option(board, column))
    ask(TO_TOP, project=board["id"], item=item)


def _item(ask, owner, name, number, project_id=None):
    """(board, the card's item id on it). Refused when the issue is closed or not on the board."""
    repo = ask(ISSUE, owner=owner, name=name, number=number)["data"]["repository"]
    issue = repo.get("issue")
    if not issue:
        raise Refused("#%d is not an issue in this repo" % number)
    board = _board(repo)
    items = [i["id"] for i in ((issue.get("projectItems") or {}).get("nodes") or [])
             if (i.get("project") or {}).get("id") == board["id"]]
    if not items:
        raise Refused("card #%d is not on the board" % number)
    return board, items[0], issue.get("state")


def start_build(repo, number, ask=gh_graphql):
    """Move the card to the top of In progress. Returns what to say next."""
    owner, name = repo.split("/", 1)
    board, item, state = _item(ask, owner, name, number)
    if state != "OPEN":
        raise Refused("card #%d is closed: there is nothing to build" % number)
    _option(board, "In progress")
    _move(ask, board, item, "In progress")
    return ("Card #%d is at the top of In progress. Now launch the builder on it (the plugin's builder agent, "
            "or a build session), with its own worktree from freshly fetched main." % number)


def _move_pr_cards(ask, repo, number, draft, column):
    """Move each card PR number closes to the top of column, if the PR is open and is a draft exactly
    when draft is True. Returns the cards moved."""
    owner, name = repo.split("/", 1)
    repository = ask(PR, owner=owner, name=name, number=number)["data"]["repository"]
    pr = repository.get("pullRequest")
    if not pr:
        raise Refused("PR #%d doesn't exist in %s" % (number, repo))
    if pr.get("state") != "OPEN":
        raise Refused("PR #%d is %s, not open" % (number, (pr.get("state") or "?").lower()))
    if bool(pr.get("isDraft")) != draft:
        if draft:
            raise Refused("PR #%d is ready for review, not a draft: its card belongs In review (pr-ready %d)"
                          % (number, number))
        raise Refused("PR #%d is still a draft: its card stays In progress until the builder marks it ready (#156)"
                      % number)
    cards = [c["number"] for c in ((pr.get("closingIssuesReferences") or {}).get("nodes") or []) if c]
    if not cards:
        raise Refused("PR #%d closes no card: add \"Closes #N\" to its description first" % number)
    moves = [_item(ask, owner, name, c) for c in cards]  # every card found before any moves
    for board, item, _ in moves:
        _option(board, column)
    for board, item, _ in moves:
        _move(ask, board, item, column)
    return cards


def pr_ready(repo, number, ask=gh_graphql):
    """Move each card the PR closes to the top of In review. Returns what to say next."""
    cards = _move_pr_cards(ask, repo, number, False, "In review")
    return "Moved %s to the top of In review. Now run apb-review on PR #%d." % (
        ", ".join("#%d" % c for c in cards), number)


def pr_draft(repo, number, ask=gh_graphql):
    """Move each card a draft PR closes back to the top of In progress (#156). Returns what to say next."""
    cards = _move_pr_cards(ask, repo, number, True, "In progress")
    return ("Moved %s back to the top of In progress: PR #%d is a draft. It goes to In review when the "
            "builder marks it ready (gh pr ready)." % (", ".join("#%d" % c for c in cards), number))


STEPS = {"start-build": start_build, "pr-ready": pr_ready, "pr-draft": pr_draft}


def main(argv):
    parser = argparse.ArgumentParser(description="Move a card as part of the step that needs it.")
    parser.add_argument("step", choices=list(STEPS))
    parser.add_argument("number", type=int, help="the card's issue (start-build) or the PR (pr-ready, pr-draft)")
    parser.add_argument("--repo", help="owner/name; by default, this folder's repo")
    args = parser.parse_args(argv)
    try:
        repo = args.repo or json.loads(subprocess.run(
            ["gh", "repo", "view", "--json", "nameWithOwner"], capture_output=True, text=True, check=True,
            timeout=TIME_LIMIT).stdout)["nameWithOwner"]
        said = STEPS[args.step](repo, args.number)
    except Refused as e:
        print("Not moved: %s." % e, file=sys.stderr)
        return 1
    except subprocess.CalledProcessError as e:
        print("gh failed: " + (e.stderr or e.stdout or "").strip(), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
        print("gh failed: %s" % e, file=sys.stderr)
        return 2
    print(said)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
