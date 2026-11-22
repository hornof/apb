"""The end-of-turn consistency check (#121): rules a script can check get enforced, not just written.

apb's rules used to live only as prose, and Code skipped them on any path the rule wasn't attached to.
This script compares the board with GitHub, and with the builds running on this computer, and names
everything that disagrees. The Stop hook (hooks/apb-stop.py) runs it when the orchestrator ends a turn
that hands something over or starts an agent, and sends the turn back once with what to fix. A GitHub
error never blocks: the hook lets the turn end and says the check couldn't run.

The flywheel rule (playbook, "Rules a script can check"): when Code breaks a rule a script could check,
the fix is a new case here, with a test built from the incident, not another sentence of prose. Each
case in CASES names the incident that added it.

The cases:
- build-in-todo: a build is running for a card (a git worktree on a branch named issue-<N>, or an
  open PR, draft or not, that closes the card or comes from such a branch), but the card is in Todo.
- pr-ready-not-in-review: a PR ready for review (not a draft) closes an open card that isn't in In review.
- in-review-with-draft-pr: a card is in In review, and every open PR that closes it is a draft (#156: a
  card moves to In review only when its PR is marked ready).
- board-vs-issue: a card is Done but its issue is open, or its issue is closed but it isn't Done.
- hand-off-not-assigned: the turn hands the human a PR or a card (a merge, a Decision, a question on
  the card), and that PR, or the card a handed PR closes, isn't assigned to the human.
- stale-assignment: a card or PR is assigned to the human with nothing waiting on them
  (human-turn.py's rule, #111).
- resolved-decision-unconfirmed: a Decision thread on an open PR is resolved, and Claude hasn't replied
  in it "Resolved = <letter>: <what happens now>".
- reply-in-unanswered-decision: someone other than the human (the orchestrator, the reviewer, the
  advisor) replied in a Decision thread the human hasn't answered or resolved.
- merge-with-open-request: the turn asks the human to merge a PR while an advisor comment on it has no
  later reply from Claude, or the reviewer's Fix post has an item with no commit and no reason it
  wasn't fixed.
- decision-without-advisor-comment: in a project with an advisor, the turn hands the human a Decision
  on a PR that has no PR-level advisor comment posted after its Decisions.

A Decision is a review thread whose first comment is Claude's and has lettered options or
"Resolve with no reply". Claude's comments are told from the human's by comment-author.py.

Usage, from the project's repo root:
    python3 <apb plugin folder>/scripts/consistency-check.py [--repo owner/name] [--human login]
        [--claude-login login] [--project folder]

It prints one line per problem, or that everything is consistent, and exits with 1 if it found a
problem, 2 if it couldn't run (gh failed or timed out). It reads GitHub and changes nothing.
It uses one GraphQL query, plus one per further 100 board cards.
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


authors = _load("comment_author", "comment-author.py")
human_turn = _load("human_turn", "human-turn.py")
decisions = _load("decision_check", "decision-check.py")

TIME_LIMIT = 20  # seconds for one gh call when run by hand
PAGES = 5  # board pages read, 100 cards each

CASES = [
    {"id": "build-in-todo",
     "incident": "2026-11-20: #117 and #118 were built as separate sessions while their cards sat in Todo (#121)"},
    {"id": "pr-ready-not-in-review",
     "incident": "#121: a PR ready for review closes a card that isn't In review"},
    {"id": "in-review-with-draft-pr",
     "incident": "2026-11-21: #153 and #140 went to In review while their PRs #154 and #155 were drafts (#156)"},
    {"id": "board-vs-issue",
     "incident": "#121: a card Done with its issue open, or an issue closed with its card not Done"},
    {"id": "hand-off-not-assigned",
     "incident": "2026-11-21 4:36 PM: #132 and PR #134 waited on the human with no assignee (#121)"},
    {"id": "stale-assignment",
     "incident": "#111: a card or PR assigned to the human with nothing waiting on them"},
    {"id": "resolved-decision-unconfirmed",
     "incident": "2026-11-20: Decision 6 on PR #119 was resolved and nothing acted on it until the human asked (#121)"},
    {"id": "reply-in-unanswered-decision",
     "incident": "2026-11-20: the orchestrator's summary went inside Decision 7's thread on PR #120 (#97, #121)"},
    {"id": "merge-with-open-request",
     "incident": "2026-11-20: PR #120 was called ready while the advisor's playbook comment was unapplied (#121)"},
    {"id": "decision-without-advisor-comment",
     "incident": "2026-11-21: an advisor session skipped the plain-words comment on a Decision (#97, #121)"},
]
INCIDENT = {c["id"]: c["incident"] for c in CASES}

BRANCH = re.compile(r"(?:^|/)issue-(\d+)(?:-|$)")
CONFIRM = re.compile(r"(?i)\bresolved\s*=\s*\(?[a-z]\)?")
ADVISOR = re.compile(r"[—-]\s*Claude \(advisor\)")
THREAD_LINK = re.compile(r"#discussion_r\d+")
SHA = re.compile(r"\b(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b|/commit/[0-9a-f]{7,40}")
WHY_NOT = re.compile(r"(?i)\b(?:not fixed|won'?t fix|wontfix|why not|not doing|declined|no change|left as is|"
                     r"not changed|skipped)\b")
ITEM = re.compile(r"\s*(\d+)[.)]\s+\S")


# ---------------------------------------------------------------- reading comments

def own_text(body):
    """The comment without its quoted lines."""
    return "\n".join(l for l in (body or "").splitlines() if not l.lstrip().startswith(">"))


def is_advisor(c):
    return bool(ADVISOR.search(own_text(c.get("body"))))


def is_claude(c, claude_login):
    return authors.is_claude(c.get("body"), c.get("author"), claude_login)


def is_decision(t, claude_login):
    """A review thread is a Decision when its first comment is Claude's and offers lettered options or
    a "Resolve with no reply" answer."""
    first = (t.get("comments") or [None])[0]
    if not first or not is_claude(first, claude_login):
        return False
    body = first.get("body") or ""
    return bool(decisions.options(body) or decisions.resolve_letter(body))


def thread_url(t):
    first = (t.get("comments") or [{}])[0]
    return first.get("url") or "a Decision thread"


def fix_items(body):
    """{item number: its text} for the Fix section of a reviewer's post, or {} if it has none. The section
    starts at a line that begins with "Fix" (after heading or bold marks) and ends at the next such
    heading line."""
    found, current, inside = {}, None, False
    for line in own_text(body).splitlines():
        bare = line.strip().lstrip("#").strip().strip("*_").strip()
        heading = line.strip().startswith("#") or (line.strip().startswith("**") and line.strip().endswith("**"))
        if re.match(r"(?i)fix\b", bare) and not ITEM.match(line):
            inside, current = True, None
            continue
        if inside and (heading or re.match(r"(?i)(?:notes?|decisions?)\b\W*$", bare)):
            inside, current = False, None
            continue
        if not inside:
            continue
        m = ITEM.match(line)
        if m:
            current = int(m.group(1))
            found[current] = line
        elif current is not None and line.strip():
            found[current] += "\n" + line
    return found


# ---------------------------------------------------------------- the cases

def _problem(case, problem, card=None):
    out = {"case": case, "incident": INCIDENT[case], "problem": problem}
    if card is not None:
        out["card"] = card
    return out


def _named_for(n, name):
    m = BRANCH.search(name or "")
    return bool(m) and int(m.group(1)) == n


def board_cases(s):
    out = []
    prs = s.get("prs") or []
    for c in s.get("board") or []:
        n, status, state = c["number"], c.get("status") or "", c.get("state")
        if state == "CLOSED":
            if status != "done":
                out.append(_problem("board-vs-issue", "card #%d is closed but its card is in %s, not Done. Find out "
                                    "why the board's \"Item closed\" automation didn't move it, then move it to Done."
                                    % (n, status.title() or "no column"), n))
            continue
        if status == "done":
            out.append(_problem("board-vs-issue", "card #%d is in Done but its issue is open. Close the issue or "
                                "move the card back." % n, n))
            continue
        ready = [p for p in prs if n in p.get("closes", []) and not p.get("draft")]
        if ready and status != "in review":
            out.append(_problem("pr-ready-not-in-review", "PR #%d is ready for review and closes card #%d, but the "
                                "card is in %s. Move it: python3 <apb plugin folder>/scripts/board-move.py pr-ready %d"
                                % (ready[0]["number"], n, status.title() or "no column", ready[0]["number"]), n))
            continue
        drafts = [p for p in prs if n in p.get("closes", []) and p.get("draft")]
        if not ready and drafts and status == "in review":
            out.append(_problem("in-review-with-draft-pr", "card #%d is in In review, but its PR #%d is still a "
                                "draft. A card goes to In review only when its PR is marked ready (gh pr ready); "
                                "until then it stays In progress. Move it back: python3 <apb plugin folder>/"
                                "scripts/board-move.py pr-draft %d" % (n, drafts[0]["number"], drafts[0]["number"]), n))
            continue
        if status == "todo":
            builds = ["worktree on %s" % w for w in s.get("worktrees") or [] if _named_for(n, w)]
            builds += ["PR #%d" % p["number"] for p in prs
                       if n in p.get("closes", []) or _named_for(n, p.get("head"))]
            if builds:
                out.append(_problem("build-in-todo", "card #%d is in Todo, but its build is running (%s). Move it: "
                                    "python3 <apb plugin folder>/scripts/board-move.py start-build %d"
                                    % (n, ", ".join(builds), n), n))
    return out


def card_item(c, s):
    """The card in the shape human-turn.py's verdict() reads: its last comment, and the PRs that close it
    (from GitHub's answer, or else the snapshot's open PRs)."""
    prs = c.get("prs")
    if prs is None:
        prs = [{"kind": "pr", "number": p["number"], "state": "OPEN", "draft": bool(p.get("draft"))}
               for p in s.get("prs") or [] if c["number"] in p.get("closes", [])]
    return {"kind": "issue", "number": c["number"], "state": c.get("state"), "last_comment": c.get("last_comment"),
            "prs": prs}


def assignment_cases(s, hand_off):
    human, repo = s.get("human"), s.get("repo")
    out = []
    if not human:
        return out
    prs = {p["number"]: p for p in s.get("prs") or []}
    cards = {c["number"]: c for c in s.get("board") or []}
    want_prs, want_cards = [], []
    for n in list(hand_off.get("prs") or []) + list(hand_off.get("issues") or []):
        if n in prs:
            if n not in want_prs:
                want_prs.append(n)
            want_cards += [c for c in prs[n].get("closes", []) if c not in want_cards]
        elif n in cards and n not in want_cards:
            want_cards.append(n)
    login = s.get("claude_login")
    # Only what human-turn.py would call waiting on the human needs the assignment, so this case and
    # stale-assignment never ask for opposite things (review fix 1 on #157): a card handed over with no
    # PR ready for review and no question from Claude on it ("choose the next card") stays unassigned.
    for n in want_prs:
        item = {"kind": "pr", "number": n, "state": "OPEN", "draft": bool(prs[n].get("draft"))}
        if human_turn.verdict(item, login)[0] == "waiting" and human not in prs[n].get("assignees", []):
            out.append(_problem("hand-off-not-assigned", "this turn hands PR #%d to %s, but it isn't assigned to them. "
                                "Assign it: gh pr edit %d --repo %s --add-assignee %s" % (n, human, n, repo, human)))
    for n in want_cards:
        c = cards.get(n)
        if (c and c.get("state") == "OPEN" and human_turn.verdict(card_item(c, s), login)[0] == "waiting"
                and human not in c.get("assignees", [])):
            out.append(_problem("hand-off-not-assigned", "this turn hands card #%d to %s, but it isn't assigned to "
                                "them. Assign it: gh issue edit %d --repo %s --add-assignee %s"
                                % (n, human, n, repo, human), n))
    for item in s.get("assigned") or []:
        kind, why = human_turn.verdict(item, s.get("claude_login"))
        if kind == "stale":
            name = ("card #%d" if item["kind"] == "issue" else "PR #%d") % item["number"]
            out.append(_problem("stale-assignment", "%s is assigned to %s, but %s. Clear it: gh %s edit %d --repo %s "
                                "--remove-assignee %s" % (name, human, why, "issue" if item["kind"] == "issue" else "pr",
                                                          item["number"], repo, human)))
    return out


def decision_cases(s):
    login = s.get("claude_login")
    out = []
    for p in s.get("prs") or []:
        for t in p.get("threads") or []:
            if not is_decision(t, login):
                continue
            replies = t["comments"][1:]
            human = [r for r in replies if not is_claude(r, login)]
            if t.get("isResolved"):
                if not any(is_claude(r, login) and CONFIRM.search(own_text(r.get("body"))) for r in replies):
                    out.append(_problem("resolved-decision-unconfirmed", "a Decision on PR #%d is resolved, and no reply "
                                        "in it says \"Resolved = <letter>: <what happens now>\": %s. Reply there, "
                                        "then do it." % (p["number"], thread_url(t))))
            elif replies and not human:
                out.append(_problem("reply-in-unanswered-decision", "someone other than the human replied in a "
                                    "Decision thread on PR #%d before the human answered: %s. Delete that reply "
                                    "(its text can go in a PR-level comment that links the thread)."
                                    % (p["number"], thread_url(t))))
    return out


def _later(a, b):
    return (a.get("createdAt") or "") > (b.get("createdAt") or "")


def merge_cases(s, hand_off):
    login = s.get("claude_login")
    prs = {p["number"]: p for p in s.get("prs") or []}
    out = []
    for n in hand_off.get("merge") or []:
        p = prs.get(n)
        if not p:
            continue
        posts = list(p.get("comments") or []) + list(p.get("reviews") or [])
        for c in posts:
            if is_advisor(c) and not THREAD_LINK.search(c.get("body") or ""):
                answered = any(_later(r, c) and is_claude(r, login) and not is_advisor(r) for r in posts)
                if not answered:
                    out.append(_problem("merge-with-open-request", "this turn asks for the merge of PR #%d, but the "
                                        "advisor's comment %s has no reply saying it's done or why not. Act on it "
                                        "and reply first." % (n, c.get("url") or "on it")))
            if is_claude(c, login) and not is_advisor(c):
                for k, text in sorted(fix_items(c.get("body")).items()):
                    if not SHA.search(text) and not WHY_NOT.search(text):
                        out.append(_problem("merge-with-open-request", "this turn asks for the merge of PR #%d, but "
                                            "Fix item %d in %s has no commit and no reason it wasn't fixed. Add the "
                                            "commit to the Fix post, or say there why not." % (n, k, c.get("url") or "the Fix post")))
    return out


def advisor_cases(s, hand_off):
    if not s.get("advisor"):
        return []
    login = s.get("claude_login")
    prs = {p["number"]: p for p in s.get("prs") or []}
    out = []
    for n in hand_off.get("decisions") or []:
        p = prs.get(n)
        if not p:
            continue
        open_ = [t["comments"][0] for t in p.get("threads") or [] if is_decision(t, login) and not t.get("isResolved")]
        if not open_:
            continue
        newest = max(open_, key=lambda c: c.get("createdAt") or "")
        comments = p.get("comments") or []
        if not any(is_advisor(c) and not _later(newest, c) for c in comments):
            out.append(_problem("decision-without-advisor-comment", "this turn hands the human a Decision on PR #%d, "
                                "and the advisor hasn't posted its PR-level plain-words comment (linking each Decision "
                                "thread, or saying it's already plain). Ask the advisor on the coordination issue, "
                                "then hand off." % n))
    return out


def check(s, hand_off=None):
    """Every problem in the snapshot s (see from_answers), each {"case", "incident", "problem"} and, for a
    board case, "card". hand_off: what this turn hands the human, {"prs", "merge", "issues",
    "decisions"}: PR and card numbers it names, PRs it asks to merge, PRs whose Decision threads it links."""
    hand_off = hand_off or {}
    return (board_cases(s) + assignment_cases(s, hand_off) + decision_cases(s) + merge_cases(s, hand_off)
            + advisor_cases(s, hand_off))


# ---------------------------------------------------------------- reading GitHub

COMMENT = "body author { login } createdAt url"
# A card: what the board cases read, and what human-turn.py's verdict reads (its last comment, its PRs).
CARD = """number state assignees(first: 10) { nodes { login } } comments(last: 1) { nodes { body author { login } } }
          closedByPullRequestsReferences(first: 5, includeClosedPrs: true) { nodes { number state isDraft } }"""
QUERY = """query($owner: String!, $name: String!, $q: String!) {
  repository(owner: $owner, name: $name) {
    owner { __typename login }
    projectsV2(first: 1) { nodes { items(first: 100, orderBy: {field: POSITION, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes { fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
        content { ... on Issue { %(card)s } } } } } }
    pullRequests(states: OPEN, first: 20, orderBy: {field: UPDATED_AT, direction: DESC}) { nodes {
      number isDraft headRefName assignees(first: 10) { nodes { login } }
      closingIssuesReferences(first: 10) { nodes { number } }
      reviewThreads(first: 20) { nodes { isResolved comments(first: 10) { nodes { %(c)s } } } }
      comments(last: 30) { nodes { %(c)s } }
      reviews(last: 10) { nodes { %(c)s } } } } }
  assigned: search(query: $q, type: ISSUE, first: 50) { nodes {
    __typename
    ... on PullRequest { number state isDraft }
    ... on Issue { number state comments(last: 1) { nodes { body author { login } } }
      closedByPullRequestsReferences(first: 20, includeClosedPrs: true) { nodes { number state isDraft } } } } } }""" % {
    "c": COMMENT, "card": CARD}
PAGE = """query($owner: String!, $name: String!, $after: String!) {
  repository(owner: $owner, name: $name) {
    projectsV2(first: 1) { nodes { items(first: 100, after: $after, orderBy: {field: POSITION, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes { fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
        content { ... on Issue { %s } } } } } } } }""" % CARD
COORDINATION = """query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {
  issue(number: $number) { comments(last: 30) { nodes { body } } } } }"""


def _nodes(x):
    return ((x or {}).get("nodes")) or []


def _comments(x):
    return [{"body": c.get("body"), "author": (c.get("author") or {}).get("login"), "createdAt": c.get("createdAt"),
             "url": c.get("url")} for c in _nodes(x) if c]


def _items(page):
    projects = _nodes(((page.get("data") or {}).get("repository") or {}).get("projectsV2"))
    return projects[0]["items"] if projects else None


def from_answers(answers, repo, claude_login, worktrees, coordination=None):
    """The snapshot check() reads, from GitHub's answers: the answer to QUERY first, then one answer to
    PAGE per further board page. coordination: the bodies of the coordination issue's last comments."""
    first = answers[0]["data"]
    owner = first["repository"].get("owner") or {}
    board = []
    for answer in answers:
        items = _items(answer)
        for item in (items or {}).get("nodes") or []:
            content = (item or {}).get("content") or {}
            if "number" not in content:
                continue
            c = {"number": content["number"], "state": content.get("state"),
                 "status": (((item.get("fieldValueByName") or {}).get("name")) or "").lower(),
                 "assignees": [a["login"] for a in _nodes(content.get("assignees")) if a]}
            if "closedByPullRequestsReferences" in content:
                # human-turn.py's own reading of a card, so both scripts judge it the same way.
                read = human_turn.items({"data": {"search": {"nodes": [dict(content, __typename="Issue")]}}})[0]
                c["last_comment"], c["prs"] = read["last_comment"], read["prs"]
            board.append(c)
    prs = []
    for p in _nodes(first["repository"].get("pullRequests")):
        prs.append({"number": p["number"], "draft": bool(p.get("isDraft")), "head": p.get("headRefName") or "",
                    "assignees": [a["login"] for a in _nodes(p.get("assignees")) if a],
                    "closes": [c["number"] for c in _nodes(p.get("closingIssuesReferences")) if c],
                    "threads": [{"isResolved": bool(t.get("isResolved")), "comments": _comments(t.get("comments"))}
                                for t in _nodes(p.get("reviewThreads")) if t and _nodes(t.get("comments"))],
                    "comments": _comments(p.get("comments")), "reviews": _comments(p.get("reviews"))})
    everything = [c for p in prs for c in p["comments"] + p["reviews"] + [x for t in p["threads"] for x in t["comments"]]]
    advisor = any(is_advisor(c) for c in everything) or any(is_advisor({"body": b}) for b in coordination or [])
    return {"repo": repo, "human": owner.get("login") if owner.get("__typename") == "User" else None,
            "claude_login": claude_login, "board": board, "prs": prs, "worktrees": list(worktrees),
            "assigned": human_turn.items({"data": {"search": first.get("assigned") or {"nodes": []}}}),
            "advisor": advisor}


class OutOfTime(Exception):
    """The time for the check's gh calls is used up."""


def worktree_branches(project, time_limit=5):
    """The branch of each git worktree of the project's repo ([] if git can't tell)."""
    try:
        out = subprocess.run(["git", "-C", project, "worktree", "list", "--porcelain"], stdin=subprocess.DEVNULL,
                             capture_output=True, text=True, timeout=time_limit)
    except (OSError, subprocess.SubprocessError):
        return []
    if out.returncode != 0:
        return []
    return [l.split(" ", 1)[1].replace("refs/heads/", "", 1) for l in out.stdout.splitlines() if l.startswith("branch ")]


def coordination_issue(project):
    """(repo or None, number) of the coordination issue named in the project's CLAUDE.md, or (None, None)."""
    try:
        with open(os.path.join(project, "CLAUDE.md"), encoding="utf-8") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError):
        return None, None
    m = re.search(r"(?m)^\s*coordination:\s*(?:([\w.-]+/[\w.-]+))?#(\d+)", text)
    return (m.group(1), int(m.group(2))) if m else (None, None)


