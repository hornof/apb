"""Checks a PR's Decision threads: their shape before the hand-off, and their answers before a builder
is sent to implement one.

Usage, from the project's repo root:
    python3 <apb plugin folder>/scripts/decision-check.py <PR number> [--repo owner/name]

Only Decisions get threads, so every review thread on the PR is taken to be a Decision. It prints one
line per thread:
- its shape: every Decision has the question in one sentence, lettered options (a, b, ...),
  "Recommended: <letter>" and "Resolve with no reply = <letter>", naming the same option;
- its answer: resolved, answered with an option letter in the human's last reply, a design discussion
  (three or more replies from the human and no letter), or waiting on the human.
A reply from Claude's own GitHub account (apb-setup step 5) is Claude's, not the human's, signed or not.
A reply signed "— Claude (<role>)" is Claude's too, since Claude may use the human's login (apb-setup step 5),
as in older comments and projects without the account. comment-author.py holds the rule, and says where
Claude's login comes from; --claude-login gives it directly.
It exits with 1 if any thread's shape is wrong, so nothing is handed over until the review is fixed.
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
DISCUSSION = 3  # this many replies from the human with no option letter make a design discussion

MISSING = {
    "question": "the question in one sentence",
    "options": "lettered options (a, b, ...)",
    "recommended": "\"Recommended: <letter>\" naming one of the options",
    "resolve": "\"Resolve with no reply = <letter>\" naming one of the options",
}
MISMATCH = "\"Resolve with no reply\" names a different option than \"Recommended\""


def plain(text):
    """The text without bold and italic marks, which reviewers put around labels."""
    return re.sub(r"[*_]", "", text or "")


# ---------------------------------------------------------------- the shape

OPTION = re.compile(r"^\s*(?:[-+]\s+)?\(?([A-Za-z])[).:]\s+\S")
LABEL = re.compile(r"^\s*(?:\d+[.)]\s*)?(?:Decision\s*\d*\s*:\s*)?", re.I)
SENTENCE_END = re.compile(r"[.!?](?=\s)")
ABBREVIATIONS = re.compile(r"\b(?:e\.g|i\.e|etc|vs)\.", re.I)


def options(body):
    """The option letters, lowercase and in order, if they run a, b, ... with at least two; else []."""
    found = [m.group(1).lower() for m in map(OPTION.match, plain(body).splitlines()) if m]
    return found if len(found) >= 2 and "".join(found) == "abcdefghijklmnopqrstuvwxyz"[:len(found)] else []


def has_question(body):
    """True if a line ends with "?" and holds one sentence, after its item number and "Decision:"."""
    for line in plain(body).splitlines():
        line = line.strip()
        if line.endswith("?"):
            rest = ABBREVIATIONS.sub("", LABEL.sub("", line, count=1))[:-1]
            if not SENTENCE_END.search(rest):
                return True
    return False


def letter_after(label, body):
    m = re.search(label + r"\s*\(?([A-Za-z])\)?(?=[\s.,;)]|$)", plain(body), re.I)
    return m.group(1).lower() if m else None


def recommended(body):
    return letter_after(r"Recommended\s*:", body)


def resolve_letter(body):
    return letter_after(r"Resolve with no reply\s*=", body)


def shape_problems(body):
    """What the Decision is missing, in MISSING's order, or [] when its shape is right."""
    letters = options(body)
    rec, res = recommended(body), resolve_letter(body)
    problems = []
    if not has_question(body):
        problems.append(MISSING["question"])
    if not letters:
        problems.append(MISSING["options"])
    if not rec or (letters and rec not in letters):
        problems.append(MISSING["recommended"])
    if not res or (letters and res not in letters):
        problems.append(MISSING["resolve"])
    if not problems and rec != res:
        problems.append(MISMATCH)
    return problems


def number(body):
    """The item number the reviewer gave the Decision, or None."""
    text = plain(body).strip()
    m = re.match(r"\W*(\d+)[.)]", text) or re.search(r"\bDecision\s+(\d+)\b", text, re.I)
    return m.group(1) if m else None


# ---------------------------------------------------------------- the answer

def body_of(comment):
    """A thread comment is its body, or {"body", "author"} when GitHub gave the author's login."""
    return comment.get("body") if isinstance(comment, dict) else comment


def is_claude(comment, claude_login=None):
    author = comment.get("author") if isinstance(comment, dict) else None
    return authors.is_claude(body_of(comment), author, claude_login)


