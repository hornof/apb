"""Wait in the background, and exit as soon as something needs the orchestrator.

It exits, printing what it found, when:
- GitHub: a PR is opened, merged or closed, a PR's checks finish, a PR gets new comments, review
  comments, replies on review comments or reviews, all of a PR's review comments are resolved, or a
  watched issue gets new comments.
- Stuck: a command started from one of Claude Code's shells has used no CPU for --stuck-minutes.
- Quiet: an agent that is still running has written nothing to .apb/events.jsonl for --quiet-minutes.
- Check-in: --check-in minutes have passed (use it while agents run, to look at their progress).
- No change: --limit minutes have passed, so it never runs forever.

It saves what it saw in the --state file, so nothing that happens between two runs is missed and
nothing is reported twice. It only reads; it never stops a process or changes anything on GitHub.

Usage (from the project folder, in the background):
  python3 <apb plugin>/scripts/apb-watch.py --state <scratch folder>/watch.json [--repo owner/name]
      [--issue N ...] [--check-in 5] [--limit 60] [--stuck-minutes 10] [--quiet-minutes 15]
"""
import argparse
import calendar
import json
import os
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

def build_query(issues):
    asks = "".join("\n    i%d: issue(number: %d) { title state comments { totalCount } }" % (n, n)
                   for n in issues)
    return """query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) {
    open: pullRequests(first: 50, states: OPEN) { nodes { number title
      commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
      reviewThreads(first: 100) { nodes { isResolved comments { totalCount } } }
      comments { totalCount } reviews { totalCount } } }
    merged: pullRequests(first: 20, states: MERGED, orderBy: {field: UPDATED_AT, direction: DESC}) { nodes { number title } }
    closed: pullRequests(first: 20, states: CLOSED, orderBy: {field: UPDATED_AT, direction: DESC}) { nodes { number title } }""" \
        + asks + "\n} }"


def normalize(repo, issues):
    snap = {"open": {}, "merged": {}, "closed": {}, "issues": {}}
    numbers = [0]
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


def gh_snapshot(repo, issues):
    owner, name = repo.split("/", 1)
    out = subprocess.run(["gh", "api", "graphql", "-f", "owner=" + owner, "-f", "name=" + name,
                          "-f", "query=" + build_query(issues)], capture_output=True, text=True, timeout=60)
    if out.returncode != 0:
        raise RuntimeError((out.stderr or out.stdout).strip()[:200])
    return normalize(json.loads(out.stdout)["data"]["repository"], issues)


def gh_repo():
    out = subprocess.run(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
                         capture_output=True, text=True, timeout=60)
    if out.returncode != 0 or "/" not in out.stdout:
        sys.exit("apb-watch: no --repo given, and gh can't tell which repo this folder is: "
                 + (out.stderr or out.stdout).strip())
    return out.stdout.strip()


def count(n, word, plural=None):
    return "%d %s" % (n, word if n == 1 else plural or word + "s")


def gh_changes(old, new):
    out = []
    was_open = old["open"]
    for kind in ("merged", "closed"):
        for n, title in new[kind].items():
            # New to us: open at the last check, or a number we had never seen (opened and closed in between).
            if n in was_open or (int(n) > old.get("max", 0) and n not in old[kind]):
                out.append("PR #%s merged: %s" % (n, title) if kind == "merged"
                           else "PR #%s closed without merging: %s" % (n, title))
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
        if o is None:
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


# ---------------------------------------------------------------- the loop

def clock(t):
    return time.strftime("%H:%M", time.localtime(t))


def minutes(s):
    return "%d min" % (s // 60)


def parse_args(argv):
    a = argparse.ArgumentParser(description="Wait until something needs the orchestrator.")
    a.add_argument("--state", required=True, help="file that keeps what was seen between runs")
    a.add_argument("--repo", help="owner/name; default: this folder's GitHub repo")
    a.add_argument("--issue", type=int, action="append", default=[], help="also watch this issue's comments")
    a.add_argument("--events", default=os.path.join(".apb", "events.jsonl"))
    a.add_argument("--check-in", type=float, default=0, help="minutes; exit with a progress summary (0: never)")
    a.add_argument("--limit", type=float, default=60, help="minutes; exit with 'no change' after this")
    a.add_argument("--stuck-minutes", type=float, default=10)
    a.add_argument("--quiet-minutes", type=float, default=15)
    a.add_argument("--poll", type=float, default=20, help="seconds between checks")
    a.add_argument("--root-pid", type=int, help="Claude Code's process; default: found from this script's parents")
    return a.parse_args(argv)


def load_state(path):
    try:
        with open(path) as f:
            state = json.load(f)
        if isinstance(state, dict):
            return state
    except (OSError, ValueError):
        pass
    return {}


def save_state(path, state):
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, path)


def watch(opts, snapshot, ps_text, now, sleep, me):
    state = load_state(opts.state)
    if state.get("repo") != opts.repo:
        state = {"repo": opts.repo}
    idle = state.setdefault("idle", {})
    reported = state.setdefault("quiet", {})
    start = now()
    failures, last_error = 0, ""
    head = lambda: "%s apb-watch (%s):" % (time.strftime("%H:%M:%S", time.localtime(now())), opts.repo)
    while True:
        found = []
        try:
            new = snapshot(opts.repo, opts.issue)
            if state.get("github") is not None:
                # An issue not watched last time has no old count, so gh_changes leaves it out this once.
                found += ["GitHub: " + c for c in gh_changes(state["github"], new)]
            state["github"] = new
        except Exception as e:  # GitHub down or slow: try again next round
            failures, last_error = failures + 1, str(e)
        procs = {}
        try:
            procs = parse_ps(ps_text())
        except Exception as e:
            last_error = "ps: %s" % e
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
        if opts.check_in and waited >= opts.check_in * 60:
            lines = ["Check-in: %s, nothing new on GitHub." % minutes(waited)]
            if agents:
                lines += ["Agent %s (%s): last event %s ago: %s" % (v["type"], a, minutes(now() - v["last"]), v["what"])
                          for a, v in agents.items()]
            else:
                lines.append("No agents running, as far as the events file shows.")
            if pids:
                lines.append("Commands running: " + "; ".join(
                    "%d %s (no CPU for %s)" % (p, procs[p]["command"][:80], minutes(now() - idle["%d %s" % (p, procs[p]["command"])]["since"]))
                    for p in pids[:5]))
            return [head()] + lines + note
        if waited >= opts.limit * 60:
            return [head(), "No change in %s." % minutes(waited)] + note
        sleep(opts.poll)


def main(argv):
    opts = parse_args(argv)
    if not opts.repo:
        opts.repo = gh_repo()
    print("\n".join(watch(opts, snapshot=gh_snapshot, ps_text=read_ps, now=time.time, sleep=time.sleep,
                          me=os.getpid())), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
