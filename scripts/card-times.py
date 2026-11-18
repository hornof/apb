"""Card times: how long a closed card spent in build, in review and with the human, posted on its issue.

Usage, from the project's repo root:
    python3 <apb plugin folder>/scripts/card-times.py <issue number> [--repo owner/name] [--post]
        [--guessed "build 15, review 5, human 10"] [--events .apb/events.jsonl]
    python3 <apb plugin folder>/scripts/card-times.py --collect [--repo owner/name]

It works the times out from .apb/events.jsonl and GitHub, never by hand:
- build: the builder's start to the PR opening. The builder is the earliest builder agent, started
  before the PR opened, that named the PR's branch in a shell command (a push), or else the issue
  (#N, issue-N, issue view N, issues/N). An agent resumed later keeps its first start.
- review: the PR opening to the first hand-off to the human: the first Stop line with holder "human"
  after the PR opened that names the PR or the card (#N, /pull/N), or else the first one at all.
- human: the hand-off to the human's last action on the PR: the merge, or the close, or for a PR still
  open, the human's last comment. GitHub doesn't say when a thread was resolved, so that isn't used.
- messages: the human's messages between the hand-off and that last action that aren't "go", a
  decision letter, "resolved" or "merged": prompts in the orchestrator's session (hooks/apb-events.py
  marks each one routine or not, without keeping its text) and the human's comments on the PR. A
  comment signed "— Claude (<role>)" is Claude's, since Claude uses the human's login. Prompts logged
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
FIELDS = ["issue", "pr", "build_min", "review_min", "human_min", "messages", "chat", "pr_messages"]

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("apb_events", os.path.join(HERE, "..", "hooks", "apb-events.py"))
events_hook = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(events_hook)
routine = events_hook.routine  # one rule for what counts as routine, in the hook and here


def seconds(text):
    """Seconds since the epoch for an events-file or GitHub time (UTC, with or without fractions)."""
    whole = calendar.timegm(tuple(int(x) for x in re.split(r"[-T:]", text[:19])) + (0, 0, 0))
    m = re.match(r"\.(\d+)", text[19:])
    return whole + (float("0." + m.group(1)) if m else 0.0)


def is_claude(body):
    return bool(re.search(r"[—-]\s*Claude \(\w+\)", body or ""))


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


def hand_off(events, opened, end, pr, number):
    """The first Stop line handing over to the human between opened and end, preferring one that
    names the PR or the card. None if there is none."""
    stops = [e for e in events if e.get("event") == "Stop" and e.get("holder") == "human"
             and opened <= seconds(e["time"]) <= end]
    named = [e for e in stops if names(e.get("todo"), pr, number)]
    return (named or stops or [None])[0]


def last_action(p):
    """(time, "merge" | "close" | "comment") or (None, None)."""
    if p.get("mergedAt"):
        return seconds(p["mergedAt"]), "merge"
    if p.get("closedAt"):
        return seconds(p["closedAt"]), "close"
    human = [seconds(c["createdAt"]) for c in p.get("comments", []) if not is_claude(c.get("body"))]
    return (max(human), "comment") if human else (None, None)


def pick_pr(prs):
    """The PR that finished the card: the last merged, else the last closed, else the last opened."""
    for key in ("mergedAt", "closedAt", "createdAt"):
        done = [p for p in prs if p.get(key)]
        if done:
            return max(done, key=lambda p: seconds(p[key]))
    return None


def card_times(events, data):
    """The card's times, from the events file's lines and the issue's GitHub data (see flatten)."""
    number = data["number"]
    r = dict.fromkeys(FIELDS + ["build_start", "opened", "hand_off", "end", "last_action", "unclassified"])
    r["issue"] = number
    p = pick_pr(data.get("prs", []))
    if not p:
        return r
    r["pr"] = p["number"]
    events = sorted((e for e in events if isinstance(e.get("time"), str)), key=lambda e: seconds(e["time"]))
    opened = seconds(p["createdAt"])
    end, r["last_action"] = last_action(p)
    start = builder_start(events, p.get("headRefName"), number, opened)
    r.update(build_start=start, opened=opened, end=end, build_min=minutes(start, opened))
    hand = hand_off(events, opened, end, p["number"], number) if end is not None else None
    if not hand:
        return r
    h = seconds(hand["time"])
    r.update(hand_off=h, review_min=minutes(opened, h), human_min=minutes(h, end))
    prompts = [e for e in events if e.get("event") == "UserPromptSubmit" and e.get("session") == hand.get("session")
               and h < seconds(e["time"]) <= end and not e.get("automatic")]
    unknown = sum(1 for e in prompts if "routine" not in e)
    chat = sum(1 for e in prompts if e.get("routine") is False)
    comments = sum(1 for c in p.get("comments", []) if not is_claude(c.get("body")) and not routine(c.get("body"))
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
        lines.append("- Review: %d min, %s opened to the hand-off to the human %s" % (
            round(r["review_min"]), pr, clock(r["hand_off"])))
        lines.append("- Human: %d min, the hand-off to the %s %s" % (
            round(r["human_min"]), {"merge": "merge", "close": "close", "comment": "human's last comment"}
            [r["last_action"]], clock(r["end"])))
    else:
        lines.append("- Review: not known, the events file has no hand-off to the human for %s" % pr)
        lines.append("- Human: not known, for the same reason")
    what = "Messages from the human between the hand-off and the %s, other than \"go\", a decision letter, " \
           "\"resolved\" or \"merged\"" % ("merge" if r.get("last_action") == "merge" else "last action")
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
  issue(number: $number) { number state comments(last: 100) { nodes { body } }
    closedByPullRequestsReferences(first: 10, includeClosedPrs: true) { nodes {
      number createdAt mergedAt closedAt headRefName
      comments(first: 100) { nodes { body createdAt } }
      reviews(first: 100) { nodes { body submittedAt } }
      reviewThreads(first: 100) { nodes { comments(first: 100) { nodes { body createdAt } } } } } } } } }"""


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=TIME_LIMIT).stdout


def flatten(got):
    """The issue's data from the GraphQL answer: every comment on a PR, of any kind, in one list."""
    i = got["data"]["repository"]["issue"]
    prs = []
    for p in i["closedByPullRequestsReferences"]["nodes"]:
        comments = [{"body": c["body"], "createdAt": c["createdAt"]} for c in p["comments"]["nodes"]]
        comments += [{"body": v["body"], "createdAt": v["submittedAt"]} for v in p["reviews"]["nodes"]
                     if (v.get("body") or "").strip() and v.get("submittedAt")]
        comments += [{"body": c["body"], "createdAt": c["createdAt"]}
                     for t in p["reviewThreads"]["nodes"] for c in t["comments"]["nodes"]]
        prs.append({k: p[k] for k in ("number", "createdAt", "mergedAt", "closedAt", "headRefName")}
                   | {"comments": comments})
    return {"number": i["number"], "state": i["state"], "comments": [c["body"] for c in i["comments"]["nodes"]],
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
        owner, name = repo.split("/", 1)
        data = flatten(json.loads(run(["gh", "api", "graphql", "-f", "owner=" + owner, "-f", "name=" + name,
                                       "-F", "number=%d" % args.issue, "-f", "query=" + QUERY])))
        text = comment(card_times(read_events(args.events), data), args.guessed)
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
