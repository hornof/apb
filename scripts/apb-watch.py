"""Wait in the background, and exit as soon as something needs the orchestrator.

It exits, printing what it found, when:
- GitHub: a PR is opened, merged or closed, a PR's checks finish, a PR gets new comments, review
  comments, replies on review comments or reviews, one of a PR's review threads (Decision threads) is
  resolved, all of them are, or a watched issue gets new comments (each issue on its own line; an issue
  not watched at the last read counts the comments posted since that read). A merged or closed PR stays watched
  while it has an unresolved thread (at most MAX_WATCHED such PRs).
- Stuck: a command started from one of Claude Code's shells has used no CPU for --stuck-minutes.
- Quiet: an agent that is still running has written nothing to .apb/events.jsonl for --quiet-minutes.
- Check-in: --check-in minutes have passed (use it while agents run, to look at their progress).
- A followed card (#165): --follow-file names the cards' issues it also watches, and their check-in,
  which comes only while an agent runs. It is read at every check, so the hook's one watcher picks up
  a card without a second watcher reading every PR again.
- No change: --limit minutes have passed, so it never runs forever.
- Budget: the account's GraphQL budget is low or used up. Said once per budget hour, for all the
  watchers on this machine.

It checks processes and the events file every TICK seconds. It reads GitHub every ACTIVE seconds while
the repo has had activity in the last hour, every QUIET seconds once it has been quiet that long (or
every --poll seconds, if given). Each read is two queries at most: a cheap check of what changed, then
a full read of only the PRs that changed. Every watcher on this machine keeps a file in a shared folder
(~/.apb/watchers, or $APB_WATCHERS_DIR); each slows down so that together they spend under CEILING
points an hour of the account's 5,000.

It saves what it saw in the --state file, so nothing that happens between two runs is missed and
nothing is reported twice. It only reads; it never stops a process or changes anything on GitHub.

Usage (from the project folder, in the background):
  python3 <apb plugin>/scripts/apb-watch.py --state <scratch folder>/watch.json [--repo owner/name]
      [--issue N ...] [--check-in 5] [--limit 60] [--stuck-minutes 10] [--quiet-minutes 15] [--poll S]
      [--follow-file F]
  python3 <apb plugin>/scripts/apb-watch.py --follow N [--check-in 5]   (or --unfollow N)
      adds the card's issue to (or takes it from) .apb/follow.json, which the hook's watcher reads
  python3 <apb plugin>/scripts/apb-watch.py --list
      lists the watchers running on this machine, each with the session that holds it (its name,
      `apb orch N · <version> · <start time PT>`, from .apb/watcher-owner.json), and the account's
      GraphQL budget
"""
import argparse
import calendar
import json
import os
import re
import signal
import subprocess
import sys
import time

SHELLS = {"sh", "bash", "zsh", "dash", "fish", "ksh"}
NEVER_RAN = 1.0  # seconds of CPU; a process that has used less has been idle since it started
ABANDONED = 3 * 3600  # an agent silent this long is taken to be gone, even without a stop event
FINISHED = ("SUCCESS", "FAILURE", "ERROR")


# ---------------------------------------------------------------- processes

def parse_clock(text):
    """'1:39.50', '44:37', '00:01:02', '09-02:09:50' (ps time and etime) -> seconds."""
    days = 0
    if "-" in text:
        d, text = text.split("-", 1)
        days = int(d)
    seconds = 0.0
    for part in text.split(":"):
        seconds = seconds * 60 + float(part)
    return days * 86400 + seconds


def read_ps():
    out = subprocess.run(["ps", "-A", "-o", "pid=,ppid=,time=,etime=,command=", "-ww"],
                         capture_output=True, text=True, timeout=20)
    return out.stdout


def parse_ps(text):
    procs = {}
    for line in text.splitlines():
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        try:
            procs[int(parts[0])] = {"ppid": int(parts[1]), "cpu": parse_clock(parts[2]),
                                    "elapsed": parse_clock(parts[3]), "command": parts[4]}
        except ValueError:
            continue
    return procs


def first_word(command):
    words = command.split()
    return os.path.basename(words[0]).lstrip("-") if words else ""


def is_shell(proc):
    return first_word(proc["command"]) in SHELLS


def find_root(procs, me):
    """Claude Code's own process: the first ancestor of this script that isn't a shell."""
    seen = set()
    pid = procs.get(me, {}).get("ppid")
    while pid in procs and pid not in seen:
        seen.add(pid)
        if not is_shell(procs[pid]):
            return pid
        pid = procs[pid]["ppid"]
    return None


