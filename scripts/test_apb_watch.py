"""Tests for apb-watch.py. Run from the repo root: python3 -m unittest discover scripts

The pure parts (reading ps, picking the processes to watch, timing how long each has been idle,
comparing two GitHub snapshots, reading the events file) are checked with random inputs against
a second, simpler way of getting the same answer. The whole script is then run for real, with a
fake `gh` on the PATH and a real idle process, to check what it prints and when it exits.
"""
import ast
import glob
import importlib.util
import json
import os
import random
import signal
import stat
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SCRIPT = os.path.join(HERE, "apb-watch.py")

spec = importlib.util.spec_from_file_location("apb_watch", SCRIPT)
w = importlib.util.module_from_spec(spec)
spec.loader.exec_module(w)


# ---------------------------------------------------------------- reading ps

def mac_clock(seconds):
    """ps 'time' on macOS: M:SS.ss"""
    m, s = divmod(seconds, 60)
    return "%d:%05.2f" % (m, s)


def linux_clock(seconds):
    """ps 'time' on Linux: [DD-]HH:MM:SS"""
    seconds = int(seconds)
    d, rest = divmod(seconds, 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    return ("%d-" % d if d else "") + "%02d:%02d:%02d" % (h, m, s)


def etime(seconds):
    """ps 'etime' on both: [[DD-]HH:]MM:SS"""
    seconds = int(seconds)
    d, rest = divmod(seconds, 86400)
    h, rest = divmod(rest, 3600)
    m, s = divmod(rest, 60)
    if d:
        return "%02d-%02d:%02d:%02d" % (d, h, m, s)
    if h:
        return "%02d:%02d:%02d" % (h, m, s)
    return "%02d:%02d" % (m, s)


class ReadingPs(unittest.TestCase):
    def test_examples(self):
        self.assertEqual(w.parse_clock("1:39.50"), 99.5)
        self.assertEqual(w.parse_clock("0:00.01"), 0.01)
        self.assertEqual(w.parse_clock("44:37"), 44 * 60 + 37)
        self.assertEqual(w.parse_clock("09-02:09:50"), 9 * 86400 + 2 * 3600 + 9 * 60 + 50)
        self.assertEqual(w.parse_clock("00:00:03"), 3)

    def test_random_clocks_round_trip(self):
        rng = random.Random(1)
        for _ in range(2000):
            s = rng.choice([rng.uniform(0, 100), rng.uniform(0, 10 ** 4), rng.uniform(0, 10 ** 7)])
            self.assertAlmostEqual(w.parse_clock(mac_clock(round(s, 2))), round(s, 2), places=2)
            self.assertEqual(w.parse_clock(linux_clock(s)), int(s))
            self.assertEqual(w.parse_clock(etime(s)), int(s))

    def test_parse_ps_keeps_spaces_in_commands_and_skips_junk(self):
        text = ("    1     0 139:32.20 09-02:09:50 /sbin/launchd\n"
                "88840 88838   1:39.50       44:37 /Users/x/Application Support/claude --flag a  b\n"
                "garbage line\n"
                "   77     1   0:00.00       00:01\n"
                "\n"
                "95339 88840   0:00.01       00:00 /bin/zsh -c source snap.sh && eval 'npm test'\n")
        procs = w.parse_ps(text)
        self.assertEqual(sorted(procs), [1, 88840, 95339])
        self.assertEqual(procs[88840]["ppid"], 88838)
        self.assertEqual(procs[88840]["cpu"], 99.5)
        self.assertEqual(procs[88840]["elapsed"], 44 * 60 + 37)
        self.assertEqual(procs[88840]["command"], "/Users/x/Application Support/claude --flag a  b")
        self.assertEqual(procs[95339]["command"], "/bin/zsh -c source snap.sh && eval 'npm test'")

    def test_real_ps_finds_this_process(self):
        procs = w.parse_ps(w.read_ps())
        me = procs[os.getpid()]
        self.assertEqual(me["ppid"], os.getppid())
        self.assertGreaterEqual(me["cpu"], 0)
        self.assertIn("python", me["command"].lower())


# ---------------------------------------------------------------- which processes to watch

SHELL_CMDS = ["/bin/zsh -c eval 'npm test'", "bash -c x", "-zsh", "sh -c 'a; b'", "/bin/dash -c y"]
OTHER_CMDS = ["node test.mjs", "git hash-object --stdin", "/usr/bin/python3 -m pytest",
              "npx playwright test", "/Apps/My App/claude --x", "sleep 300", "/bin/sleep 20",
              "python3 /p/scripts/apb-watch.py --repo a/b", "gh pr checks 5 --watch", "<defunct>"]


def random_tree(rng, n):
    """A random process table. pid 1 is the top. Returns (procs, claude_pid, watcher_pid)."""
    procs = {1: {"ppid": 0, "cpu": 0.0, "elapsed": 10 ** 6, "command": "/sbin/launchd"}}
    claude = 50
    procs[claude] = {"ppid": 1, "cpu": 9.0, "elapsed": 3000, "command": "/Apps/x/claude --output-format"}
    pids = [1, claude]
    for pid in range(100, 100 + n):
        parent = rng.choice(pids + [claude] * 3)
        cmd = rng.choice(SHELL_CMDS if rng.random() < 0.4 else OTHER_CMDS)
        procs[pid] = {"ppid": parent, "cpu": 0.0, "elapsed": 10.0, "command": cmd}
        pids.append(pid)
    # the watcher: claude -> shell -> (shell) -> python apb-watch, possibly with its own ps child
    sh1 = 100 + n
    procs[sh1] = {"ppid": claude, "cpu": 0.0, "elapsed": 1, "command": "/bin/zsh -c python3 apb-watch.py"}
    parent = sh1
    if rng.random() < 0.5:
        procs[sh1 + 1] = {"ppid": sh1, "cpu": 0.0, "elapsed": 1, "command": "zsh"}
        parent = sh1 + 1
    me = sh1 + 2
    procs[me] = {"ppid": parent, "cpu": 0.1, "elapsed": 1, "command": "/usr/bin/python3 /x/apb-watch.py"}
    procs[me + 1] = {"ppid": me, "cpu": 0.0, "elapsed": 0, "command": "ps -A -o pid="}
    return procs, claude, me


def oracle_watched(procs, root, me):
    """The same answer as w.watched, worked out one process at a time by walking up to the top."""
    def chain(pid):
        out, seen = [], set()
        while pid in procs and pid not in seen:
            out.append(pid)
            seen.add(pid)
            pid = procs[pid]["ppid"]
        return out
    has_child = {p["ppid"] for p in procs.values()}
    result = set()
    for pid, p in procs.items():
        c = chain(pid)
        if root not in c[1:]:
            continue
        below_root = c[c.index(root) - 1]
        first = os.path.basename(procs[below_root]["command"].split()[0]).lstrip("-")
        if first not in ("sh", "bash", "zsh", "dash", "fish", "ksh"):
            continue
        if me in c or pid in chain(me):
            continue
        if pid in has_child:
            continue
        word = os.path.basename(p["command"].split()[0])
        if word == "sleep" or "apb-watch" in p["command"] or "<defunct>" in p["command"]:
            continue
        result.add(pid)
    return result


class WhichProcesses(unittest.TestCase):
    def test_root_is_the_first_ancestor_that_is_not_a_shell(self):
        rng = random.Random(2)
        for _ in range(200):
            procs, claude, me = random_tree(rng, rng.randint(0, 30))
            self.assertEqual(w.find_root(procs, me), claude)

    def test_random_trees_match_the_oracle(self):
        rng = random.Random(3)
        for _ in range(500):
            procs, claude, me = random_tree(rng, rng.randint(0, 40))
            self.assertEqual(set(w.watched(procs, claude, me)), oracle_watched(procs, claude, me))

    def test_the_stuck_git_call_from_the_trial_is_watched(self):
        procs = {
            50: {"ppid": 1, "cpu": 5, "elapsed": 9000, "command": "/x/claude"},
            60: {"ppid": 50, "cpu": 0, "elapsed": 4200, "command": "/bin/zsh -c npm test"},
            61: {"ppid": 60, "cpu": 0.4, "elapsed": 4190, "command": "npm test"},
            62: {"ppid": 61, "cpu": 1.2, "elapsed": 4180, "command": "node --test tests/"},
            63: {"ppid": 62, "cpu": 0.0, "elapsed": 4140, "command": "git hash-object --stdin"},
            70: {"ppid": 50, "cpu": 0.2, "elapsed": 9000, "command": "node mcp-server.js"},
            71: {"ppid": 70, "cpu": 0.0, "elapsed": 9000, "command": "idle-helper"},
            80: {"ppid": 50, "cpu": 0, "elapsed": 5, "command": "/bin/zsh -c sleep 300"},
            81: {"ppid": 80, "cpu": 0, "elapsed": 5, "command": "sleep 300"},
            90: {"ppid": 50, "cpu": 0, "elapsed": 5, "command": "/bin/zsh -c gh pr view"},
            91: {"ppid": 90, "cpu": 0, "elapsed": 5, "command": "<defunct>"},
            92: {"ppid": 90, "cpu": 0, "elapsed": 5, "command": "[git] <defunct>"},
        }
        self.assertEqual(w.watched(procs, 50, 999), [63])

    def test_no_root_watches_nothing(self):
        procs, claude, me = random_tree(random.Random(4), 10)
        self.assertEqual(w.watched(procs, None, me), [])

    def test_a_loop_in_the_table_does_not_hang(self):
        procs = {5: {"ppid": 6, "cpu": 0, "elapsed": 1, "command": "zsh"},
                 6: {"ppid": 5, "cpu": 0, "elapsed": 1, "command": "zsh"},
                 7: {"ppid": 5, "cpu": 0, "elapsed": 1, "command": "python3 apb-watch.py"}}
        self.assertIsNone(w.find_root(procs, 7))
        self.assertEqual(w.watched(procs, 5, 7), [])


# ---------------------------------------------------------------- how long each has been idle

def oracle_reports(trace, stuck):
    """trace: list of (t, cpu, elapsed) for one process. The times a report is due, worked out from the whole trace."""
    out = []
    since, last_cpu, reported = None, None, False
    for t, cpu, elapsed in trace:
        if since is None:
            since = t - elapsed if cpu < w.NEVER_RAN else t
        elif cpu != last_cpu:
            since, reported = t, False
        last_cpu = cpu
        if not reported and t - since >= stuck:
            out.append(t)
            reported = True
    return out


class IdleTime(unittest.TestCase):
    def test_random_traces_match_the_oracle_even_across_restarts(self):
        rng = random.Random(5)
        for _ in range(300):
            stuck = rng.choice([30, 60, 600])
            times, t = [], 1000.0
            for _ in range(rng.randint(1, 60)):
                t += rng.choice([1, 5, 20, 20, 20, 120])
                times.append(t)
            traces = {}
            for i in range(rng.randint(1, 4)):
                cpu, elapsed = rng.choice([0.0, 0.3, 5.0]), rng.choice([0, 10, 700])
                trace = []
                for t in times:
                    if rng.random() < 0.15:
                        cpu += rng.choice([0.01, 1.0])
                    trace.append((t, cpu, elapsed + t - 1000))
                traces[200 + i] = trace
            got = {pid: [] for pid in traces}
            idle = {}
            for k, t in enumerate(times):
                procs = {pid: {"ppid": 1, "cpu": tr[k][1], "elapsed": tr[k][2], "command": "cmd %d" % pid}
                         for pid, tr in traces.items()}
                for r in w.track_idle(idle, procs, sorted(procs), t, stuck):
                    got[r["pid"]].append(t)
                if rng.random() < 0.2:  # the watcher restarts: the state goes through the file
                    idle = json.loads(json.dumps(idle))
            for pid, tr in traces.items():
                self.assertEqual(got[pid], oracle_reports(tr, stuck), (pid, stuck, tr))

    def test_a_process_that_never_ran_counts_from_its_start(self):
        idle = {}
        procs = {9: {"ppid": 1, "cpu": 0.0, "elapsed": 4140, "command": "git hash-object --stdin"}}
        [r] = w.track_idle(idle, procs, [9], 5000.0, 600)
        self.assertEqual(r["pid"], 9)
        self.assertEqual(int(r["idle"]), 4140)

    def test_a_process_that_did_work_counts_from_when_it_was_first_seen(self):
        idle = {}
        procs = {9: {"ppid": 1, "cpu": 30.0, "elapsed": 4140, "command": "node test"}}
        self.assertEqual(w.track_idle(idle, procs, [9], 5000.0, 600), [])
        self.assertEqual(w.track_idle(idle, procs, [9], 5599.0, 600), [])
        self.assertEqual(len(w.track_idle(idle, procs, [9], 5600.0, 600)), 1)
        self.assertEqual(w.track_idle(idle, procs, [9], 9000.0, 600), [])  # once only

    def test_a_reused_pid_is_a_new_process(self):
        idle = {}
        a = {9: {"ppid": 1, "cpu": 0.0, "elapsed": 0, "command": "first"}}
        b = {9: {"ppid": 1, "cpu": 0.0, "elapsed": 0, "command": "second"}}
        w.track_idle(idle, a, [9], 0.0, 60)
        self.assertEqual(len(w.track_idle(idle, a, [9], 60.0, 60)), 1)
        self.assertEqual(w.track_idle(idle, b, [9], 61.0, 60), [])
        self.assertEqual(len(w.track_idle(idle, b, [9], 121.0, 60)), 1)

    def test_gone_processes_are_forgotten(self):
        idle = {}
        w.track_idle(idle, {9: {"ppid": 1, "cpu": 0, "elapsed": 0, "command": "x"}}, [9], 0.0, 60)
        w.track_idle(idle, {}, [], 1.0, 60)
        self.assertEqual(idle, {})


# ---------------------------------------------------------------- GitHub changes

def random_snapshot(rng):
    snap = {"open": {}, "merged": {}, "closed": {}, "issues": {}}
    numbers = rng.sample(range(1, 60), rng.randint(0, 12))
    for n in numbers:
        kind = rng.choice(["open", "open", "merged", "closed"])
        if kind == "open":
            threads = rng.randint(0, 4)
            snap["open"][str(n)] = {"title": "pr %d" % n, "checks": rng.choice(["NONE", "PENDING", "SUCCESS", "FAILURE"]),
                                    "threads": threads, "open_threads": rng.randint(0, threads),
                                    "comments": rng.randint(0, 5), "replies": rng.randint(0, 5),
                                    "reviews": rng.randint(0, 3)}
        else:
            snap[kind][str(n)] = "pr %d" % n
    for n in rng.sample(range(100, 120), rng.randint(0, 3)):
        snap["issues"][str(n)] = {"title": "issue %d" % n, "comments": rng.randint(0, 9), "state": "OPEN"}
    snap["max"] = max([0] + numbers)
    return snap


def mutate(rng, old):
    """One random change to a snapshot, and the word its report must contain."""
    new = json.loads(json.dumps(old))
    choices = ["opened"]
    if old["open"]:
        choices += ["merged", "closed", "checks", "threads", "comments", "replies", "reviews"]
        if any(p["open_threads"] for p in old["open"].values()):
            choices.append("resolved")
    if old["issues"]:
        choices += ["issue"]
    kind = rng.choice(choices)
    if kind == "opened":
        n = str(new["max"] + 1)
        new["open"][n] = {"title": "new", "checks": "PENDING", "threads": 0, "open_threads": 0, "comments": 0}
        new["max"] += 1
        return new, kind, n
    if kind == "issue":
        n = rng.choice(sorted(new["issues"]))
        new["issues"][n]["comments"] += rng.randint(1, 3)
        return new, "Issue #%s" % n, n
    n = rng.choice(sorted(p for p in new["open"] if kind != "resolved" or new["open"][p]["open_threads"]))
    p = new["open"][n]
    if kind in ("merged", "closed"):
        del new["open"][n]
        new[kind][n] = p["title"]
    elif kind == "checks":
        p["checks"] = "FAILURE" if p["checks"] == "SUCCESS" else "SUCCESS"
    elif kind == "threads":
        p["threads"] += 1
        p["open_threads"] += 1
    elif kind == "comments":
        p["comments"] += 2
    elif kind == "replies":
        p["replies"] += 1
    elif kind == "reviews":
        p["reviews"] += 1
    elif kind == "resolved":
        p["open_threads"] = 0
    return new, kind, n


class GitHubChanges(unittest.TestCase):
    def test_no_change_reports_nothing(self):
        rng = random.Random(6)
        for _ in range(500):
            s = random_snapshot(rng)
            self.assertEqual(w.gh_changes(s, json.loads(json.dumps(s))), [])

    def test_each_single_change_is_reported_once_and_names_its_number(self):
        rng = random.Random(7)
        words = {"opened": "opened", "merged": "merged", "closed": "closed without merging",
                 "checks": "checks finished", "threads": "new review comment", "comments": "new comment",
                 "replies": "new reply on a review comment", "reviews": "new review",
                 "resolved": "all review comments resolved"}
        for _ in range(2000):
            old = random_snapshot(rng)
            new, kind, n = mutate(rng, old)
            got = w.gh_changes(old, new)
            self.assertEqual(len(got), 1, (kind, got))
            self.assertIn("#" + n, got[0])
            self.assertIn(words.get(kind, kind), got[0])

    def test_an_old_merged_pr_coming_back_into_the_list_is_not_news(self):
        old = {"open": {}, "merged": {}, "closed": {}, "issues": {}, "max": 40}
        new = {"open": {}, "merged": {"12": "old one"}, "closed": {"13": "older"}, "issues": {}, "max": 40}
        self.assertEqual(w.gh_changes(old, new), [])

    def test_a_pr_opened_and_merged_between_two_checks_is_reported(self):
        old = {"open": {}, "merged": {}, "closed": {}, "issues": {}, "max": 40}
        new = {"open": {}, "merged": {"41": "quick one"}, "closed": {}, "issues": {}, "max": 41}
        [line] = w.gh_changes(old, new)
        self.assertIn("#41", line)
        self.assertIn("merged", line)

    def test_counts_read_as_english(self):
        old = {"open": {"5": {"title": "A", "checks": "NONE", "threads": 0, "open_threads": 0, "comments": 0}},
               "merged": {}, "closed": {}, "issues": {"25": {"title": "C", "state": "OPEN", "comments": 3}}, "max": 5}
        new = json.loads(json.dumps(old))
        new["open"]["5"].update(threads=1, open_threads=1, comments=2)
        new["issues"]["25"]["comments"] = 4
        self.assertEqual(w.gh_changes(old, new), ["PR #5: 1 new review comment", "PR #5: 2 new comments",
                                                  "Issue #25: 1 new comment (C)"])

    def test_a_reply_on_a_review_thread_is_news(self):
        old = {"open": {"5": {"title": "A", "checks": "NONE", "threads": 2, "open_threads": 1, "comments": 0,
                              "replies": 1, "reviews": 1}},
               "merged": {}, "closed": {}, "issues": {}, "max": 5}
        new = json.loads(json.dumps(old))
        new["open"]["5"]["replies"] = 2
        self.assertEqual(w.gh_changes(old, new), ["PR #5: 1 new reply on a review comment"])
        new["open"]["5"]["replies"] = 3
        self.assertEqual(w.gh_changes(old, new), ["PR #5: 2 new replies on a review comment"])

    def test_a_submitted_review_is_news(self):
        old = {"open": {"5": {"title": "A", "checks": "NONE", "threads": 0, "open_threads": 0, "comments": 0,
                              "replies": 0, "reviews": 0}},
               "merged": {}, "closed": {}, "issues": {}, "max": 5}
        new = json.loads(json.dumps(old))
        new["open"]["5"]["reviews"] = 1
        self.assertEqual(w.gh_changes(old, new), ["PR #5: 1 new review"])

    def test_a_state_saved_before_replies_were_counted_still_works(self):
        old = {"open": {"5": {"title": "A", "checks": "NONE", "threads": 1, "open_threads": 1, "comments": 0}},
               "merged": {}, "closed": {}, "issues": {}, "max": 5}
        new = json.loads(json.dumps(old))
        new["open"]["5"].update(replies=3, reviews=2)
        self.assertEqual(w.gh_changes(old, new), [])

    def test_deleted_comments_are_not_news(self):
        old = {"open": {"5": {"title": "A", "checks": "NONE", "threads": 3, "open_threads": 2, "comments": 4}},
               "merged": {}, "closed": {}, "issues": {"25": {"title": "C", "state": "OPEN", "comments": 3}}, "max": 5}
        new = json.loads(json.dumps(old))
        new["open"]["5"].update(threads=2, open_threads=1, comments=3)
        new["issues"]["25"]["comments"] = 2
        self.assertEqual(w.gh_changes(old, new), [])

    def test_checks_still_running_are_not_news(self):
        old = random_snapshot(random.Random(8))
        old["open"]["70"] = {"title": "t", "checks": "SUCCESS", "threads": 0, "open_threads": 0, "comments": 0}
        new = json.loads(json.dumps(old))
        new["open"]["70"]["checks"] = "PENDING"
        self.assertEqual(w.gh_changes(old, new), [])

    def test_normalize_reads_the_graphql_answer(self):
        data = {"open": {"nodes": [
            {"number": 5, "title": "A", "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": "SUCCESS"}}}]},
             "reviewThreads": {"nodes": [{"isResolved": True, "comments": {"totalCount": 3}},
                                         {"isResolved": False, "comments": {"totalCount": 1}},
                                         {"isResolved": False, "comments": {"totalCount": 2}}]},
             "comments": {"totalCount": 3}, "reviews": {"totalCount": 4}},
            {"number": 6, "title": "B", "commits": {"nodes": [{"commit": {"statusCheckRollup": None}}]},
             "reviewThreads": {"nodes": []}, "comments": {"totalCount": 0}, "reviews": {"totalCount": 0}},
            {"number": 7, "title": "C", "commits": {"nodes": []}, "reviewThreads": {"nodes": []},
             "comments": {"totalCount": 0}, "reviews": {"totalCount": 0}}]},
            "merged": {"nodes": [{"number": 4, "title": "M"}]},
            "closed": {"nodes": [{"number": 3, "title": "X"}]},
            "i25": {"title": "Coordination", "state": "OPEN", "comments": {"totalCount": 12}}}
        snap = w.normalize(data, [25])
        self.assertEqual(snap["open"]["5"], {"title": "A", "checks": "SUCCESS", "threads": 3, "open_threads": 2, "comments": 3,
                                            "replies": 3, "reviews": 4})
        self.assertEqual(snap["open"]["6"]["checks"], "NONE")
        self.assertEqual(snap["open"]["7"]["checks"], "NONE")
        self.assertEqual(snap["merged"], {"4": "M"})
        self.assertEqual(snap["closed"], {"3": "X"})
        self.assertEqual(snap["issues"], {"25": {"title": "Coordination", "state": "OPEN", "comments": 12}})
        self.assertEqual(snap["max"], 7)

    def test_the_query_asks_for_each_issue(self):
        q = w.build_query([25, 7])
        self.assertIn("i25: issue(number: 25)", q)
        self.assertIn("i7: issue(number: 7)", q)


