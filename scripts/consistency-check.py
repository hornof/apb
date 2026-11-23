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
- build-in-todo: a build is running for a card (a git worktree on a branch named issue-<N>, or on the
  branch Claude Code gives an agent's isolated worktree, worktree-agent-<id>, when the events file says
  that agent was launched on the card (#159); or an open PR, draft or not, that closes the card or comes
  from such a branch), but the card is in Todo.
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
- reply-in-unanswered-decision: someone other than the human or the advisor (the orchestrator, the
  reviewer) replied in a Decision thread the human hasn't answered or resolved. The advisor may reply
  there when it helps (#169).
- merge-with-open-request: the turn asks the human to merge a PR while an advisor comment on it has no
  later reply from Claude that addresses it (links it or quotes it; the reviewer's posts don't count),
  or the reviewer's Fix post has an item with no commit on the PR and no reason it wasn't fixed (#159).
- loose-follow-up: the turn asks the human to merge a PR, or says a PR is merged (the Stop hook reads
  "PR #N merged" in the message; the PR is found among the open PRs or the MERGED_PRS merged ones most
  recently updated), and a reviewer Note on it (a numbered item in the Notes section of a post of
  Claude's that isn't the advisor's), or an ask for a card on it or on the cards it closes ("file a
  card", "want a card?", "say so" next to "file" or "card"), links no new card (#N other than the PR and
  the cards it closes) and has no later reply that settles it: for a Note, a line that names it ("Note
  6: #181", "Notes 3 to 5: no action"); for an ask, a reply that links or quotes that comment and links
  a new card or says "no action" (#173).

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
    {"id": "loose-follow-up",
     "incident": "2026-11-22: PR #161 Note 2, PR #163 Note 6 and PR #164's advisor comment (\"file one card\") were "
                 "left with no card until the human asked what was missing (#173)"},
]
INCIDENT = {c["id"]: c["incident"] for c in CASES}

BRANCH = re.compile(r"(?:^|/)issue-(\d+)(?:-|$)")
# The branch Claude Code gives the worktree of an agent launched with isolation: "worktree" (#142, #159).
AGENT_BRANCH = re.compile(r"(?:^|/)worktree-agent-([0-9a-z]+)$")
CONFIRM = re.compile(r"(?i)\bresolved\s*=\s*\(?[a-z]\)?")
ADVISOR = re.compile(r"[—-]\s*Claude \(advisor\)")
REVIEWER = re.compile(r"[—-]\s*Claude \(reviewer\)")
SIGNATURE = re.compile(r"[—-]\s*Claude \([\w ]+\)\s*$")
QUOTE_MIN = 15  # characters a quoted line needs to say which comment it answers
# A word that may be a commit: 7 to 40 hex characters. It counts only if a commit on the PR starts with it.
SHA = re.compile(r"(?i)(?<![0-9a-z])[0-9a-f]{7,40}(?![0-9a-z])")
WHY_NOT = re.compile(r"(?i)\b(?:not fixed|won'?t fix|wontfix|why not|not doing|declined|no change|left as is|"
                     r"not changed|skipped)\b")
ITEM = re.compile(r"\s*(\d+)[.)]\s+\S")
# A numbered item, maybe in bold: "2. …", "**3.** …" (#173).
NOTE_ITEM = re.compile(r"\s*[*_]{0,2}(\d+)[.)][*_]{0,2}\s+\S")
NOTES_HEADING = re.compile(r"(?i)notes?\b(?:\s*\([^)]*\))?\W*$")
# A card a text links: #N, or a link to an issue. Not a PR ("PR #44"), an HTML entity (&#44;) or a word#N.
CARD_REF = re.compile(r"(?<![\w/&#])(?<!PR )(?<!PRs )(?<!pull request )#(\d+)\b|github\.com/[\w.-]+/[\w.-]+/issues/(\d+)\b")
# "no action" only: "Note 6 has no card yet" is a status, not a decision (review fix 2 on #179).
NO_ACTION = re.compile(r"(?i)\bno\s+action\b")
# A line that refers to reviewer Notes by number: "Note 6", "Notes 4 and 5", "Notes 3 to 6", "Notes 2-3".
NOTE_REF = re.compile(r"(?i)\bnotes?\s+(\d+(?:\s*(?:,\s*(?:and\s+)?|and\s+|&\s*|to\s+|[-–]\s*)\d+)*)")
# Asking for a card (#173): "file a card", "want a card?", "say so" next to "file" or "card".
REQUEST = re.compile(r"(?i)\bfile\s+(?:a|one|another|the|that|this)\s+(?:new\s+|follow-?up\s+|separate\s+)?card\b"
                     r"|\bwant\s+(?:a|one|another)\s+(?:new\s+|separate\s+)?card\b"
                     r"|\bsay\s+so\b[^.\n]{0,60}?\b(?:file|card)\b"
                     r"|\b(?:file|card)\b[^.\n]{0,60}?\bsay\s+so\b")
CODE = re.compile(r"`[^`\n]*`")


# ---------------------------------------------------------------- reading comments

def own_text(body):
    """The comment without its quoted lines."""
    return "\n".join(l for l in (body or "").splitlines() if not l.lstrip().startswith(">"))


def is_advisor(c):
    return bool(ADVISOR.search(own_text(c.get("body"))))


def is_reviewer(c):
    return bool(REVIEWER.search(own_text(c.get("body"))))


def anchor(c):
    """The comment's own anchor in its URL (issuecomment-123, pullrequestreview-45), or None."""
    m = re.search(r"#((?:issuecomment|pullrequestreview|discussion_r)-?\d+)$", c.get("url") or "")
    return m.group(1) if m else None


def flat(text):
    return " ".join((text or "").split())


def addresses(reply, c):
    """True if the reply addresses comment c: it links c (its anchor, not a longer number), or it quotes a
    line of c's own text of at least QUOTE_MIN characters that isn't only c's signature."""
    body = reply.get("body") or ""
    a = anchor(c)
    if a and re.search(re.escape(a) + r"(?!\d)", body):
        return True
    asked = flat(own_text(c.get("body")))
    for line in body.splitlines():
        if not line.lstrip().startswith(">"):
            continue
        quoted = flat(line.lstrip().lstrip(">").strip())
        if len(quoted) >= QUOTE_MIN and not SIGNATURE.fullmatch(quoted) and quoted in asked:
            return True
    return False


def on_pr(text, commits):
    """True if the text names a commit on the PR: a hex word of 7 to 40 characters that one starts with."""
    oids = [o.lower() for o in commits or [] if isinstance(o, str)]
    return any(o.startswith(w.lower()) for w in SHA.findall(text or "") for o in oids)


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


def note_items(body):
    """({item number: its text}, the post's other lines) for the Notes section of a reviewer's post. The
    section starts at a line that is only "Notes" (after heading or bold marks; a "(…)" may follow) and
    ends at the next heading line or a "Fix" or "Decisions" line. An item is a numbered line, maybe in
    bold ("**3.** …"), with the lines under it."""
    found, other, current, inside = {}, [], None, False
    for line in own_text(body).splitlines():
        stripped = line.strip()
        bare = stripped.lstrip("#").strip().strip("*_").strip()
        item = NOTE_ITEM.match(line)
        heading = not item and (stripped.startswith("#") or (stripped.startswith("**") and stripped.endswith("**")))
        if not item and NOTES_HEADING.match(bare):
            inside, current = True, None
            continue
        if inside and (heading or (not item and re.match(r"(?i)(?:fix\w*|decisions?)\b\W*$", bare))):
            inside, current = False, None
        if inside and item:
            current = int(item.group(1))
            found[current] = line
        elif inside and current is not None and stripped:
            found[current] += "\n" + line
        else:
            other.append(line)
    return found, other


def new_cards(text, own):
    """The cards the text links (#N or a link to an issue, outside code spans) that aren't in own: the PR
    itself and the cards it closes."""
    refs = {int(a or b) for a, b in CARD_REF.findall(CODE.sub(" ", text or ""))}
    return refs - set(own)


def note_numbers(line):
    """The Note numbers a line refers to: "Note 6", "Notes 4 and 5", "Notes 3 to 6", "Notes 2-3"."""
    out = set()
    for m in NOTE_REF.finditer(re.sub(r"[*_]", "", line)):
        tokens = re.findall(r"\d+|to|[-–]", m.group(1))
        k = 0
        while k < len(tokens):
            if k + 2 < len(tokens) and tokens[k + 1] in ("to", "-", "–"):
                lo, hi = int(tokens[k]), int(tokens[k + 2])
                out |= set(range(lo, hi + 1)) if 0 <= hi - lo <= 50 else {lo, hi}
                k += 3
            else:
                if tokens[k].isdigit():
                    out.add(int(tokens[k]))
                k += 1
    return out


def settles(line, own):
    """True if the line settles a follow-up: it links a new card, or it says "no action"."""
    return bool(new_cards(line, own)) or bool(NO_ACTION.search(line))


def requests(lines, own):
    """The sentences in the lines that ask for a card, outside code spans (the lines are a comment's own
    text, so quotes are already gone); a line that settles itself (it links a new card, or says "no
    action") asks nothing."""
    found = []
    for line in lines:
        text = re.sub(r"[*_]", "", CODE.sub(" ", line))
        if not REQUEST.search(text) or settles(text, own):
            continue
        for sentence in re.split(r"(?<=[.!?])\s+", text):
            if REQUEST.search(sentence):
                found.append(flat(sentence).lstrip("-+ ").strip())
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


def _agent_of(branch):
    m = AGENT_BRANCH.search(branch or "")
    return m.group(1) if m else None


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
            agents = s.get("agents") or {}
            builds = ["worktree on %s" % w for w in s.get("worktrees") or []
                      if _named_for(n, w) or n in agents.get(_agent_of(w), [])]
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
            # Only the human's own replies, by login when it is known, as decision-check.py counts them (#169).
            human = [r for r in replies if decisions.is_humans(r, login, s.get("human"))]
            if t.get("isResolved"):
                if not any(is_claude(r, login) and CONFIRM.search(own_text(r.get("body"))) for r in replies):
                    out.append(_problem("resolved-decision-unconfirmed", "a Decision on PR #%d is resolved, and no reply "
                                        "in it says \"Resolved = <letter>: <what happens now>\": %s. Reply there, "
                                        "then do it." % (p["number"], thread_url(t))))
            elif not human and any(not is_advisor(r) for r in replies):
                # The advisor may reply before the human, when it helps (#169); nobody else may.
                out.append(_problem("reply-in-unanswered-decision", "someone other than the human replied in a "
                                    "Decision thread on PR #%d before the human answered: %s. Delete that reply, "
                                    "so the Resolve button stays under the Decision; say it in chat or on the "
                                    "card instead." % (p["number"], thread_url(t))))
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
            if is_advisor(c):
                # Only a reply that addresses it, from Claude but not the advisor or the reviewer (#159).
                answered = any(_later(r, c) and is_claude(r, login) and not is_advisor(r) and not is_reviewer(r)
                               and addresses(r, c) for r in posts)
                if not answered:
                    out.append(_problem("merge-with-open-request", "this turn asks for the merge of PR #%d, but the "
                                        "advisor's comment %s has no reply that links it or quotes it and says it's "
                                        "done or why not. Act on it and reply first." % (n, c.get("url") or "on it")))
            if is_claude(c, login) and not is_advisor(c):
                for k, text in sorted(fix_items(c.get("body")).items()):
                    if not on_pr(text, p.get("commits")) and not WHY_NOT.search(text):
                        out.append(_problem("merge-with-open-request", "this turn asks for the merge of PR #%d, but "
                                            "Fix item %d in %s has no commit on the PR and no reason it wasn't fixed. "
                                            "Add the commit to the Fix post, or say there why not."
                                            % (n, k, c.get("url") or "the Fix post")))
    return out


def follow_up_cases(s, hand_off):
    """loose-follow-up (#173): for each PR the turn asks to merge or says is merged, every reviewer Note on
    it, and every ask for a card on it or on the cards it closes, links a new card or has a later reply
    that settles it. A reply settles a Note on a line that names it ("Note 6: #181", "Notes 3 to 5: no
    action"), and an ask for a card when it links or quotes that comment (as `addresses` reads it) and
    links a new card or says "no action"."""
    login = s.get("claude_login")
    # A PR to merge is an open one; a PR said to be merged counts only if it is (review fix 1 on #179:
    # "PR #179 is ready to be merged" names an open PR).
    where = {"merge": {p["number"]: p for p in s.get("prs") or []},
             "merged": {p["number"]: p for p in s.get("merged") or []}}
    out = []
    wanted = [(n, key) for key in ("merge", "merged") for n in hand_off.get(key) or [] if n in where[key]]
    done = set()
    for n, key in wanted:
        if n in done:  # a PR both asked to merge and said to be merged is checked once
            continue
        done.add(n)
        p = where[key][n]
        what = ("this turn hands over the merge of PR #%d" if key == "merge" else "this turn notices PR #%d merged") % n
        own = {n} | set(p.get("closes") or [])
        posts = list(p.get("comments") or []) + list(p.get("reviews") or [])
        everything = posts + list(p.get("card_comments") or [])
        for i, c in enumerate(everything):
            notes, rest = ({}, own_text(c.get("body")).splitlines())
            if i < len(posts) and is_claude(c, login) and not is_advisor(c):
                notes, rest = note_items(c.get("body"))
            for k, text in sorted(notes.items()):
                if new_cards(text, own) or any(
                        _later(r, c) and any(k in note_numbers(line) and settles(line, own)
                                             for line in own_text(r.get("body")).splitlines())
                        for r in everything):
                    continue
                out.append(_problem("loose-follow-up", "%s, but Note %d in %s has no card and no \"no action\" "
                                    "reply. File a card and reply on the PR \"Note %d: #<card>\", or reply "
                                    "\"Note %d: no action, <why>\"." % (what, k, c.get("url") or "a review post",
                                                                       k, k)))
            asked = requests(rest, own)
            if asked and not any(_later(r, c) and addresses(r, c) and settles(own_text(r.get("body")), own)
                                 for r in everything):
                out.append(_problem("loose-follow-up", "%s, but %s asks for a card (\"%s\") and has no card linked "
                                    "and no \"no action\" reply. File the card (#<card>) and reply linking that "
                                    "comment and the card, or reply linking it with \"no action, <why>\"."
                                    % (what, c.get("url") or "a comment", asked[0][:160])))
    return out


def check(s, hand_off=None):
    """Every problem in the snapshot s (see from_answers), each {"case", "incident", "problem"} and, for a
    board case, "card". hand_off: what this turn hands the human, {"prs", "merge", "issues", "merged"}:
    PR and card numbers it names, PRs it asks to merge, and PRs it says are merged."""
    hand_off = hand_off or {}
    return (board_cases(s) + assignment_cases(s, hand_off) + decision_cases(s) + merge_cases(s, hand_off)
            + follow_up_cases(s, hand_off))


# ---------------------------------------------------------------- reading GitHub

COMMENT = "body author { login } createdAt url"
# A card: what the board cases read, and what human-turn.py's verdict reads (its last comment, its PRs).
CARD = """number state assignees(first: 10) { nodes { login } } comments(last: 1) { nodes { body author { login } } }
          closedByPullRequestsReferences(first: 5, includeClosedPrs: true) { nodes { number state isDraft } }"""
# A PR: what every case reads, and the comments on the cards it closes (#173).
PR = """number isDraft headRefName assignees(first: 10) { nodes { login } }
      commits(last: 100) { nodes { commit { oid } } }
      closingIssuesReferences(first: 10) { nodes { number comments(last: 30) { nodes { %(c)s } } } }
      reviewThreads(first: 20) { nodes { isResolved comments(first: 10) { nodes { %(c)s } } } }
      comments(last: 30) { nodes { %(c)s } }
      reviews(last: 10) { nodes { %(c)s } }""" % {"c": COMMENT}
MERGED_PRS = 5  # the most recently updated merged PRs, where a merge the turn notices is found (#173)
QUERY = """query($owner: String!, $name: String!, $q: String!) {
  repository(owner: $owner, name: $name) {
    owner { __typename login }
    projectsV2(first: 1) { nodes { items(first: 100, orderBy: {field: POSITION, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes { fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
        content { ... on Issue { %(card)s } } } } } }
    pullRequests(states: OPEN, first: 20, orderBy: {field: UPDATED_AT, direction: DESC}) { nodes { %(pr)s } }
    merged: pullRequests(states: MERGED, first: %(merged)d, orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes { %(pr)s } } }
  assigned: search(query: $q, type: ISSUE, first: 50) { nodes {
    __typename
    ... on PullRequest { number state isDraft }
    ... on Issue { number state comments(last: 1) { nodes { body author { login } } }
      closedByPullRequestsReferences(first: 20, includeClosedPrs: true) { nodes { number state isDraft } } } } } }""" % {
    "c": COMMENT, "card": CARD, "pr": PR, "merged": MERGED_PRS}
PAGE = """query($owner: String!, $name: String!, $after: String!) {
  repository(owner: $owner, name: $name) {
    projectsV2(first: 1) { nodes { items(first: 100, after: $after, orderBy: {field: POSITION, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes { fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
        content { ... on Issue { %s } } } } } } } }""" % CARD


def _nodes(x):
    return ((x or {}).get("nodes")) or []


def _comments(x):
    return [{"body": c.get("body"), "author": (c.get("author") or {}).get("login"), "createdAt": c.get("createdAt"),
             "url": c.get("url")} for c in _nodes(x) if c]


def _items(page):
    projects = _nodes(((page.get("data") or {}).get("repository") or {}).get("projectsV2"))
    return projects[0]["items"] if projects else None


def from_answers(answers, repo, claude_login, worktrees, agents=None):
    """The snapshot check() reads, from GitHub's answers: the answer to QUERY first, then one answer to
    PAGE per further board page. agents: {agent id: the cards it was launched on} (agent_cards)."""
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
    return {"repo": repo, "human": owner.get("login") if owner.get("__typename") == "User" else None,
            "claude_login": claude_login, "board": board,
            "prs": [_pr(p) for p in _nodes(first["repository"].get("pullRequests")) if p],
            "merged": [_pr(p) for p in _nodes(first["repository"].get("merged")) if p],
            "worktrees": list(worktrees),
            "assigned": human_turn.items({"data": {"search": first.get("assigned") or {"nodes": []}}}),
            "agents": dict(agents or {})}


def _pr(p):
    """A PR from GitHub's answer, in the shape the cases read."""
    closing = [c for c in _nodes(p.get("closingIssuesReferences")) if c]
    return {"number": p["number"], "draft": bool(p.get("isDraft")), "head": p.get("headRefName") or "",
            "assignees": [a["login"] for a in _nodes(p.get("assignees")) if a],
            "closes": [c["number"] for c in closing],
            "card_comments": [x for c in closing for x in _comments(c.get("comments"))],
            "threads": [{"isResolved": bool(t.get("isResolved")), "comments": _comments(t.get("comments"))}
                        for t in _nodes(p.get("reviewThreads")) if t and _nodes(t.get("comments"))],
            "comments": _comments(p.get("comments")), "reviews": _comments(p.get("reviews")),
            "commits": [((c or {}).get("commit") or {}).get("oid") for c in _nodes(p.get("commits"))
                        if ((c or {}).get("commit") or {}).get("oid")]}


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


def agent_cards(project):
    """{agent id: the cards it was launched on}, from the AgentLaunch lines hooks/apb-events.py writes to
    the project's events file ({} without one)."""
    found = {}
    try:
        with open(os.path.join(project, ".apb", "events.jsonl"), "rb") as f:
            raw = f.read().decode("utf-8", "replace")
    except OSError:
        return found
    for line in raw.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and e.get("event") == "AgentLaunch" and isinstance(e.get("launched"), str):
            cards = e.get("cards") if isinstance(e.get("cards"), list) else []
            found[e["launched"]] = [c for c in cards if isinstance(c, int) and not isinstance(c, bool)]
    return found


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
    # The worktrees come out of the same budget, so the check's whole run fits in it (review fix 2 on #157).
    left = end - time.time()
    if left <= 0:
        raise OutOfTime("no time left for git")
    s = from_answers(answers, repo, claude_login, worktree_branches(project, min(time_limit, left)),
                     agent_cards(project))
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