def watched(procs, root, me):
    """The commands Claude Code's shells are running now: the last process of each chain started
    from a shell that is a direct child of root, leaving out this script, its own children, sleep,
    and processes that have already finished."""
    if root not in procs:
        return []
    children = {}
    for pid, p in procs.items():
        children.setdefault(p["ppid"], []).append(pid)
    skip, todo = set(), [me]
    while todo:  # this script and everything it started
        pid = todo.pop()
        if pid not in skip:
            skip.add(pid)
            todo.extend(children.get(pid, []))
    # The shells that started this script have a child (this script), so the leaf rule below leaves them out.
    found, seen = [], {root}
    todo = [c for c in children.get(root, []) if is_shell(procs[c])]
    while todo:
        pid = todo.pop()
        if pid in seen:
            continue
        seen.add(pid)
        kids = children.get(pid, [])
        todo.extend(kids)
        p = procs[pid]
        if (pid in skip or kids or first_word(p["command"]) == "sleep" or "apb-watch" in p["command"]
                or "<defunct>" in p["command"]):  # a finished process its parent hasn't collected yet
            continue
        found.append(pid)
    return sorted(found)


def track_idle(idle, procs, pids, now, stuck):
    """Update idle (saved between runs) and return the processes that just reached `stuck` seconds
    without using CPU. Each idle stretch is reported once."""
    keys = {}
    for pid in pids:
        p = procs[pid]
        keys["%d %s" % (pid, p["command"])] = pid
    for key in list(idle):
        if key not in keys:
            del idle[key]
    found = []
    for key, pid in keys.items():
        p = procs[pid]
        seen = idle.get(key)
        if seen is None:
            seen = idle[key] = {"cpu": p["cpu"], "reported": False,
                                "since": now - p["elapsed"] if p["cpu"] < NEVER_RAN else now}
        elif p["cpu"] != seen["cpu"]:
            seen.update(cpu=p["cpu"], since=now, reported=False)
        if not seen["reported"] and now - seen["since"] >= stuck:
            seen["reported"] = True
            found.append({"pid": pid, "command": p["command"], "idle": now - seen["since"],
                          "elapsed": p["elapsed"]})
    return found


# ---------------------------------------------------------------- GitHub

MAX_WATCHED = 20  # merged or closed PRs kept watched for their unresolved Decision threads

# How often GitHub is read. All the watchers on one machine share one account's GraphQL budget,
# 5,000 points an hour; together they keep under CEILING, leaving the rest for the agents.
ACTIVE = 120  # seconds between reads while the repo has had activity in the last ACTIVE_WINDOW
QUIET = 900  # seconds between reads once it has been quiet that long
ACTIVE_WINDOW = 3600
TICK = 20  # seconds between local checks (stuck commands, quiet agents, check-in, limit)
FOLLOW = os.path.join(".apb", "follow.json")  # the cards the hook's watcher follows (#165)
CEILING = 1000  # points an hour for all the watchers on this machine together
LOW = 1000  # points left in the account's hour: below this, read every QUIET and say so once
FIRST_COST = 2  # points a read is taken to cost until one has been measured

# Stage 1, every read: what changed, cheaply (1 point). Stage 2: the full read, only for the PRs
# whose stage-1 answer changed (about 1 point for every two PRs).
OPEN_PAGE = 10  # open PRs, most recently updated first
RECENT_PAGE = 5  # merged or closed PRs, most recently updated first
THREADS_CHECKED = 20  # review threads per PR in stage 1, for their resolved state
THREADS_READ = 50  # review threads per PR in stage 2
ISSUE_TIMES = 20  # latest comments per watched issue whose times stage 1 reads
BUDGET = "rateLimit { cost remaining limit resetAt }"
ROLLUP = "commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }"
RESOLVED = "reviewThreads(first: %d) { nodes { isResolved } }" % THREADS_CHECKED
# Each review thread with its state and its first comment, whose id names the thread in a link.
THREADS = "reviewThreads(first: %d) { nodes { isResolved comments { totalCount } " \
          "opening: comments(first: 1) { nodes { databaseId } } } }" % THREADS_READ
DETAIL = "number title %s %s comments { totalCount } reviews { totalCount }" % (ROLLUP, THREADS)
HEAD = "query($owner: String!, $name: String!) { %s repository(owner: $owner, name: $name) {" % BUDGET


def check_query(issues, watch):
    """Stage 1: the open PRs and the recent merges and closes, with when each last changed; the
    watched issues; and the merged or closed PRs in watch, with their threads' resolved state."""
    # The latest comments' times tell what is new on an issue that wasn't watched at the last read.
    asks = "".join("\n    i%d: issue(number: %d) { title state updatedAt comments(last: %d) { totalCount nodes "
                   "{ createdAt } } }" % (n, n, ISSUE_TIMES) for n in issues)
    asks += "".join("\n    w%d: pullRequest(number: %d) { number updatedAt %s }" % (n, n, RESOLVED) for n in watch)
    return HEAD + """
    open: pullRequests(first: %d, states: OPEN, orderBy: {field: UPDATED_AT, direction: DESC}) { nodes {
      number updatedAt %s %s } }
    recent: pullRequests(first: %d, states: [MERGED, CLOSED], orderBy: {field: UPDATED_AT, direction: DESC}) {
      nodes { number title state updatedAt } }
    latest: issues(first: 1, orderBy: {field: UPDATED_AT, direction: DESC}) { nodes { updatedAt } }""" \
        % (OPEN_PAGE, ROLLUP, RESOLVED, RECENT_PAGE) + asks + "\n} }"