def fetch(repo, project, claude_login=None, human=None, gh="gh", budget=TIME_LIMIT * 3, time_limit=TIME_LIMIT):
    """The snapshot of the repo, from gh, within budget seconds in all. Raises OSError, ValueError,
    subprocess.SubprocessError or OutOfTime when it can't get one."""
    end = time.time() + budget

    def ask(query, **fields):
        left = end - time.time()
        if left <= 0:
            raise OutOfTime("no time left for gh")
        args = [gh, "api", "graphql", "-f", "query=" + query]
        for k, v in fields.items():
            args += ["-F" if isinstance(v, int) else "-f", "%s=%s" % (k, v)]
        out = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True,
                             timeout=min(time_limit, left))
        answer = json.loads(out.stdout)
        if not isinstance(answer, dict) or answer.get("errors") or not answer.get("data"):
            raise ValueError("GitHub answered with errors: %s" % str(answer.get("errors") if isinstance(answer, dict) else answer)[:200])
        return answer

    owner, name = repo.split("/", 1)
    answers = [ask(QUERY, owner=owner, name=name, q="repo:%s assignee:%s" % (repo, human or owner))]
    for _ in range(PAGES - 1):
        items = _items(answers[-1])
        if not items or not items["pageInfo"].get("hasNextPage"):
            break
        answers.append(ask(PAGE, owner=owner, name=name, after=items["pageInfo"]["endCursor"]))
    coordination = None
    coord_repo, number = coordination_issue(project)
    if number:
        try:
            c_owner, c_name = (coord_repo or repo).split("/", 1)
            got = ask(COORDINATION, owner=c_owner, name=c_name, number=number)
            coordination = [c.get("body") for c in _nodes(got["data"]["repository"]["issue"]["comments"])]
        except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, OutOfTime):
            coordination = None  # the advisor check alone goes without it
    # The worktrees come out of the same budget, so the check's whole run fits in it (review fix 2 on #157).
    left = end - time.time()
    if left <= 0:
        raise OutOfTime("no time left for git")
    s = from_answers(answers, repo, claude_login, worktree_branches(project, min(time_limit, left)), coordination)
    if human:
        s["human"] = human
    return s


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=TIME_LIMIT).stdout


def main(argv):
    parser = argparse.ArgumentParser(description="Compare the board with GitHub and name what disagrees.")
    parser.add_argument("--repo", help="owner/name; by default, this folder's repo")
    parser.add_argument("--human", help="the human's GitHub login; by default, the repo's owner")
    parser.add_argument("--claude-login", help="Claude's own GitHub login; by default, from the project's settings")
    parser.add_argument("--project", default=".", help="the project's folder (its worktrees, its CLAUDE.md)")
    args = parser.parse_args(argv)
    try:
        repo = args.repo or json.loads(run(["gh", "repo", "view", "--json", "nameWithOwner"]))["nameWithOwner"]
        s = fetch(repo, args.project, authors.claude_login(args.claude_login), args.human)
    except subprocess.CalledProcessError as e:
        print("The check couldn't run: gh failed: " + (e.stderr or e.stdout or "").strip(), file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError, OutOfTime) as e:
        print("The check couldn't run: %s" % (e or type(e).__name__), file=sys.stderr)
        return 2
    found = check(s)
    if not found:
        print("All consistent: the board, the PRs and the assignments agree.")
        return 0
    for p in found:
        print("%s: %s (case added for: %s)" % (p["case"], p["problem"], p["incident"]))
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
