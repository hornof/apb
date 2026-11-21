"""Finds a stale "your turn" flag: a card or PR assigned to the human with nothing left waiting on them.

The orchestrator assigns a card's issue to the human (and the PR, when there is one) whenever the card
waits on them, and unassigns them as soon as they act (the playbook's "Assign the card to the human
while it waits on them"). Nothing else uses the assignee. This script is the guard: the orchestrator runs
it on every status check, and clears each flag it reports as stale.

A PR assigned to the human waits on them while it is open (to merge, or to decide) or while it has an
unresolved review thread (a Decision). A card assigned to the human waits on them while a PR that closes
it ("Closes #N") is open or has an unresolved thread, or while the card is open and its last comment is a
question from Claude: a comment by Claude (comment-author.py's rule) with a line ending in "?" or saying
"Waiting for your go", outside quoted lines. Anything else assigned to the human is stale.

Usage, from the project's repo root:
    python3 <apb plugin folder>/scripts/human-turn.py [--repo owner/name] [--human login] [--claude-login login]

The human is the repo's owner unless --human names them (needed when an organization owns the repo).
It prints one line per card or PR assigned to the human, "Waiting on <login>: ..." or "Stale: ...",
with the command that clears a stale flag. It exits with 1 if any flag is stale, 2 if gh fails.
It reads GitHub and changes nothing.
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

_spec = importlib.util.spec_from_file_location(
    "comment_author", os.path.join(os.path.dirname(os.path.abspath(__file__)), "comment-author.py"))
authors = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(authors)

TIME_LIMIT = 60  # seconds for each gh call

QUERY = """query($q: String!) { search(query: $q, type: ISSUE, first: 100) { nodes {
  __typename
  ... on PullRequest { number state reviewThreads(first: 100) { nodes { isResolved } } }
  ... on Issue { number state comments(last: 1) { nodes { body author { login } } }
    closedByPullRequestsReferences(first: 20, includeClosedPrs: true) { nodes {
      number state reviewThreads(first: 100) { nodes { isResolved } } } } } } } }"""


def search_terms(repo, human):
    """Every issue and PR in the repo assigned to the human, open or closed: a merged card keeps the flag
    until someone clears it."""
    return "repo:%s assignee:%s" % (repo, human)


def asks(body):
    """True if the comment asks the human something: a line ending in "?" or "Waiting for your go",
    outside quoted lines."""
    for line in (body or "").splitlines():
        line = line.strip()
        if line.startswith(">"):
            continue
        if line.endswith("?") or re.search(r"waiting for your go", line, re.I):
            return True
    return False


def _pr(node):
    threads = (node.get("reviewThreads") or {}).get("nodes") or []
    return {"kind": "pr", "number": node["number"], "state": node["state"],
            "unresolved": sum(1 for t in threads if not t.get("isResolved"))}


def items(answer):
    """The cards and PRs in GitHub's answer to QUERY, in the shape verdict() takes."""
    out = []
    for node in answer["data"]["search"]["nodes"] or []:
        if not node:
            continue
        if node.get("__typename") == "PullRequest":
            out.append(_pr(node))
        elif node.get("__typename") == "Issue":
            last = ((node.get("comments") or {}).get("nodes") or [None])[-1]
            prs = ((node.get("closedByPullRequestsReferences") or {}).get("nodes")) or []
            out.append({"kind": "issue", "number": node["number"], "state": node["state"],
                        "last_comment": None if not last else {
                            "body": last.get("body"), "author": (last.get("author") or {}).get("login")},
                        "prs": [_pr(p) for p in prs if p]})
    return out


def _pr_waits(p):
    """Why the PR still waits on the human, or None."""
    if p["state"] == "OPEN":
        return "PR #%d is open" % p["number"]
    if p["unresolved"]:
        return "PR #%d has %d open Decision thread%s" % (p["number"], p["unresolved"], "" if p["unresolved"] == 1 else "s")
    return None


def verdict(item, claude_login=None):
    """("waiting", why) or ("stale", why)."""
    if item["kind"] == "pr":
        why = _pr_waits(item)
        return ("waiting", why) if why else ("stale", "PR #%d is %s with no open Decision thread"
                                             % (item["number"], item["state"].lower()))
    for p in item["prs"]:
        why = _pr_waits(p)
        if why:
            return "waiting", why
    last = item["last_comment"]
    if item["state"] == "OPEN" and last and authors.is_claude(last["body"], last["author"], claude_login) \
            and asks(last["body"]):
        return "waiting", "Claude's question on the card is unanswered"
    if item["state"] != "OPEN":
        return "stale", "the card is closed, and no PR that closes it is open or has an open Decision thread"
    return "stale", "no open PR closes it, no open Decision thread, and no unanswered question from Claude on it"


def report(found, human, repo, claude_login=None):
    """(lines, how many flags are stale)."""
    if not found:
        return ["Nothing is assigned to %s in %s." % (human, repo)], 0
    lines, stale = [], 0
    for it in found:
        name = ("card #%d" if it["kind"] == "issue" else "PR #%d") % it["number"]
        kind, why = verdict(it, claude_login)
        if kind == "waiting":
            lines.append("Waiting on %s: %s: %s." % (human, name, why))
        else:
            stale += 1
            command = "gh %s edit %d --repo %s --remove-assignee %s" % (
                "issue" if it["kind"] == "issue" else "pr", it["number"], repo, human)
            lines.append("Stale: %s is assigned to %s, but %s. Clear it: %s" % (name, human, why, command))
    return lines, stale


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=TIME_LIMIT).stdout


def main(argv):
    parser = argparse.ArgumentParser(description="Find cards and PRs assigned to the human with nothing waiting on them.")
    parser.add_argument("--repo", help="owner/name; by default, this folder's repo")
    parser.add_argument("--human", help="the human's GitHub login; by default, the repo's owner")
    parser.add_argument("--claude-login", help="Claude's own GitHub login; by default, from the project's settings")
    args = parser.parse_args(argv)
    try:
        repo, human = args.repo, args.human
        if not repo or not human:
            view = json.loads(run(["gh", "repo", "view"] + ([repo] if repo else []) +
                                  ["--json", "nameWithOwner,owner"]))
            repo = repo or view["nameWithOwner"]
            human = human or view["owner"]["login"]
        answer = json.loads(run(["gh", "api", "graphql", "-f", "q=" + search_terms(repo, human),
                                 "-f", "query=" + QUERY]))
        lines, stale = report(items(answer), human, repo, authors.claude_login(args.claude_login))
    except subprocess.CalledProcessError as e:
        print("gh failed: " + (e.stderr or e.stdout or "").strip(), file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired:
        print("gh failed: no answer in %d seconds" % TIME_LIMIT, file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 1 if stale else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