# ---------------------------------------------------------------- the events file

def ev(t, event, agent=None, kind=None, **extra):
    line = {"time": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime(t)), "event": event, "session": "s"}
    if agent:
        line["agent_id"] = agent
        line["agent_type"] = kind or "apb:builder"
    line.update(extra)
    return line


class EventsFile(unittest.TestCase):
    def test_running_agents_follow_starts_and_stops(self):
        events = [ev(1000, "SubagentStart", "a1"), ev(1010, "SubagentStart", "a2", "apb:reviewer"),
                  ev(1020, "PostToolUse", "a1", command="npm test"), ev(1030, "SubagentStop", "a2"),
                  ev(1040, "Stop")]
        agents = w.running_agents(events, now=1100)
        self.assertEqual(list(agents), ["a1"])
        self.assertEqual(agents["a1"]["type"], "apb:builder")
        self.assertEqual(agents["a1"]["last"], 1020)
        self.assertIn("npm test", agents["a1"]["what"])

    def test_random_event_streams_match_a_replay(self):
        rng = random.Random(9)
        for _ in range(300):
            events, live, t = [], {}, 10 ** 6
            for _ in range(rng.randint(0, 40)):
                t += rng.randint(0, 100)
                a = "a%d" % rng.randint(0, 4)
                kind = rng.choice(["SubagentStart", "SubagentStop", "PostToolUse", "TaskCompleted", "Stop"])
                if kind == "Stop":
                    events.append(ev(t, kind))
                    continue
                events.append(ev(t, kind, a, command="c%d" % t))
                if kind == "SubagentStop":
                    live.pop(a, None)
                else:
                    live[a] = t
            got = w.running_agents(events, now=t)
            self.assertEqual({k: v["last"] for k, v in got.items()}, live)

    def test_agents_silent_for_hours_are_dropped(self):
        events = [ev(1000, "SubagentStart", "a1")]
        self.assertEqual(w.running_agents(events, now=1000 + w.ABANDONED + 1), {})

    def test_quiet_agents_are_reported_once_per_silence(self):
        reported = {}
        agents = {"a1": {"type": "apb:builder", "last": 1000, "what": "PostToolUse: npm test"}}
        self.assertEqual(w.quiet_agents(agents, 1000 + 899, 900, reported), [])
        [line] = w.quiet_agents(agents, 1000 + 900, 900, reported)
        self.assertIn("apb:builder", line)
        self.assertIn("15 min", line)
        self.assertEqual(w.quiet_agents(agents, 5000, 900, reported), [])
        agents["a1"]["last"] = 4000
        self.assertEqual(w.quiet_agents(agents, 4100, 900, reported), [])
        self.assertEqual(len(w.quiet_agents(agents, 4900, 900, reported)), 1)

    def test_read_events_skips_bad_lines_and_a_missing_file(self):
        d = tempfile.mkdtemp()
        path = os.path.join(d, "events.jsonl")
        self.assertEqual(w.read_events(path), [])
        with open(path, "w") as f:
            f.write(json.dumps(ev(1000, "SubagentStart", "a1")) + "\nnot json\n[1]\n"
                    + json.dumps({"event": "x"}) + "\n" + json.dumps(ev(1001, "Stop"))[:-3])
        got = w.read_events(path)
        self.assertEqual([g["event"] for g in got], ["SubagentStart"])
        self.assertEqual(got[0]["t"], 1000)