def detail_query(numbers):
    """Stage 2: each PR in numbers, in full."""
    return HEAD + "".join("\n    p%d: pullRequest(number: %d) { %s }" % (n, n, DETAIL) for n in numbers) + "\n} }"


def thread_states(pr):
    """{first comment's id: resolved} for each of the PR's review threads, as strings for the state file."""
    found = {}
    for t in ((pr or {}).get("reviewThreads") or {}).get("nodes") or []:
        first = (t.get("opening") or {}).get("nodes") or []
        if first and first[0].get("databaseId") is not None:
            found[str(first[0]["databaseId"])] = bool(t.get("isResolved"))
    return found


def still_watched(snap):
    """The merged or closed PRs in the snapshot that still have an unresolved review thread: at most
    MAX_WATCHED, newest first."""
    if not isinstance(snap, dict):
        return []
    found = [int(n) for n, t in (snap.get("threads") or {}).items()
             if n not in snap.get("open", {}) and False in t.values()]
    return sorted(found, reverse=True)[:MAX_WATCHED]


def normalize(repo, issues):
    snap = {"open": {}, "merged": {}, "closed": {}, "issues": {}, "threads": {}}
    numbers = [0]
    for key, p in repo.items():
        if key in ("open", "merged", "closed"):
            nodes = p["nodes"]
        elif key.startswith("w") and isinstance(p, dict):  # a merged or closed PR kept watched
            nodes = [p]
        else:
            continue
        for node in nodes:
            threads = thread_states(node)
            if threads:
                snap["threads"][str(node["number"])] = threads
    for p in repo["open"]["nodes"]:
        commits = p["commits"]["nodes"]
        rollup = commits[0]["commit"]["statusCheckRollup"] if commits else None
        threads = p["reviewThreads"]["nodes"]
        snap["open"][str(p["number"])] = {
            "title": p["title"], "checks": (rollup or {}).get("state", "NONE"), "threads": len(threads),
            "open_threads": sum(1 for t in threads if not t["isResolved"]),
            "comments": p["comments"]["totalCount"],
            # comments in a thread after its first one: replies, for example on a Decision thread
            "replies": sum(t["comments"]["totalCount"] - 1 for t in threads),
            "reviews": p["reviews"]["totalCount"]}
        numbers.append(p["number"])
    for kind in ("merged", "closed"):
        for p in repo[kind]["nodes"]:
            snap[kind][str(p["number"])] = p["title"]
            numbers.append(p["number"])
    for n in issues:
        i = repo.get("i%d" % n)
        if i:
            snap["issues"][str(n)] = {"title": i["title"], "state": i["state"],
                                      "comments": i["comments"]["totalCount"]}
    snap["max"] = max(numbers)
    return snap


class RateLimited(Exception):
    """GitHub refused: the account's GraphQL budget is used up until reset (epoch seconds, or None)."""

    def __init__(self, reset):
        Exception.__init__(self, "rate-limited until %s" % (clock(reset) if reset else "the hour's reset"))
        self.reset = reset


def is_rate_limit(text):
    return bool(re.search(r"rate limit", text or "", re.I))


def rate_budget():
    """The account's GraphQL budget, {"remaining", "limit", "reset"}, or None if gh can't say.
    GitHub doesn't count this call against any budget."""
    try:
        out = subprocess.run(["gh", "api", "rate_limit"], capture_output=True, text=True, timeout=30)
        g = json.loads(out.stdout)["resources"]["graphql"]
        return {"remaining": g["remaining"], "limit": g["limit"], "reset": g["reset"]}
    except Exception:
        return None


def gh_graphql(owner, name, query):
    """The data of one GraphQL answer, with its rateLimit. Raises RateLimited or RuntimeError."""
    out = subprocess.run(["gh", "api", "graphql", "-f", "owner=" + owner, "-f", "name=" + name,
                          "-f", "query=" + query], capture_output=True, text=True, timeout=60)
    text = (out.stderr or "") + (out.stdout or "")
    if out.returncode != 0 and is_rate_limit(text):
        raise RateLimited((rate_budget() or {}).get("reset"))
    try:
        data = json.loads(out.stdout).get("data")
    except (ValueError, AttributeError):
        data = None
    # A PR or issue GitHub no longer has comes back as null, with an error; the rest of the answer counts.
    if not isinstance(data, dict) or not data.get("repository"):
        raise RuntimeError(text.strip()[:200])
    return data


