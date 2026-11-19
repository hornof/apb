"""Card times: how long a closed card spent in build, in review and with the human, posted on its issue.

Usage, from the project's repo root:
    python3 <apb plugin folder>/scripts/card-times.py <issue number> [--repo owner/name] [--post]
        [--guessed "build 15, review 5, human 10"] [--events .apb/events.jsonl]
    python3 <apb plugin folder>/scripts/card-times.py --collect [--repo owner/name]

It works the times out from .apb/events.jsonl and GitHub, never by hand:
- build: the builder's start to the PR opening. The builder is the earliest builder agent, started
  before the PR opened, that named the PR's branch in a shell command (a push), or else the issue
  (#N, issue-N, issue view N, issues/N). An agent resumed later keeps its first start.
- hand-offs: the Stop lines with holder "human" between the PR opening and the human's last action
  (below) that name the PR or the card (#N, /pull/N), or else all of them. Their number is in the
  comment. A card with a Decision usually has 2 ("answer the decision", then "merge"); more means the
  PR came back to Claude after a hand-off.
- review: the PR opening to the first hand-off.
- human: the sum of the human's waits. Each wait runs from a hand-off to the human's next action: a
  chat message in the orchestrator's session, or a comment on the PR or the card. The last wait runs
  to the human's last action on the PR: the merge, or the close, or for a PR still open, the human's
  last comment. So a second pass by the builder after a hand-off (a red test suite, a missed fix) is
  not counted as the human's time, and answering a Decision is. A wait with no action seen runs on
  to the next hand-off. When the orchestrator answers the human and stops with no holder line, a new
  wait starts there, as hooks/apb-stop.py reads it. GitHub doesn't say when a thread was resolved,
  so that isn't used.
- messages: the human's messages between the first hand-off and that last action that aren't "go", a
  decision letter, "resolved" or "merged": prompts in the orchestrator's session (hooks/apb-events.py
  marks each one routine or not, without keeping its text) and the human's comments on the PR. A
  comment is Claude's when it comes from Claude's own GitHub account (apb-setup step 5), signed or not,
  or when it is signed "— Claude (<role>)", as in older comments, the advisor's, and projects without
  the account. comment-author.py holds the rule and says where Claude's login comes from;
  --claude-login gives it directly. Prompts logged
  before apb 0.3.21 have no mark, so the count is then not known.

It prints the comment. With --post it posts it on the issue, once: only when the issue is closed, and
not if the issue already has one. The comment starts with a hidden data line, so --collect can list
every card's numbers in one table, tab-separated, for a retrospective.
"""
import argparse
import calendar
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

TIME_LIMIT = 60  # seconds for each gh call
MARK = "<!-- apb-card-times "
FIELDS = ["issue", "pr", "build_min", "review_min", "human_min", "messages", "chat", "pr_messages", "hand_offs"]

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("apb_events", os.path.join(HERE, "..", "hooks", "apb-events.py"))
events_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(events_hook)
routine = events_hook.routine  # one rule for what counts as routine, in the hook and here
_spec = importlib.util.spec_from_file_location("comment_author", os.path.join(HERE, "comment-author.py"))
authors = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(authors)


def seconds(text):
    """Seconds since the epoch for an events-file or GitHub time (UTC, with or without fractions)."""
    whole = calendar.timegm(tuple(int(x) for x in re.split(r"[-T:]", text[:19])) + (0, 0, 0))
    m = re.match(r"\.(\d+)", text[19:])
    return whole + (float("0." + m.group(1)) if m else 0.0)


def is_claude(body, author=None, claude_login=None):
    """True if the comment is Claude's: from Claude's account, or signed by Claude outside quoted lines."""
    return authors.is_claude(body, author, claude_login)


def by_claude(c, claude_login):
    return is_claude(c.get("body"), c.get("author"), claude_login)


def minutes(a, b):
    return None if a is None or b is None else round((b - a) / 60, 1)


# ---------------------------------------------------------------- the steps

def issue_named(number):
    return re.compile(r"(?:#|\bissue[- ]|\bissue view |/issues/)%d(?!\d)" % number)


def branch_named(branch):
    return re.compile(r"(?<![\w.-])(?:[\w.-]+/)?%s(?![\w.-])" % re.escape(branch))