# ---------------------------------------------------------------- the whole loop, with fakes

class FakeClock:
    def __init__(self):
        self.t = 1_000_000.0

    def now(self):
        return self.t

    def sleep(self, s):
        self.t += s


class Loop(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.state = os.path.join(self.dir, "state.json")
        self.clock = FakeClock()
        self.snaps = []
        self.ps = ""

    def snapshot(self, repo, issues):
        s = self.snaps.pop(0) if len(self.snaps) > 1 else self.snaps[0]
        if isinstance(s, Exception):
            raise s
        return s

    def run_watch(self, *extra):
        opts = w.parse_args(["--repo", "o/n", "--state", self.state, "--events",
                             os.path.join(self.dir, "none.jsonl")] + list(extra))
        return w.watch(opts, snapshot=self.snapshot, ps_text=lambda: self.ps,
                       now=self.clock.now, sleep=self.clock.sleep, me=999)

    def test_first_run_takes_a_baseline_then_reports_a_merge(self):
        base = {"open": {"5": {"title": "A", "checks": "SUCCESS", "threads": 0, "open_threads": 0, "comments": 0}},
                "merged": {}, "closed": {}, "issues": {}, "max": 5}
        merged = {"open": {}, "merged": {"5": "A"}, "closed": {}, "issues": {}, "max": 5}
        self.snaps = [base, base, base, merged]
        out = self.run_watch()
        self.assertIn("apb-watch", out[0])
        self.assertEqual(out[1:], ["GitHub: PR #5 merged: A"])
        self.assertEqual(self.clock.t, 1_000_000.0 + 3 * 20)

    def test_a_change_while_stopped_is_reported_on_the_next_start(self):
        base = {"open": {"5": {"title": "A", "checks": "PENDING", "threads": 0, "open_threads": 0, "comments": 0}},
                "merged": {}, "closed": {}, "issues": {}, "max": 5}
        self.snaps = [base]
        self.run_watch("--limit", "1")
        done = json.loads(json.dumps(base))
        done["open"]["5"]["checks"] = "FAILURE"
        self.snaps = [done]
        out = self.run_watch()
        self.assertEqual(out[1:], ["GitHub: PR #5 checks finished: FAILURE"])
        self.assertEqual(self.clock.t, 1_000_000.0 + 60)  # no sleep before the first check

    def test_watching_a_new_issue_keeps_the_pr_changes_made_meanwhile(self):
        base = {"open": {"5": {"title": "A", "checks": "SUCCESS", "threads": 0, "open_threads": 0, "comments": 0}},
                "merged": {}, "closed": {}, "issues": {}, "max": 5}
        self.snaps = [base]
        self.run_watch("--limit", "1")
        merged = {"open": {}, "merged": {"5": "A"}, "closed": {},
                  "issues": {"25": {"title": "C", "state": "OPEN", "comments": 7}}, "max": 5}
        more = json.loads(json.dumps(merged))
        more["issues"]["25"]["comments"] = 8
        self.snaps = [merged, more]
        out = self.run_watch("--issue", "25")
        self.assertEqual(out[1:], ["GitHub: PR #5 merged: A"])  # the issue's 7 old comments are not news
        out = self.run_watch("--issue", "25")
        self.assertEqual(out[1:], ["GitHub: Issue #25: 1 new comment (C)"])

    def test_nothing_happens_until_the_limit(self):
        self.snaps = [{"open": {}, "merged": {}, "closed": {}, "issues": {}, "max": 0}]
        out = self.run_watch("--limit", "5")
        self.assertTrue(out[1].startswith("No change in 5 min"), out)
        self.assertGreaterEqual(self.clock.t - 1_000_000.0, 300)
        self.assertLess(self.clock.t - 1_000_000.0, 320)

    def test_check_in_comes_first_when_it_is_shorter(self):
        self.snaps = [{"open": {}, "merged": {}, "closed": {}, "issues": {}, "max": 0}]
        out = self.run_watch("--check-in", "5")
        self.assertTrue(out[1].startswith("Check-in: 5 min"), out)
        self.assertIn("No agents running", "\n".join(out))
        self.assertEqual(self.clock.t - 1_000_000.0, 300)

    def test_github_failures_do_not_stop_the_watch(self):
        self.snaps = [RuntimeError("HTTP 502")]
        out = self.run_watch("--limit", "1")
        self.assertTrue(out[1].startswith("No change in 1 min"), out)
        self.assertIn("HTTP 502", "\n".join(out))

    def test_a_stuck_process_wakes_the_orchestrator_once(self):
        self.snaps = [{"open": {}, "merged": {}, "closed": {}, "issues": {}, "max": 0}]
        self.ps = ("  50     1   9:00.00   2:00:00 /x/claude\n"
                   "  60    50   0:00.00     30:00 /bin/zsh -c npm test\n"
                   "  61    60   0:00.00     30:00 git hash-object --stdin\n")
        out = self.run_watch("--stuck-minutes", "10", "--root-pid", "50")
        self.assertEqual(len(out), 2, out)
        self.assertTrue(out[1].startswith("Stuck: process 61"), out)
        self.assertIn("kill 61", out[1])
        again = self.run_watch("--stuck-minutes", "10", "--root-pid", "50", "--limit", "1")
        self.assertTrue(again[1].startswith("No change"), again)

    def test_a_quiet_agent_wakes_the_orchestrator(self):
        self.snaps = [{"open": {}, "merged": {}, "closed": {}, "issues": {}, "max": 0}]
        events = os.path.join(self.dir, "events.jsonl")
        with open(events, "w") as f:
            f.write(json.dumps(ev(self.clock.t - 600, "SubagentStart", "a1", "apb:reviewer")) + "\n")
        opts = w.parse_args(["--repo", "o/n", "--state", self.state, "--events", events, "--quiet-minutes", "15"])
        out = w.watch(opts, snapshot=self.snapshot, ps_text=lambda: "", now=self.clock.now,
                      sleep=self.clock.sleep, me=999)
        self.assertTrue(out[1].startswith("Quiet: apb:reviewer"), out)
        self.assertGreaterEqual(self.clock.t, 1_000_000.0 + 300)


# ---------------------------------------------------------------- the real script

FAKE_GH = '''import json, os, sys
d = os.environ["FAKE_GH_DIR"]
if sys.argv[1:3] == ["repo", "view"]:
    print("o/n"); sys.exit(0)
with open(os.path.join(d, "args"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
answers = json.load(open(os.path.join(d, "answers.json")))
count = os.path.join(d, "count")
i = int(open(count).read()) if os.path.exists(count) else 0
open(count, "w").write(str(i + 1))
a = answers[min(i, len(answers) - 1)]
if a == "fail":
    print("HTTP 502", file=sys.stderr); sys.exit(1)
print(json.dumps({"data": {"repository": a}}))
'''


def gh_answer(open_prs=(), merged=()):
    return {"open": {"nodes": [{"number": n, "title": "pr %d" % n, "commits": {"nodes": []},
                                "reviewThreads": {"nodes": []}, "comments": {"totalCount": 0},
                                "reviews": {"totalCount": 0}} for n in open_prs]},
            "merged": {"nodes": [{"number": n, "title": "pr %d" % n} for n in merged]},
            "closed": {"nodes": []}}


class RealScript(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        bindir = os.path.join(self.dir, "bin")
        os.makedirs(bindir)
        gh = os.path.join(bindir, "gh")
        with open(gh, "w") as f:
            f.write("#!" + sys.executable + "\n" + FAKE_GH)
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        self.env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], FAKE_GH_DIR=self.dir)
        self.state = os.path.join(self.dir, "state.json")

    def answers(self, items):
        with open(os.path.join(self.dir, "answers.json"), "w") as f:
            json.dump(items, f)

    def run_script(self, *extra, timeout=30):
        args = [sys.executable, SCRIPT, "--state", self.state, "--poll", "0.05",
                "--events", os.path.join(self.dir, "none.jsonl")] + list(extra)
        r = subprocess.run(args, capture_output=True, text=True, env=self.env, cwd=self.dir, timeout=timeout)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        return r.stdout.splitlines()

    def test_a_merge_ends_the_watch(self):
        self.answers([gh_answer(open_prs=[7]), gh_answer(open_prs=[7]), gh_answer(merged=[7])])
        out = self.run_script("--repo", "o/n", "--limit", "1")
        self.assertIn("o/n", out[0])
        self.assertEqual(out[1:], ["GitHub: PR #7 merged: pr 7"])

    def test_the_repo_comes_from_gh_when_not_given_and_issues_are_asked_for(self):
        self.answers([gh_answer()])
        out = self.run_script("--limit", "0.005", "--issue", "25")
        self.assertIn("o/n", out[0])
        with open(os.path.join(self.dir, "args")) as f:
            first = json.loads(f.readline())
        self.assertIn("owner=o", first)
        self.assertIn("name=n", first)
        self.assertTrue(any("i25: issue(number: 25)" in a for a in first))

    def test_gh_failing_still_ends_at_the_limit(self):
        self.answers(["fail"])
        out = self.run_script("--repo", "o/n", "--limit", "0.005")
        self.assertTrue(out[1].startswith("No change in"), out)
        self.assertIn("HTTP 502", "\n".join(out))

    def test_a_real_idle_process_is_reported(self):
        self.answers([gh_answer()])
        # This test process plays Claude Code: it starts a shell, which starts a process that does nothing.
        # Its own process group, so the cleanup stops the sleeper too, not just the shell.
        idle = subprocess.Popen(["sh", "-c", sys.executable + " -c 'import time; time.sleep(60)'; true"],
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                start_new_session=True)
        try:
            time.sleep(1.5)
            out = self.run_script("--repo", "o/n", "--limit", "0.5", "--stuck-minutes", "0.02",
                                  "--root-pid", str(os.getpid()))
        finally:
            os.killpg(idle.pid, signal.SIGKILL)
            idle.wait(timeout=10)
        stuck = [line for line in out if line.startswith("Stuck:")]
        self.assertEqual(len(stuck), 1, out)
        self.assertIn("time.sleep(60)", stuck[0])

    def test_bad_arguments_fail_loudly(self):
        r = subprocess.run([sys.executable, SCRIPT, "--repo", "o/n"], capture_output=True, text=True,
                           env=self.env, timeout=30)
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("--state", r.stderr)