def gh_snapshot(repo, issues, watch=(), cache=None, graphql=gh_graphql):
    """The snapshot of repo, read in two stages. cache (kept in the state file) holds each PR's
    stage-1 answer and its full read, so a PR is read in full again only when its stage-1 answer
    changes. The snapshot's "meta" says what the reads cost, the budget left, and the newest activity."""
    owner, name = repo.split("/", 1)
    cache = {} if cache is None else cache
    first = graphql(owner, name, check_query(issues, watch))
    r = first["repository"]
    opened, recent = r["open"]["nodes"], r["recent"]["nodes"]
    watched = [r["w%d" % n] for n in watch if r.get("w%d" % n)]
    marks = {}  # each PR's stage-1 answer: when anything in it changes, the PR is read in full again
    for role, nodes in (("open", opened), ("recent", recent), ("watch", watched)):
        for p in nodes:
            marks.setdefault(str(p["number"]), {})[role] = p
    # Open PRs past the first page stay as last read, unless they turn up merged or closed.
    carried = [n for n, c in cache.items() if c.get("open") and n not in marks]
    stale = [int(n) for n, m in marks.items() if (cache.get(n) or {}).get("mark") != m]
    budget = first["rateLimit"]
    meta = {"cost": budget["cost"], "remaining": budget["remaining"], "limit": budget.get("limit"),
            "reset": parse_time(budget["resetAt"])}
    if stale:
        second = graphql(owner, name, detail_query(stale))
        meta["cost"] += second["rateLimit"]["cost"]
        meta["remaining"] = second["rateLimit"]["remaining"]
        for n in stale:
            p = second["repository"].get("p%d" % n)
            if p:
                cache[str(n)] = {"mark": marks[str(n)], "node": p}
    still_open = {str(p["number"]) for p in opened} | set(carried)
    for n in list(cache):
        if n in marks or n in carried:
            cache[n]["open"] = n in still_open
        else:
            del cache[n]
    node = lambda n: (cache.get(str(n)) or {}).get("node")
    answer = {"open": {"nodes": [node(p["number"]) for p in opened if node(p["number"])]
                       + [cache[n]["node"] for n in carried]},
              "merged": {"nodes": []}, "closed": {"nodes": []}}
    for p in recent:
        if node(p["number"]):
            answer["merged" if p["state"] == "MERGED" else "closed"]["nodes"].append(
                dict(node(p["number"]), title=p["title"]))
    meta["issue_times"] = {}
    for n in issues:
        i = answer["i%d" % n] = r.get("i%d" % n)
        if i:
            meta["issue_times"][str(n)] = [parse_time(c["createdAt"]) for c in i["comments"].get("nodes") or []]
    for p in watched:
        answer["w%d" % p["number"]] = node(p["number"])
    times = [p["updatedAt"] for p in opened + recent + r["latest"]["nodes"]]
    times += [r["i%d" % n]["updatedAt"] for n in issues if r.get("i%d" % n)]
    meta["newest"] = max(parse_time(t) for t in times) if times else None
    snap = normalize(answer, issues)
    snap["meta"] = meta
    return snap


def gh_repo():
    out = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0 or "/" not in out.stdout:
        sys.exit("apb-watch: no --repo given, and gh can't tell which repo this folder is: "
                 + (out.stderr or out.stdout).strip())
    return out.stdout.strip()


def count(n, word, plural=None):
    return "%d %s" % (n, word if n == 1 else plural or word + "s")


def gh_changes(old, new, repo=None, fresh=None):
    """What changed between two snapshots, as lines for the orchestrator. repo (owner/name) makes a
    resolved thread's line a link. fresh: {issue: comments posted since the last read} for the issues
    that weren't watched then."""
    out = []
    was_open = old["open"]
    for kind in ("merged", "closed"):
        for n, title in new[kind].items():
            # New to us: open at the last check, or a number we had never seen (opened and closed in between).
            if n in was_open or (int(n) > old.get("max", 0) and n not in old[kind]):
                out.append("PR #%s merged: %s" % (n, title) if kind == "merged"
                           else "PR #%s closed without merging: %s" % (n, title))
    # Each Decision thread that became resolved, on an open, merged or closed PR. A state saved by an
    # older version has no threads: compare from now on.
    for n, threads in (new.get("threads") or {}).items():
        before = (old.get("threads") or {}).get(n, {})
        for i, resolved in threads.items():
            if resolved and before.get(i) is False:
                out.append("PR #%s: decision thread resolved: %s" % (
                    n, "https://github.com/%s/pull/%s#discussion_r%s" % (repo, n, i) if repo else "r" + i))
    for n, p in new["open"].items():
        o = was_open.get(n)
        if o is None:
            out.append("PR #%s opened: %s (checks %s)" % (n, p["title"], p["checks"]))
            continue
        if p["checks"] != o["checks"] and p["checks"] in FINISHED:
            out.append("PR #%s checks finished: %s" % (n, p["checks"]))
        if p["threads"] > o["threads"]:
            out.append("PR #%s: %s" % (n, count(p["threads"] - o["threads"], "new review comment")))
        if p["comments"] > o["comments"]:
            out.append("PR #%s: %s" % (n, count(p["comments"] - o["comments"], "new comment")))
        # A state saved by an older version has no replies or reviews yet: compare from now on.
        if p.get("replies", 0) > o.get("replies", p.get("replies", 0)):
            out.append("PR #%s: %s on a review comment" % (
                n, count(p["replies"] - o["replies"], "new reply", "new replies")))
        if p.get("reviews", 0) > o.get("reviews", p.get("reviews", 0)):
            out.append("PR #%s: %s" % (n, count(p["reviews"] - o["reviews"], "new review")))
        if o["open_threads"] > 0 and p["open_threads"] == 0 and p["threads"] > 0:
            out.append("PR #%s: all review comments resolved" % n)
    for n, i in new["issues"].items():
        o = old["issues"].get(n)
        if o is None:  # not watched at the last read: only the comments posted since then are new
            if (fresh or {}).get(n):
                out.append("Issue #%s: %s (%s)" % (n, count(fresh[n], "new comment"), i["title"]))
            continue
        if i["comments"] > o["comments"]:
            out.append("Issue #%s: %s (%s)" % (n, count(i["comments"] - o["comments"], "new comment"), i["title"]))
        if i["state"] != o["state"]:
            out.append("Issue #%s is now %s (%s)" % (n, i["state"].lower(), i["title"]))
    return out