def builder_start(events, branch, number, opened):
    """The first start of the earliest builder, started by opened, that named the branch, or else the issue."""
    first, said = {}, {}
    for e in events:
        a = e.get("agent_id")
        if not isinstance(a, str) or not str(e.get("agent_type", "")).endswith("builder"):
            continue
        first.setdefault(a, seconds(e["time"]))
        if isinstance(e.get("command"), str):
            said[a] = said.get(a, "") + "\n" + e["command"]
    for pattern in ([branch_named(branch)] if branch else []) + [issue_named(number)]:
        starts = [first[a] for a, text in said.items() if pattern.search(text) and first[a] <= opened]
        if starts:
            return min(starts)
    return None


def names(todo, pr, number):
    return bool(re.search(r"(?:#|/pull/|\bPR\s*)%d(?!\d)|#%d(?!\d)" % (pr, number), todo or ""))


def hand_offs(events, opened, end, pr, number):
    """The Stop lines handing over to the human between opened and end, in time order: the ones that
    name the PR or the card, or else all of them. Empty if there is none."""
    stops = [e for e in events if e.get("event") == "Stop" and e.get("holder") == "human"
             and opened <= seconds(e["time"]) <= end]
    return [e for e in stops if names(e.get("todo"), pr, number)] or stops


def human_time(events, hands, end, data, p, claude_login=None):
    """Seconds the human held the PR: the sum of each wait, from each hand-off to the human's next action,
    and from the last hand-off to end. An action is a chat message in the hand-off's session that Claude
    Code didn't send itself, or the human's comment on the PR or the card. A wait with no action seen
    runs on to the next hand-off. GitHub doesn't say when a thread was resolved, so that isn't used."""
    comments = [seconds(c["createdAt"]) for c in p.get("comments", []) + data.get("card_comments", [])
                if c.get("createdAt") and not by_claude(c, claude_login)]
    starts = sorted([(seconds(e["time"]), e.get("session")) for e in hands] +
                    resumes(events, seconds(hands[0]["time"]), seconds(hands[-1]["time"])))
    total = 0.0
    for i, (t, session) in enumerate(starts):
        if i == len(starts) - 1:
            total += end - t
            break
        chat = [seconds(e["time"]) for e in events if e.get("event") == "UserPromptSubmit"
                and e.get("session") == session and not e.get("automatic")]
        total += min([a for a in chat + comments if a > t] + [starts[i + 1][0]]) - t
    return total


def resumes(events, first, last):
    """(time, session) of each Stop between first and last that starts a new wait for the human without
    a new hand-off: a Stop with no holder line, at the end of a turn the human started with a message,
    while the session's last holder was the human. hooks/apb-stop.py reads such a Stop the same way: the
    orchestrator answered the human and the human holds the ball again."""
    found, holder, human_said = [], {}, {}
    for e in events:
        s = e.get("session")
        if e.get("event") == "UserPromptSubmit" and not e.get("automatic") and "routine" in e:
            human_said[s] = True
        if e.get("event") != "Stop":
            continue
        t = seconds(e["time"])
        if e.get("holder"):
            holder[s] = e["holder"]
        elif not e.get("sent_back") and holder.get(s) == "human" and human_said.get(s) and first < t < last:
            found.append((t, s))
        if not e.get("sent_back"):
            human_said[s] = False
    return found


def last_action(p, claude_login=None):
    """(time, "merge" | "close" | "comment") or (None, None)."""
    if p.get("mergedAt"):
        return seconds(p["mergedAt"]), "merge"
    if p.get("closedAt"):
        return seconds(p["closedAt"]), "close"
    human = [seconds(c["createdAt"]) for c in p.get("comments", []) if not by_claude(c, claude_login)]
    return (max(human), "comment") if human else (None, None)


def pick_pr(prs):
    """The PR that finished the card: the last merged, else the last closed, else the last opened."""
    for key in ("mergedAt", "closedAt", "createdAt"):
        done = [p for p in prs if p.get(key)]
        if done:
            return max(done, key=lambda p: seconds(p[key]))
    return None