def chosen(reply, letters):
    """The option letter a reply names, or None. Strict, so an "a" in a sentence is not a choice.
    Quoted lines (a GitHub "Quote reply" copies the Decision) and questions name no choice."""
    text = "\n".join(line for line in plain(reply).splitlines() if not line.lstrip().startswith(">"))
    text = re.sub(r"[^.!?\n]*\?", "", text).strip()
    patterns = [
        r"^\W*(?:ok(?:ay)?\W+)?\(?([a-z])\)?\W*$",  # the whole reply: "b", "B.", "ok, c", "(a)"
        r"\b(?:option|choice)\s+\(?([a-z])\)?\b",  # "option b"
        r"\b(?:go with|pick|choose|take)\s+(?:\(([a-z])\)|([a-z])(?=[.,;:!]|\s*$))",  # "go with a, ..."
        r"\(([a-z])\)",  # "(b) please"
        r"^([a-z])[).:,]\s",  # "b) but shorter"
    ]
    for pattern in patterns:
        for m in re.finditer(pattern, text, re.I):
            letter = next(g for g in m.groups() if g).lower()
            if letter in letters:
                return letter
    return None


def status(thread, claude_login=None):
    """("resolved", letter), ("answered", letter), ("discussion", None) or ("waiting", None).
    thread: {"isResolved", "comments": [oldest first]}; the first comment is the Decision. Each comment
    is its body, or {"body", "author"}. claude_login is Claude's own GitHub login, or None."""
    body, replies = body_of(thread["comments"][0]), thread["comments"][1:]
    letters = options(body) or list("abcdefghijklmnopqrstuvwxyz")
    human = [body_of(r) for r in replies if not is_claude(r, claude_login)]
    last = chosen(human[-1], letters) if human else None
    if thread["isResolved"]:
        named = [c for c in (chosen(r, letters) for r in human) if c]
        return "resolved", named[-1] if named else recommended(body)
    if last:
        return "answered", last
    if len(human) >= DISCUSSION:
        return "discussion", None
    return "waiting", None


def ready(state):
    """True when a builder may be sent to implement the Decision."""
    return state[0] in ("resolved", "answered") and bool(state[1])


# ---------------------------------------------------------------- the report

def describe(state):
    kind, letter = state
    if kind == "resolved" and not letter:
        return "resolved with no option letter: read the thread"
    if kind == "resolved":
        return "resolved, %s, ready for the builder" % letter
    if kind == "answered":
        return "answered %s, ready for the builder" % letter
    if kind == "discussion":
        return "design discussion, not a decision: move the question to the card or to chat"
    return "waiting on the human"


def report(threads, claude_login=None):
    """(lines, how many threads have a wrong shape)."""
    if not threads:
        return ["No Decision threads."], 0
    lines, bad = [], 0
    for i, t in enumerate(threads, 1):
        body = body_of(t["comments"][0])
        n = number(body)
        name = ("Decision %s" % n) if n else ("Decision thread %d" % i)
        problems = shape_problems(body)
        bad += bool(problems)
        shape = "; ".join(("missing " + p) if p in MISSING.values() else p for p in problems) or "shape ok"
        where = t.get("path") or "PR"
        if t.get("line"):
            where += ":%s" % t["line"]
        lines.append("%s (%s): %s; %s" % (name, where, shape, describe(status(t, claude_login))))
    return lines, bad


QUERY = """query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {
  pullRequest(number: $number) { reviewThreads(first: 100) { nodes {
    isResolved path line comments(first: 100) { nodes { body author { login } } } } } } } }"""


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=TIME_LIMIT).stdout


def threads(repo, pr):
    owner, name = repo.split("/", 1)
    got = json.loads(run(["gh", "api", "graphql", "-f", "owner=" + owner, "-f", "name=" + name,
                          "-F", "number=%d" % pr, "-f", "query=" + QUERY]))
    nodes = got["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
    return [{"isResolved": t["isResolved"], "path": t["path"], "line": t["line"],
             "comments": [{"body": c["body"], "author": (c.get("author") or {}).get("login")}
                          for c in t["comments"]["nodes"]]} for t in nodes if t["comments"]["nodes"]]


def main(argv):
    parser = argparse.ArgumentParser(description="Check a PR's Decision threads.")
    parser.add_argument("pr", type=int)
    parser.add_argument("--repo", help="owner/name; by default, this folder's repo")
    parser.add_argument("--claude-login", help="Claude's own GitHub login; by default, from the project's settings")
    args = parser.parse_args(argv)
    try:
        repo = args.repo or json.loads(run(["gh", "repo", "view", "--json", "nameWithOwner"]))["nameWithOwner"]
        lines, bad = report(threads(repo, args.pr), authors.claude_login(args.claude_login))
    except subprocess.CalledProcessError as e:
        print("gh failed: " + (e.stderr or e.stdout or "").strip(), file=sys.stderr)
        return 2
    for line in lines:
        print(line)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