# ---------------------------------------------------------------- the events file

def parse_time(text):
    return calendar.timegm(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S"))


def read_events(path):
    events = []
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return events
    for line in text.splitlines():
        try:
            e = json.loads(line)
            e["t"] = parse_time(e["time"])
        except (ValueError, TypeError, KeyError):
            continue
        events.append(e)
    return events


def running_agents(events, now):
    agents = {}
    for e in events:
        a = e.get("agent_id")
        if not a:
            continue
        t = e["t"] if "t" in e else parse_time(e["time"])
        if e.get("event") == "SubagentStop":
            agents.pop(a, None)
            continue
        what = e.get("event", "?")
        if e.get("command"):
            what += ": " + str(e["command"])[:100]
        agents[a] = {"type": e.get("agent_type") or agents.get(a, {}).get("type") or "agent",
                     "last": t, "what": what}
    return {a: v for a, v in agents.items() if now - v["last"] <= ABANDONED}


def quiet_agents(agents, now, quiet, reported):
    out = []
    for a, v in agents.items():
        if now - v["last"] >= quiet and reported.get(a) != v["last"]:
            reported[a] = v["last"]
            out.append("Quiet: %s (%s) has written no event for %d min. Last: %s at %s." % (
                v["type"], a, (now - v["last"]) // 60, v["what"], clock(v["last"])))
    return out


# ---------------------------------------------------------------- times

def clock(t):
    return time.strftime("%H:%M", time.localtime(t))


def minutes(s):
    return "%d min" % (s // 60)


def span(seconds):
    m = int(seconds) // 60
    if m < 1:
        return "under 1 min"
    return "%d min" % m if m < 60 else "%d h %d min" % (m // 60, m % 60)


def every(seconds):
    return "every %g s" % seconds if seconds < 60 else "every %d min" % round(seconds / 60)


# ---------------------------------------------------------------- pace and budget

def github_interval(now, active_at, live, cost, remaining=None, reset=None, fixed=None):
    """Seconds until the next GitHub read. ACTIVE while the repo has had activity in the last
    ACTIVE_WINDOW, else QUIET; --poll (fixed) instead, if given. Without --poll, never so often that
    `live` watchers each spending `cost` points a read would pass CEILING points an hour together.
    With under LOW points left in the account's hour, at most one read every QUIET until the reset."""
    if fixed:
        pace = fixed
    else:
        pace = ACTIVE if now - active_at < ACTIVE_WINDOW else QUIET
        pace = max(pace, max(1, live) * cost * 3600.0 / CEILING)
    if remaining is not None and remaining < LOW and reset is not None and reset > now:
        pace = max(pace, min(QUIET, reset - now))
    return pace


# ---------------------------------------------------------------- the watchers on this machine
# Each running watcher keeps a file in one shared folder, so the watchers can share the ceiling, and
# `apb-watch.py --list` (and the progress page) can show them.

def registry_dir():
    return os.environ.get("APB_WATCHERS_DIR") or os.path.join(os.path.expanduser("~"), ".apb", "watchers")


def entry(folder, pid, repo):
    return os.path.join(folder, "%s-%d.json" % (repo.replace("/", "_"), pid))


def register(folder, pid, record):
    """Write (or rewrite) this watcher's file. Never stops the watcher if it can't."""
    try:
        os.makedirs(folder, exist_ok=True)
        path = entry(folder, pid, record["repo"])
        with open(path + ".tmp", "w") as f:
            json.dump(dict(record, pid=pid), f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def unregister(folder, pid):
    try:
        for name in os.listdir(folder):
            if name.endswith("-%d.json" % pid):
                os.remove(os.path.join(folder, name))
    except OSError:
        pass


FRESH = 60  # seconds: a watcher file this new may belong to a watcher that started after the ps snapshot


def live_watchers(folder, procs):
    """The records of the watchers running now: their pid is in procs, running apb-watch, or their
    file is under FRESH seconds old (the watcher may have started after procs was read). A file whose
    process is gone, or that can't be read, is removed once it is older than that. With no procs
    (ps failed), nothing counts and nothing is removed."""
    found = []
    if not procs:
        return found
    try:
        names = sorted(n for n in os.listdir(folder) if n.endswith(".json"))
    except OSError:
        return found
    for name in names:
        path = os.path.join(folder, name)
        fresh = True  # a file that vanishes while being read is left alone
        try:
            fresh = time.time() - os.path.getmtime(path) < FRESH
            with open(path) as f:
                record = json.load(f)
            pid = record["pid"]
        except (OSError, ValueError, TypeError, KeyError):
            record, pid = None, None
        if pid in procs:
            if "apb-watch" in procs[pid].get("command", ""):
                found.append(record)
        elif record is not None and fresh:
            found.append(record)
        elif not fresh:
            try:
                os.remove(path)
            except OSError:
                pass
    return found


def claim_notice(folder, reset):
    """True the first time any watcher on this machine claims the notice for this budget hour."""
    try:
        os.makedirs(folder, exist_ok=True)
        for name in os.listdir(folder):  # notices from earlier hours
            path = os.path.join(folder, name)
            if name.startswith("notice-") and time.time() - os.path.getmtime(path) > 86400:
                os.remove(path)
        os.close(os.open(os.path.join(folder, "notice-%d" % (reset or 0)), os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        return True
    except FileExistsError:
        return False
    except OSError:
        return True


def account_budget(records, now, fallback):
    """The account's GraphQL budget: the newest rateLimit a live watcher recorded from its own reads
    (the figure the watchers pace themselves on), with "source": "watcher" and "at": when it was read.
    When no live watcher has one for the current hour, fallback() (gh api rate_limit), with
    "source": "gh" and "at": now. None if neither has one."""
    seen = [r["budget"] for r in records if isinstance(r.get("budget"), dict)
            and r["budget"].get("at") is not None and (r["budget"].get("reset") or 0) > now]
    if seen:
        return dict(max(seen, key=lambda b: b["at"]), source="watcher")
    b = fallback()
    return dict(b, source="gh", at=now) if b else None


def budget_line(budget, now):
    if not budget:
        return "GitHub GraphQL budget: could not be read (gh api rate_limit)."
    left = "%d of %d" % (budget["remaining"], budget["limit"]) if budget.get("limit") else "%d" % budget["remaining"]
    line = "GitHub GraphQL budget: %s points left, resets at %s" % (left, clock(budget["reset"]))
    if budget.get("source"):
        line += " (%s %s ago)" % ("read by a watcher" if budget["source"] == "watcher" else "read from gh api rate_limit",
                                   span(now - budget["at"]))
    return line + "."


def owner_name(record):
    """The name of the session holding this watcher (issue #133): from .apb/watcher-owner.json next to
    its state file, which hooks/apb-watch-start.py writes. None if there is none."""
    try:
        with open(os.path.join(os.path.dirname(record["state"]), "watcher-owner.json"), encoding="utf-8") as f:
            name = json.load(f).get("name")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return name if isinstance(name, str) and name else None


def listing(records, budget, now, mine=None):
    """Lines that list the running watchers, then the account's GraphQL budget. With mine (a test on a
    record), only the watchers it accepts are listed, and the others are only counted."""
    shown = [r for r in records if mine(r)] if mine else records
    n = len(shown)
    where = "for this project" if mine else "on this machine"
    lines = ["%d watcher%s %s:" % (n, "" if n == 1 else "s", where) if n else
             ("No watchers for this project." if mine else "No apb watchers running on this machine.")]
    for r in shown:
        issues = "".join(" #%d" % i for i in r.get("issues") or [])
        held = ', held by "%s"' % r["owner"] if r.get("owner") else ""
        lines.append("  %s%s: %s, running %s (pid %d)%s" % (r["repo"], issues, every(r["poll"]),
                                                            span(now - r["started"]), r["pid"], held))
    if mine:
        others = len(records) - n
        lines.append("%d other watcher%s on this machine." % (others, "" if others == 1 else "s"))
    lines.append(budget_line(budget, now))
    return lines


# ---------------------------------------------------------------- the loop

def parse_args(argv):
    a = argparse.ArgumentParser(description="Wait until something needs the orchestrator.")
    a.add_argument("--list", action="store_true",
                   help="list the watchers running on this machine and the account's GraphQL budget, then exit")
    a.add_argument("--state", help="file that keeps what was seen between runs (required, except with --list)")
    a.add_argument("--repo", help="owner/name; default: this folder's GitHub repo")
    a.add_argument("--issue", type=int, action="append", default=[], help="also watch this issue's comments")
    a.add_argument("--events", default=os.path.join(".apb", "events.jsonl"))
    a.add_argument("--check-in", type=float, default=0, help="minutes; exit with a progress summary (0: never)")
    a.add_argument("--limit", type=float, default=60, help="minutes; exit with 'no change' after this")
    a.add_argument("--stuck-minutes", type=float, default=10)
    a.add_argument("--quiet-minutes", type=float, default=15)
    a.add_argument("--poll", type=float, help="seconds between GitHub reads, fixed; default: every %d s while "
                   "the repo is active, every %d s once it has been quiet for an hour" % (ACTIVE, QUIET))
    a.add_argument("--root-pid", type=int, help="Claude Code's process; default: found from this script's parents")
    a.add_argument("--follow-file", help="the follow file to read at every check (default for --follow: %s)" % FOLLOW)
    a.add_argument("--follow", type=int, action="append", default=[],
                   help="add this card's issue to the follow file (with --check-in, its check-in), then exit")
    a.add_argument("--unfollow", type=int, action="append", default=[],
                   help="take this card's issue out of the follow file, then exit")
    opts = a.parse_args(argv)
    if not opts.list and not opts.state and not (opts.follow or opts.unfollow):
        a.error("the following arguments are required: --state")
    return opts


def load_state(path):
    try:
        with open(path) as f:
            state = json.load(f)
        if isinstance(state, dict):
            return state
    except (OSError, ValueError):
        pass
    return {}


def read_follow(path):
    """{"issues": [N, ...], "check_in": minutes} from the follow file; nothing followed if it is missing
    or unreadable."""
    try:
        with open(path) as f:
            data = json.load(f)
        issues = sorted({int(n) for n in data.get("issues") or []})
        return {"issues": issues, "check_in": float(data.get("check_in") or 0)}
    except (OSError, ValueError, TypeError, AttributeError):
        return {"issues": [], "check_in": 0}


def follow(path, add, remove, check_in):
    """Add and remove cards' issues in the follow file; returns a line saying what it follows now."""
    now = read_follow(path)
    issues = sorted((set(now["issues"]) | set(add)) - set(remove))
    check_in = check_in or now["check_in"]
    if not issues:
        try:
            os.remove(path)
        except OSError:
            pass
        return "Following no cards."
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    save_state(path, {"issues": issues, "check_in": check_in})
    return "Following %s%s." % (", ".join("#%d" % n for n in issues),
                                " (check-in every %g min while agents run)" % check_in if check_in else "")


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def low_line(remaining, reset):
    if remaining <= 0:
        return ("GitHub: rate-limited until %s: the account's GraphQL budget is used up. The watchers wait "
                "until then." % (clock(reset) if reset else "the hour's reset"))
    return ("GitHub: the account's GraphQL budget is low: %d points left, resets at %s. The watchers read "
            "GitHub every %d min until then." % (remaining, clock(reset), QUIET // 60))


def watch(opts, snapshot, ps_text, now, sleep, me, registry=None):
    """Watch until something needs the orchestrator; returns the lines to print. registry: the shared
    folder of running watchers (None: don't use one)."""
    state = load_state(opts.state)
    if state.get("repo") != opts.repo:
        state = {"repo": opts.repo}
    idle = state.setdefault("idle", {})
    reported = state.setdefault("quiet", {})
    start = now()
    failures, last_error = 0, ""
    head = lambda: "%s apb-watch (%s):" % (time.strftime("%H:%M:%S", time.localtime(now())), opts.repo)
    tick = min(TICK, opts.poll) if opts.poll else TICK
    next_read = max(start, state.get("blocked_until") or 0)
    record = {"repo": opts.repo, "issues": opts.issue, "poll": opts.poll or ACTIVE, "started": start,
              "state": os.path.abspath(opts.state)}
    if registry:
        register(registry, me, record)
    try:
        while True:
            found = []
            procs = {}
            try:
                procs = parse_ps(ps_text())
            except Exception as e:
                last_error = "ps: %s" % e
            followed = read_follow(opts.follow_file) if opts.follow_file else {"issues": [], "check_in": 0}
            if now() >= next_read:
                issues = sorted(set(opts.issue) | set(followed["issues"]))
                record["issues"] = issues
                lines, error = read_github(opts, state, snapshot, now(), registry, issues)
                found += lines
                if error is not None:  # GitHub down or slow: try again at the next read
                    failures, last_error = failures + 1, error
                live = live_watchers(registry, procs) if registry else []
                count = len(live) + (0 if any(r.get("pid") == me for r in live) else 1)
                budget = state.get("budget") or {}
                t = now()
                pace = github_interval(t, state.get("active_at") or start, count, state.get("cost") or FIRST_COST,
                                       budget.get("remaining"), budget.get("reset"), fixed=opts.poll)
                next_read = max(t + pace, state.get("blocked_until") or 0)
                if registry:  # at every read, so a file lost or removed by mistake comes back
                    record.update(poll=pace, checked=t)
                    if state.get("budget"):  # what --list and the progress page show as the budget
                        record["budget"] = state["budget"]
                    register(registry, me, record)
            root = opts.root_pid or find_root(procs, me)
            pids = watched(procs, root, me)
            for r in track_idle(idle, procs, pids, now(), opts.stuck_minutes * 60):
                found.append("Stuck: process %d has used no CPU for %s (running %s): %s. "
                             "If it should have finished, stop it: kill %d" % (
                                 r["pid"], minutes(r["idle"]), minutes(r["elapsed"]), r["command"][:200], r["pid"]))
            agents = running_agents(read_events(opts.events), now())
            found += quiet_agents(agents, now(), opts.quiet_minutes * 60, reported)
            save_state(opts.state, state)
            if found:
                return [head()] + found
            waited = now() - start
            note = [] if not failures else [
                "GitHub could not be read %d times; last error: %s" % (failures, last_error)]
            check_in = opts.check_in or (followed["check_in"] if agents else 0)
            if check_in and waited >= check_in * 60:
                lines = ["Check-in: %s, nothing new on GitHub." % minutes(waited)]
                if agents:
                    lines += ["Agent %s (%s): last event %s ago: %s" % (
                        v["type"], a, minutes(now() - v["last"]), v["what"]) for a, v in agents.items()]
                else:
                    lines.append("No agents running, as far as the events file shows.")
                if pids:
                    lines.append("Commands running: " + "; ".join(
                        "%d %s (no CPU for %s)" % (p, procs[p]["command"][:80], minutes(
                            now() - idle["%d %s" % (p, procs[p]["command"])]["since"])) for p in pids[:5]))
                return [head()] + lines + note
            if waited >= opts.limit * 60:
                return [head(), "No change in %s." % minutes(waited)] + note
            sleep(max(0.0, min(tick, next_read - now())))
    finally:
        if registry:
            unregister(registry, me)


def read_github(opts, state, snapshot, t, registry, issues=None):
    """One GitHub read of the PRs and issues (default: --issue): updates state, returns (lines for the
    orchestrator, error or None)."""
    found = []
    try:
        # A merged or closed PR stays watched while it has an unresolved Decision thread.
        new = snapshot(opts.repo, opts.issue if issues is None else issues, still_watched(state.get("github")),
                       cache=state.setdefault("details", {}))
    except RateLimited as e:
        reset = e.reset or t + QUIET
        state["blocked_until"] = reset
        state["budget"] = {"remaining": 0, "reset": reset, "at": t}
        if notice(state, registry, reset):
            found.append(low_line(0, e.reset))
        return found, None
    except Exception as e:
        return found, str(e)
    state.pop("blocked_until", None)
    meta = new.get("meta") or {}
    new = {k: v for k, v in new.items() if k != "meta"}
    old = state.get("github")
    if old is not None:
        # An issue not watched last time has no old count: its comments posted since the last read are new.
        since = state.get("read_at")
        fresh = {n: sum(1 for c in times if c > since) for n, times in (meta.get("issue_times") or {}).items()
                 if n not in old.get("issues", {})} if since is not None else {}
        found += ["GitHub: " + c for c in gh_changes(old, new, opts.repo, fresh)]
    state["github"] = new
    state["read_at"] = t
    # Activity: the newest change GitHub shows, or any change in what the watcher sees.
    times = [x for x in (state.get("active_at"), meta.get("newest")) if x is not None]
    if (old is not None and old != new) or not times:
        times.append(t)
    state["active_at"] = max(times)
    if meta.get("cost") is not None:
        state["cost"] = meta["cost"] if state.get("cost") is None else (state["cost"] + meta["cost"]) / 2.0
    if meta.get("remaining") is not None:
        state["budget"] = {"remaining": meta["remaining"], "limit": meta.get("limit"), "reset": meta.get("reset"),
                           "at": t}
        if meta["remaining"] < LOW and notice(state, registry, meta.get("reset")):
            found.append(low_line(meta["remaining"], meta.get("reset")))
    return found, None


def notice(state, registry, reset):
    """True once per budget hour: on this machine with a registry, else for this state file."""
    if registry:
        return claim_notice(registry, reset)
    if state.get("notice") == reset:
        return False
    state["notice"] = reset
    return True


def main(argv):
    opts = parse_args(argv)
    if opts.follow or opts.unfollow:
        print(follow(opts.follow_file or FOLLOW, opts.follow, opts.unfollow, opts.check_in), flush=True)
        return
    if opts.list:
        records = [dict(r, owner=owner_name(r)) for r in live_watchers(registry_dir(), parse_ps(read_ps()))]
        now = time.time()
        print("\n".join(listing(records, account_budget(records, now, rate_budget), now)), flush=True)
        return
    if not opts.repo:
        opts.repo = gh_repo()
    # Stopped (the hook stops its watchers this way): still remove this watcher's registry entry.
    signal.signal(signal.SIGTERM, lambda signum, frame: sys.exit(128 + signum))
    print("\n".join(watch(opts, snapshot=gh_snapshot, ps_text=read_ps, now=time.time, sleep=time.sleep,
                          me=os.getpid(), registry=registry_dir())), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