# ---------------------------------------------------------------- every child process has a time limit

class TimeLimits(unittest.TestCase):
    """apb's own scripts follow the rule apb sets: every child process they start has a time limit."""

    # The only processes allowed to outlive the script that starts them, by (file, function).
    # Each one must end itself at a deadline it is given, and have a test that checks it does.
    DETACHED = {
        ("hooks/apb-stop.py", "start_reminder"):
            "the reminder outlives the Stop hook; it ends at the deadline it is given "
            "(hooks/test_apb_stop.py, ReminderLifetime)",
    }

    def calls(self):
        """(file, enclosing function, call name, call node) for every call in apb's own scripts."""
        files = [f for f in glob.glob(os.path.join(REPO, "*", "*.py")) if not os.path.basename(f).startswith("test_")]
        self.assertIn(SCRIPT, files)
        for path in files:
            with open(path, encoding="utf-8") as f:
                tree = ast.parse(f.read())
            rel = os.path.relpath(path, REPO).replace(os.sep, "/")
            for func, node in self.calls_by_function(tree):
                yield rel, func, ast.unparse(node.func), node

    @staticmethod
    def calls_by_function(tree):
        """(name of the innermost function holding the call, or None, call node) for every call."""
        def walk(node, func):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    yield from walk(child, child.name)
                else:
                    if isinstance(child, ast.Call):
                        yield func, child
                    yield from walk(child, func)
        return list(walk(tree, None))

    def test_every_subprocess_call_has_a_timeout(self):
        for rel, func, name, node in self.calls():
            where = "%s:%d" % (rel, node.lineno)
            if name == "subprocess.Popen" and (rel, func) in self.DETACHED:
                continue
            if name in ("os.system", "os.popen", "subprocess.Popen", "subprocess.call",
                        "subprocess.check_call", "subprocess.check_output", "subprocess.getoutput"):
                self.fail("%s uses %s; use subprocess.run with a timeout" % (where, name))
            if name == "subprocess.run":
                self.assertIn("timeout", [k.arg for k in node.keywords], where)

    def test_each_detached_process_is_still_started_where_the_list_says(self):
        found = {(rel, func) for rel, func, name, _ in self.calls() if name == "subprocess.Popen"}
        for place in self.DETACHED:
            self.assertIn(place, found, "stale entry in DETACHED: %s, %s" % place)

    def test_each_call_is_credited_to_its_innermost_function(self):
        tree = ast.parse("import subprocess\n"
                         "subprocess.Popen(['top'])\n"
                         "def start_reminder():\n"
                         "    def helper():\n"
                         "        subprocess.Popen(['inner'])\n"
                         "    subprocess.Popen(['outer'])\n"
                         "def other():\n"
                         "    subprocess.Popen(['other'])\n")
        got = {(func, ast.literal_eval(node.args[0])[0])
               for func, node in self.calls_by_function(tree) if ast.unparse(node.func) == "subprocess.Popen"}
        self.assertEqual(got, {(None, "top"), ("helper", "inner"), ("start_reminder", "outer"), ("other", "other")})


if __name__ == "__main__":
    unittest.main()