def card_times(events, data, claude_login=None):
    """The card's times, from the events file's lines and the issue's GitHub data (see flatten).
    claude_login is Claude's own GitHub login, or None when the project has no Claude account."""
    number = data["number"]
    r = dict.fromkeys(FIELDS + ["build_start", "opened", "first_hand_off", "last_hand_off", "hand_off_times", "end",
                                "last_action", "unclassified"])
    r["issue"] = number
    p = pick_pr(data.get("prs", []))
    if not p:
        return r
    r["pr"] = p["number"]
    events = sorted((e for e in events if isinstance(e.get("time"), str)), key=lambda e: seconds(e["time"]))
    opened = seconds(p["createdAt"])
    end, r["last_action"] = last_action(p, claude_login)
    start = builder_start(events, p.get("headRefName"), number, opened)
    r.update(build_start=start, opened=opened, end=end, build_min=minutes(start, opened))
    hands = hand_offs(events, opened, end, p["number"], number) if end is not None else []
    if not hands:
        return r
    hand = hands[0]
    times = [seconds(e["time"]) for e in hands]
    h, last = times[0], times[-1]
    r.update(first_hand_off=h, last_hand_off=last, hand_off_times=times, hand_offs=len(hands),
             review_min=minutes(opened, h), human_min=round(human_time(events, hands, end, data, p, claude_login) / 60, 1))
    prompts = [e for e in events if e.get("event") == "UserPromptSubmit" and e.get("session") == hand.get("session")
               and h < seconds(e["time"]) <= end and not e.get("automatic")]
    unknown = sum(1 for e in prompts if "routine" not in e)
    chat = sum(1 for e in prompts if e.get("routine") is False)
    comments = sum(1 for c in p.get("comments", []) if not by_claude(c, claude_login) and not routine(c.get("body"))
                   and h < seconds(c["createdAt"]) <= end)
    r.update(chat=chat, pr_messages=comments, unclassified=unknown,
             messages=None if unknown else chat + comments)
    return r


# ---------------------------------------------------------------- the comment

def clock(t):
    return time.strftime("%H:%M", time.gmtime(t))


def comment(r, guessed=None):
    data = {k: r.get(k) for k in FIELDS}
    lines = [MARK + json.dumps(data) + " -->", "**Card times**, from `.apb/events.jsonl` and GitHub (UTC):", ""]
    pr = "PR #%s" % r["pr"] if r.get("pr") else "the PR"
    if r.get("build_min") is not None:
        lines.append("- Build: %d min, builder start %s to %s opened %s" % (
            round(r["build_min"]), clock(r["build_start"]), pr, clock(r["opened"])))
    elif not r.get("pr"):
        lines.append("- Build: not known, no PR closed this card")
    else:
        lines.append("- Build: not known, no builder in the events file named the PR's branch or the card")
    if r.get("review_min") is not None:
        lines.append("- Review: %d min, %s opened %s to the first hand-off to the human %s" % (
            round(r["review_min"]), pr, clock(r["opened"]), clock(r["first_hand_off"])))
        lines.append("- Human: %d min, the human's waits: from each hand-off to their next message or comment, "
                     "and from the last hand-off %s to the %s %s" % (
            round(r["human_min"]), clock(r["last_hand_off"]),
            {"merge": "merge", "close": "close", "comment": "human's last comment"}[r["last_action"]],
            clock(r["end"])))
        times = [clock(t) for t in r["hand_off_times"]]
        lines.append("- Hand-offs: %d, at %s" % (
            r["hand_offs"], times[0] if len(times) == 1 else ", ".join(times[:-1]) + " and " + times[-1]))
    else:
        lines.append("- Review: not known, the events file has no hand-off to the human for %s" % pr)
        lines.append("- Human: not known, for the same reason")
        lines.append("- Hand-offs: not known, for the same reason")
    what = "Messages from the human between the first hand-off and the %s, other than \"go\", a decision " \
           "letter, \"resolved\" or \"merged\"" % ("merge" if r.get("last_action") == "merge" else "last action")
    if r.get("messages") is not None:
        lines.append("- %s: %d (chat %d, PR %d)" % (what, r["messages"], r["chat"], r["pr_messages"]))
    elif r.get("unclassified"):
        lines.append("- %s: not known, %d prompt%s logged before apb 0.3.21 %s no routine mark" % (
            what, r["unclassified"], "" if r["unclassified"] == 1 else "s", "has" if r["unclassified"] == 1 else "have"))
    else:
        lines.append("- %s: not known, no hand-off" % what)
    if guessed:
        lines += ["", "Guessed beforehand: " + guessed]
    lines += ["", "— Claude (orchestrator)"]
    return "\n".join(lines)


def data_of(body):
    """The data line's numbers, or None if the comment has none."""
    m = re.search(re.escape(MARK) + r"(\{.*?\}) -->", body or "")
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except ValueError:
        return None


def posted(bodies):
    return any(data_of(b) is not None for b in bodies)


def collect(bodies):
    return [d for d in map(data_of, bodies) if d]


# ---------------------------------------------------------------- GitHub

QUERY = """query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) {
  issue(number: $number) { number state comments(last: 100) { nodes { body createdAt author { login } } }
    closedByPullRequestsReferences(first: 10, includeClosedPrs: true) { nodes {
      number createdAt mergedAt closedAt headRefName
      comments(first: 100) { nodes { body createdAt author { login } } }
      reviews(first: 100) { nodes { body submittedAt author { login } } }
      reviewThreads(first: 100) { nodes { comments(first: 100) { nodes { body createdAt author { login } } } } }
    } } } } }"""


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=TIME_LIMIT).stdout


def login(node):
    """The author's login; None for a deleted account, which GitHub gives no author."""
    return (node.get("author") or {}).get("login")


def flatten(got):
    """The issue's data from the GraphQL answer: every comment on a PR, of any kind, in one list."""
    i = got["data"]["repository"]["issue"]
    prs = []
    for p in i["closedByPullRequestsReferences"]["nodes"]:
        comments = [{"body": c["body"], "createdAt": c["createdAt"], "author": login(c)} for c in p["comments"]["nodes"]]
        comments += [{"body": v["body"], "createdAt": v["submittedAt"], "author": login(v)} for v in p["reviews"]["nodes"]
                     if (v.get("body") or "").strip() and v.get("submittedAt")]
        comments += [{"body": c["body"], "createdAt": c["createdAt"], "author": login(c)}
                     for t in p["reviewThreads"]["nodes"] for c in t["comments"]["nodes"]]
        prs.append({k: p[k] for k in ("number", "createdAt", "mergedAt", "closedAt", "headRefName")}
                   | {"comments": comments})
    return {"number": i["number"], "state": i["state"], "comments": [c["body"] for c in i["comments"]["nodes"]],
            "card_comments": [{"body": c["body"], "createdAt": c.get("createdAt"), "author": login(c)}
                              for c in i["comments"]["nodes"]],
            "prs": prs}


def read_events(path):
    events = []
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                if isinstance(e, dict) and isinstance(e.get("event"), str) and isinstance(e.get("time"), str):
                    events.append(e)
    except OSError:
        pass
    return events


def main(argv):
    parser = argparse.ArgumentParser(description="Post a closed card's build, review and human times on its issue.")
    parser.add_argument("issue", type=int, nargs="?")
    parser.add_argument("--repo", help="owner/name; by default, this folder's repo")
    parser.add_argument("--events", default=os.path.join(".apb", "events.jsonl"))
    parser.add_argument("--guessed", help="the times guessed before the card started, added as a line")
    parser.add_argument("--post", action="store_true", help="post the comment on the issue")
    parser.add_argument("--collect", action="store_true", help="list every card's numbers from the comments")
    parser.add_argument("--claude-login", help="Claude's own GitHub login; by default, from the project's settings")
    args = parser.parse_args(argv)
    if args.issue is None and not args.collect:
        parser.error("give an issue number, or --collect")
    try:
        repo = args.repo or json.loads(run(["gh", "repo", "view", "--json", "nameWithOwner"]))["nameWithOwner"]
        if args.collect:
            bodies = json.loads(run(["gh", "api", "--paginate", "--slurp", "repos/%s/issues/comments" % repo]))
            print("\t".join(FIELDS))
            for d in collect([c.get("body") for page in bodies for c in page]):
                print("\t".join("" if d.get(k) is None else str(d.get(k)) for k in FIELDS))
            return 0
        events = read_events(args.events)
        if not events:
            print("Nothing printed or posted: no events in %s. Run it from the project's root, or give "
                  "--events." % os.path.abspath(args.events), file=sys.stderr)
            return 1
        owner, name = repo.split("/", 1)
        data = flatten(json.loads(run(["gh", "api", "graphql", "-f", "owner=" + owner, "-f", "name=" + name,
                                       "-F", "number=%d" % args.issue, "-f", "query=" + QUERY])))
        text = comment(card_times(events, data, authors.claude_login(args.claude_login)), args.guessed)
        print(text)
        if not args.post:
            return 0
        if data["state"] != "CLOSED":
            print("Not posted: issue #%d is still open. Post when the card closes." % args.issue, file=sys.stderr)
            return 1
        if posted(data["comments"]):
            print("Not posted: issue #%d already has its card times." % args.issue)
            return 0
        run(["gh", "issue", "comment", str(args.issue), "--repo", repo, "--body", text])
        print("Posted on issue #%d." % args.issue)
    except subprocess.CalledProcessError as e:
        print("gh failed: " + (e.stderr or e.stdout or "").strip(), file=sys.stderr)
        return 2
    except subprocess.TimeoutExpired as e:
        print("gh timed out after %d seconds: %s" % (TIME_LIMIT, " ".join(map(str, e.cmd))[:200]), file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
