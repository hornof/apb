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
        q = w.check_query([25, 7], [])
        self.assertIn("i25: issue(number: 25)", q)
        self.assertIn("i7: issue(number: 7)", q)


def thread_nodes(threads):
    """A PR's reviewThreads answer. threads: {first comment's id: resolved}."""
    return {"nodes": [{"isResolved": r, "comments": {"totalCount": 2}, "opening": {"nodes": [{"databaseId": i}]}}
                      for i, r in threads.items()]}


def snap_with(threads, open_prs=(), merged=()):
    """A snapshot holding the PRs' review threads: threads {PR: {first comment's id: resolved}}."""
    snap = {"open": {}, "merged": {}, "closed": {}, "issues": {}, "max": max([0] + list(open_prs) + list(merged))}
    for n in open_prs:
        t = threads.get(n, {})
        snap["open"][str(n)] = {"title": "pr %d" % n, "checks": "NONE", "threads": len(t),
                                "open_threads": sum(1 for r in t.values() if not r), "comments": 0,
                                "replies": 0, "reviews": 0}
    for n in merged:
        snap["merged"][str(n)] = "pr %d" % n
    snap["threads"] = {str(n): {str(i): r for i, r in t.items()} for n, t in threads.items() if t}
    return snap


class DecisionThreads(unittest.TestCase):
    """Each Decision thread that becomes resolved is reported, on an open PR or a merged or closed one,
    and a merged or closed PR stays watched while it has an unresolved thread."""

    def test_normalize_records_each_prs_threads(self):
        data = gh_answer(open_prs=[5], merged=[4])
        data["open"]["nodes"][0]["reviewThreads"] = thread_nodes({101: True, 102: False})
        data["merged"]["nodes"][0]["reviewThreads"] = thread_nodes({90: False})
        data["closed"]["nodes"] = [{"number": 3, "title": "X", "reviewThreads": thread_nodes({})}]
        data["w2"] = {"number": 2, "reviewThreads": thread_nodes({80: True})}
        data["w1"] = None  # a PR GitHub no longer has
        snap = w.normalize(data, [])
        self.assertEqual(snap["threads"], {"5": {"101": True, "102": False}, "4": {"90": False}, "2": {"80": True}})
        self.assertEqual(snap["open"]["5"]["open_threads"], 1)
        self.assertEqual(snap["merged"], {"4": "pr 4"})  # a watched PR isn't news as a merge
        self.assertEqual(snap["max"], 5)

    def test_the_query_asks_for_each_watched_pr_and_each_threads_first_comment(self):
        q = w.check_query([25], [82, 7])
        self.assertIn("w82: pullRequest(number: 82)", q)
        self.assertIn("w7: pullRequest(number: 7)", q)
        self.assertNotIn("pullRequest(number", w.check_query([25], []))
        # The first comment's id, which names a thread in a link, is read only in the second stage.
        self.assertNotIn("databaseId", q)
        d = w.detail_query([82, 7])
        self.assertIn("p82: pullRequest(number: 82)", d)
        self.assertEqual(d.count("opening: comments(first: 1) { nodes { databaseId } }"), 2)

    def test_a_resolved_thread_on_an_open_pr(self):
        old = snap_with({5: {101: False, 102: False}}, open_prs=[5])
        new = snap_with({5: {101: True, 102: False}}, open_prs=[5])
        self.assertEqual(w.gh_changes(old, new, "o/n"),
                         ["PR #5: decision thread resolved: https://github.com/o/n/pull/5#discussion_r101"])

    def test_the_last_one_is_also_all_resolved(self):
        old = snap_with({5: {101: True, 102: False}}, open_prs=[5])
        new = snap_with({5: {101: True, 102: True}}, open_prs=[5])
        self.assertEqual(w.gh_changes(old, new, "o/n"),
                         ["PR #5: decision thread resolved: https://github.com/o/n/pull/5#discussion_r102",
                          "PR #5: all review comments resolved"])

    def test_a_resolved_thread_on_a_merged_pr(self):
        old = snap_with({82: {4627001605: False}}, merged=[82])
        new = snap_with({82: {4627001605: True}}, merged=[82])
        self.assertEqual(w.gh_changes(old, new, "hornof/apb"),
                         ["PR #82: decision thread resolved: "
                          "https://github.com/hornof/apb/pull/82#discussion_r4627001605"])

    def test_merged_and_resolved_in_one_round(self):
        old = snap_with({82: {1: False, 2: False}}, open_prs=[82])
        new = snap_with({82: {1: True, 2: True}}, merged=[82])
        self.assertEqual(w.gh_changes(old, new), ["PR #82 merged: pr 82", "PR #82: decision thread resolved: r1",
                                                  "PR #82: decision thread resolved: r2"])

    def test_not_news(self):
        cases = [
            ({}, {5: {1: True}}),  # a thread new to us: its resolution isn't a change
            ({5: {1: True}}, {5: {1: False}}),  # unresolved again
            ({5: {1: False}}, {}),  # deleted, or the PR no longer read
            ({5: {1: True}}, {5: {1: True}}),
        ]
        for before, after in cases:
            old, new = snap_with(before, merged=[5]), snap_with(after, merged=[5])
            self.assertEqual(w.gh_changes(old, new), [], (before, after))
        old = snap_with({5: {1: False}}, merged=[5])
        del old["threads"]  # a state saved by an older version
        self.assertEqual(w.gh_changes(old, snap_with({5: {1: True}}, merged=[5])), [])

    def test_random_thread_changes_match_an_oracle(self):
        rng = random.Random(85)
        states = [True, False, None]  # None: no such thread
        for _ in range(1000):
            before, after = {}, {}
            for n in rng.sample(range(1, 9), rng.randint(0, 4)):
                for i in rng.sample(range(100, 106), rng.randint(0, 4)):
                    b, a = rng.choice(states), rng.choice(states)
                    if b is not None:
                        before.setdefault(n, {})[i] = b
                    if a is not None:
                        after.setdefault(n, {})[i] = a
            want = sorted("PR #%d: decision thread resolved: r%d" % (n, i) for n, t in after.items()
                          for i, r in t.items() if r and before.get(n, {}).get(i) is False)
            got = w.gh_changes(snap_with(before, merged=range(1, 9)), snap_with(after, merged=range(1, 9)))
            self.assertEqual(sorted(got), want, (before, after))

    def test_still_watched(self):
        snap = snap_with({5: {1: False}, 6: {1: True}, 7: {1: True, 2: False}, 8: {1: False}}, open_prs=[8],
                         merged=[5, 6, 7])
        self.assertEqual(w.still_watched(snap), [7, 5])  # 8 is open, 6 has nothing unresolved
        many = snap_with({n: {1: False} for n in range(1, 40)}, merged=range(1, 40))
        self.assertEqual(w.still_watched(many), list(range(39, 39 - w.MAX_WATCHED, -1)))
        self.assertEqual(w.still_watched({}), [])
        self.assertEqual(w.still_watched(None), [])


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
        self.watched = []  # the merged or closed PRs each snapshot was asked to keep watching
        self.asked = []  # when GitHub was read

    def snapshot(self, repo, issues, prs=(), **kw):
        self.watched.append(list(prs))
        self.asked.append(self.clock.t)
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
        self.assertEqual(self.clock.t, 1_000_000.0 + 3 * w.ACTIVE)  # GitHub is read every 2 min

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

    def test_a_merged_pr_stays_watched_until_its_threads_are_resolved(self):
        # The night of PR #82: it merged, and the human resolved its Decision thread after that.
        open_ = snap_with({82: {1: False}}, open_prs=[82])
        merged = snap_with({82: {1: False}}, merged=[82])
        self.snaps = [open_, merged]
        self.assertEqual(self.run_watch()[1:], ["GitHub: PR #82 merged: pr 82"])
        later = snap_with({82: {1: True}})  # dropped out of the recent merges; read as a watched PR
        self.snaps = [later]
        out = self.run_watch()
        self.assertEqual(out[1:], ["GitHub: PR #82: decision thread resolved: "
                                   "https://github.com/o/n/pull/82#discussion_r1"])
        self.assertEqual(self.watched, [[], [], [82]])
        self.snaps = [later]
        self.run_watch("--limit", "1")
        self.assertEqual(self.watched[-1], [])  # nothing left unresolved: no longer watched

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


def empty(**meta):
    """A snapshot with nothing in it; meta is what the two-stage read says about cost and activity."""
    s = {"open": {}, "merged": {}, "closed": {}, "issues": {}, "max": 0}
    if meta:
        s["meta"] = meta
    return s


class Pace(Loop):
    """How often GitHub is read: every 2 min while the repo is active, every 15 min once it has been
    quiet for an hour, back to 2 min on any change; never more than the account-wide ceiling allows.
    Stuck commands and quiet agents are still checked every TICK seconds."""

    def gaps(self):
        return [b - a for a, b in zip(self.asked, self.asked[1:])]

    def test_an_active_repo_is_read_every_two_minutes(self):
        self.snaps = [empty(newest=self.clock.t - 60)]
        self.run_watch("--limit", "10")
        self.assertEqual(self.gaps(), [w.ACTIVE] * 5)

    def test_a_repo_quiet_for_an_hour_is_read_every_15_minutes(self):
        self.snaps = [empty(newest=self.clock.t - 2 * 3600)]
        out = self.run_watch("--limit", "60")
        self.assertTrue(out[1].startswith("No change in 60 min"), out)
        self.assertEqual(self.gaps(), [w.QUIET] * 4)

    def test_a_change_brings_it_back_to_two_minutes(self):
        quiet = empty(newest=self.clock.t - 2 * 3600)
        self.snaps = [quiet]
        self.run_watch("--limit", "30")
        self.assertEqual(self.gaps(), [w.QUIET] * 2)
        busy = empty(newest=self.clock.t - 2 * 3600)
        busy["open"]["9"] = {"title": "new", "checks": "PENDING", "threads": 0, "open_threads": 0, "comments": 0}
        busy["max"] = 9
        self.snaps = [busy]
        self.asked = []
        self.assertEqual(self.run_watch("--limit", "10")[1:], ["GitHub: PR #9 opened: new (checks PENDING)"])
        self.asked = []
        self.run_watch("--limit", "10")
        self.assertEqual(self.gaps(), [w.ACTIVE] * 5)

    def test_the_quiet_pace_carries_over_a_restart(self):
        self.snaps = [empty(newest=self.clock.t - 2 * 3600)]
        self.run_watch("--limit", "30")
        self.asked = []
        self.run_watch("--limit", "60")
        self.assertEqual(self.gaps(), [w.QUIET] * 4)

    def test_stuck_commands_are_still_checked_between_reads(self):
        self.snaps = [empty(newest=self.clock.t - 2 * 3600)]
        self.ps = ("  50     1   9:00.00   2:00:00 /x/claude\n"
                   "  60    50   0:00.00     30:00 /bin/zsh -c npm test\n"
                   "  61    60   0:00.00      9:30 git hash-object --stdin\n")
        out = self.run_watch("--stuck-minutes", "10", "--root-pid", "50")
        self.assertTrue(out[1].startswith("Stuck: process 61"), out)
        self.assertLessEqual(self.clock.t - 1_000_000.0, 30 + w.TICK)
        self.assertEqual(len(self.asked), 1)

    def test_poll_still_sets_a_fixed_pace(self):
        self.snaps = [empty(newest=self.clock.t - 2 * 3600)]
        self.run_watch("--limit", "2", "--poll", "30")
        self.assertEqual(self.gaps(), [30] * 4)

    def test_the_watchers_on_this_machine_share_the_ceiling(self):
        folder = os.path.join(self.dir, "watchers")
        others = list(range(7001, 7010))  # nine other live watchers
        for pid in others:
            w.register(folder, pid, {"repo": "o/x%d" % pid, "poll": 120, "started": self.clock.t, "issues": []})
        self.ps = "".join("%d 1 0:01.00 10:00 python3 /p/scripts/apb-watch.py --state s\n" % p for p in others)
        self.ps += "999 1 0:01.00 00:01 python3 /p/scripts/apb-watch.py --state me\n"
        cost = 20
        self.snaps = [empty(newest=self.clock.t - 60, cost=cost)]
        opts = w.parse_args(["--repo", "o/n", "--state", self.state, "--events", os.path.join(self.dir, "none.jsonl"),
                             "--limit", "30"])
        w.watch(opts, snapshot=self.snapshot, ps_text=lambda: self.ps, now=self.clock.now, sleep=self.clock.sleep,
                me=999, registry=folder)
        want = 10 * cost * 3600 / w.CEILING  # ten watchers, 20 points a read: 720 s apart
        self.assertTrue(all(abs(g - want) < 1e-6 for g in self.gaps()), self.gaps())
        self.assertEqual(len(self.gaps()), 2)

    def test_its_own_entry_shows_the_pace_while_it_runs_and_goes_at_exit(self):
        folder = os.path.join(self.dir, "watchers")
        seen = []

        def snapshot(repo, issues, prs=(), **kw):
            seen.append(w.live_watchers(folder, {999: {"command": "python3 apb-watch.py"}}))
            return empty(newest=self.clock.t - 2 * 3600)

        opts = w.parse_args(["--repo", "o/n", "--state", self.state, "--events", os.path.join(self.dir, "none.jsonl"),
                             "--limit", "20", "--issue", "25"])
        w.watch(opts, snapshot=snapshot, ps_text=lambda: "", now=self.clock.now, sleep=self.clock.sleep, me=999,
                registry=folder)
        [mine] = seen[-1]
        self.assertEqual((mine["repo"], mine["pid"], mine["poll"], mine["issues"]), ("o/n", 999, w.QUIET, [25]))
        self.assertEqual(os.listdir(folder), [])

    def test_its_entry_is_written_again_at_every_read(self):
        folder = os.path.join(self.dir, "watchers")
        seen = []

        def snapshot(repo, issues, prs=(), **kw):
            seen.append(sorted(os.listdir(folder)))
            for name in os.listdir(folder):  # lost, say removed by another watcher
                os.remove(os.path.join(folder, name))
            return empty(newest=self.clock.t - 60)

        opts = w.parse_args(["--repo", "o/n", "--state", self.state, "--events", os.path.join(self.dir, "none.jsonl"),
                             "--limit", "6"])
        w.watch(opts, snapshot=snapshot, ps_text=lambda: "", now=self.clock.now, sleep=self.clock.sleep, me=999,
                registry=folder)
        self.assertEqual(len(seen), 4)
        self.assertEqual(seen, [["o_n-999.json"]] * 4)

    def test_its_entry_records_the_budget_of_its_last_read(self):
        folder = os.path.join(self.dir, "watchers")
        seen = []
        reset = self.clock.t + 1800

        def snapshot(repo, issues, prs=(), **kw):
            seen.append(w.live_watchers(folder, {999: {"command": "python3 apb-watch.py"}}))
            return empty(newest=self.clock.t - 60, remaining=4100 - len(seen), limit=5000, reset=reset)

        opts = w.parse_args(["--repo", "o/n", "--state", self.state, "--events", os.path.join(self.dir, "none.jsonl"),
                             "--limit", "2"])
        w.watch(opts, snapshot=snapshot, ps_text=lambda: "", now=self.clock.now, sleep=self.clock.sleep, me=999,
                registry=folder)
        [mine] = seen[-1]  # seen during the second read: the first read's budget
        self.assertEqual(mine["budget"], {"remaining": 4099, "limit": 5000, "reset": reset,
                                          "at": 1_000_000.0})

    def test_a_low_budget_is_said_once_and_slows_the_reads(self):
        folder = os.path.join(self.dir, "watchers")
        reset = self.clock.t + 1800
        self.snaps = [empty(newest=self.clock.t - 60, remaining=400, reset=reset)]
        opts = w.parse_args(["--repo", "o/n", "--state", self.state, "--events", os.path.join(self.dir, "none.jsonl"),
                             "--limit", "20"])
        run = lambda: w.watch(opts, snapshot=self.snapshot, ps_text=lambda: "", now=self.clock.now,
                              sleep=self.clock.sleep, me=999, registry=folder)
        out = run()
        self.assertEqual(len(out), 2, out)
        self.assertIn("400 points left", out[1])
        self.assertIn(w.clock(reset), out[1])
        self.asked = []
        out = run()  # the same reset: not said again, by this watcher or another one
        self.assertTrue(out[1].startswith("No change"), out)
        self.assertEqual(self.gaps(), [w.QUIET])

    def test_rate_limited_waits_for_the_reset_and_says_so_once(self):
        folder = os.path.join(self.dir, "watchers")
        reset = self.clock.t + 1500
        self.snaps = [w.RateLimited(reset)]
        opts = w.parse_args(["--repo", "o/n", "--state", self.state, "--events", os.path.join(self.dir, "none.jsonl"),
                             "--limit", "60"])
        run = lambda: w.watch(opts, snapshot=self.snapshot, ps_text=lambda: "", now=self.clock.now,
                              sleep=self.clock.sleep, me=999, registry=folder)
        out = run()
        self.assertIn("rate-limited until %s" % w.clock(reset), out[1])
        self.asked = []
        self.snaps = [empty(newest=self.clock.t)]
        run()
        self.assertGreaterEqual(self.asked[0], reset)  # nothing asked before the reset


def card(comments, state="OPEN"):
    return {"title": "Card", "state": state, "comments": comments}


class IssueComments(Loop):
    """Issue #96: a new comment on a watched issue always gets its own line, whatever else changed
    in the same read, and even when the issue was not watched at the last read."""

    def test_a_comment_and_a_merge_in_one_read_each_get_their_own_line(self):
        # tahoe-tracker: the human answered a Decision on the card's issue (#1) in the same read in
        # which PR #2 merged and closed #1.
        before = {"open": {"2": {"title": "Setup", "checks": "SUCCESS", "threads": 0, "open_threads": 0,
                                 "comments": 0, "replies": 0, "reviews": 0}},
                  "merged": {}, "closed": {}, "issues": {"1": card(3)}, "max": 2}
        after = {"open": {}, "merged": {"2": "Setup"}, "closed": {}, "issues": {"1": card(4, "CLOSED")}, "max": 2}
        self.snaps = [before, after]
        self.assertEqual(self.run_watch("--issue", "1")[1:], ["GitHub: PR #2 merged: Setup",
                                                             "GitHub: Issue #1: 1 new comment (Card)",
                                                             "GitHub: Issue #1 is now closed (Card)"])

    def test_random_changes_never_hide_an_issue_comment(self):
        rng = random.Random(96)
        for _ in range(2000):
            old = random_snapshot(rng)
            for n in rng.sample(range(1, 4), rng.randint(1, 3)):
                old["issues"].setdefault(str(n), card(rng.randint(0, 5)))
            new = json.loads(json.dumps(old))
            for _ in range(rng.randint(0, 6)):  # any mix of PR and issue changes in one read
                new = mutate(rng, new)[0]
                for p in new["open"].values():  # a PR mutate opened has no replies or reviews yet
                    p.setdefault("replies", 0)
                    p.setdefault("reviews", 0)
            for n in rng.sample(sorted(new["issues"]), rng.randint(0, len(new["issues"]))):
                new["issues"][n]["comments"] += rng.randint(1, 3)
            got = w.gh_changes(old, new)
            for n, i in new["issues"].items():
                added = i["comments"] - old["issues"][n]["comments"]
                mine = [g for g in got if g.startswith("Issue #%s: " % n)]
                want = ["Issue #%s: %s (%s)" % (n, w.count(added, "new comment"), i["title"])] if added else []
                self.assertEqual(mine, want, (old, new, got))
            for g in got:  # one event a line: no line names a second PR or issue
                self.assertEqual(g.count("#"), 1, g)

    def test_a_comment_while_the_watcher_was_stopped_is_reported_on_the_next_start(self):
        base = {"open": {}, "merged": {}, "closed": {}, "issues": {"1": card(3)}, "max": 0}
        self.snaps = [base]
        self.run_watch("--issue", "1", "--limit", "1")
        later = json.loads(json.dumps(base))
        later["issues"]["1"]["comments"] = 4
        self.snaps = [later]
        self.assertEqual(self.run_watch("--issue", "1")[1:], ["GitHub: Issue #1: 1 new comment (Card)"])

    def test_a_comment_posted_before_the_issue_was_watched_is_reported(self):
        # The watcher read GitHub at the start without --issue 1; the human answered on #1 30 s later;
        # the watcher was then started again with --issue 1.
        t0 = self.clock.t
        self.snaps = [empty()]
        self.run_watch("--limit", "1")
        answered = empty(issue_times={"1": [t0 - 5000, t0 + 30]})
        answered["issues"]["1"] = card(2)
        self.snaps = [answered]
        self.assertEqual(self.run_watch("--issue", "1")[1:], ["GitHub: Issue #1: 1 new comment (Card)"])
        self.assertTrue(self.run_watch("--issue", "1", "--limit", "1")[1].startswith("No change"))  # once only

    def test_comments_older_than_the_last_read_are_not_news(self):
        t0 = self.clock.t
        self.snaps = [empty()]
        self.run_watch("--limit", "1")
        old = empty(issue_times={"1": [t0 - 5000, t0 - 10]})
        old["issues"]["1"] = card(2)
        self.snaps = [old]
        self.assertTrue(self.run_watch("--issue", "1", "--limit", "1")[1].startswith("No change"))

    def test_a_first_run_takes_its_baseline_without_reporting(self):
        first = empty(issue_times={"1": [self.clock.t - 10]})
        first["issues"]["1"] = card(1)
        self.snaps = [first]
        self.assertTrue(self.run_watch("--issue", "1", "--limit", "1")[1].startswith("No change"))


class Ceiling(unittest.TestCase):
    """github_interval against a second way of working out the same rule."""

    def test_examples(self):
        t = 10 ** 6
        self.assertEqual(w.github_interval(t, t - 100, 1, 2), w.ACTIVE)
        self.assertEqual(w.github_interval(t, t - 3601, 1, 2), w.QUIET)
        self.assertEqual(w.github_interval(t, t - 3601, 1, 2, fixed=20), 20)
        self.assertEqual(w.github_interval(t, t, 1, 2, remaining=100, reset=t + 3000), w.QUIET)
        self.assertEqual(w.github_interval(t, t, 1, 2, remaining=100, reset=t + 60), w.ACTIVE)
        self.assertEqual(w.github_interval(t, t, 1, 2, remaining=100, reset=t - 60), w.ACTIVE)

    def test_random_paces_stay_under_the_ceiling(self):
        rng = random.Random(94)
        for _ in range(5000):
            t = 10 ** 6
            active_at = t - rng.choice([0, 100, 3599, 3600, 3601, 10 ** 5])
            live = rng.randint(1, 60)
            cost = rng.choice([1, 1, 2, 3, 8, 25, 90])
            fixed = rng.choice([None, None, 0.05, 20, 300])
            remaining = rng.choice([None, 0, 999, 1000, 4000])
            reset = t + rng.choice([-10, 30, 2000])
            got = w.github_interval(t, active_at, live, cost, remaining=remaining, reset=reset, fixed=fixed)
            pace = fixed if fixed else (w.ACTIVE if t - active_at < 3600 else w.QUIET)
            if not fixed:
                pace = max(pace, live * cost * 3600 / w.CEILING)
            if remaining is not None and remaining < w.LOW and reset > t:
                pace = max(pace, min(w.QUIET, reset - t))
            self.assertAlmostEqual(got, pace, msg=(active_at, live, cost, fixed, remaining, reset))
            if not fixed:
                self.assertLessEqual(live * cost * 3600 / got, w.CEILING + 1e-6)


# ---------------------------------------------------------------- the two-stage read, against a fake GitHub

# A fake GitHub, shared with the fake gh below: it answers the watcher's queries from a model.
# model: {"prs": [{"number", "title", "state", "updated", "checks", "threads": [[id, resolved, comments]],
#                  "comments", "reviews"}], "issues": {"25": {"title", "state", "comments", "updated", "times"}}}
# An issue's "times" (optional) are when its comments were posted, oldest first.
# Posting a comment, a review or a thread moves a PR's updatedAt; resolving a thread and checks finishing don't.
FAKE_GITHUB = r'''
import re, time

def iso(t):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))

def checks_node(p):
    return {"nodes": [{"commit": {"statusCheckRollup": {"state": p["checks"]} if p.get("checks") else None}}]}

def resolved(p, n):
    return {"nodes": [{"isResolved": t[1]} for t in p.get("threads", [])[:n]]}

def respond(query, model):
    prs = {p["number"]: p for p in model.get("prs", [])}
    issues = model.get("issues", {})
    repo, asked = {}, []
    if "open: pullRequests(" in query:
        # Only the fields the open section asks for, so a check that leaves one out is caught.
        section = query.split("open: pullRequests(")[1].split("recent:")[0]
        opened = sorted((p for p in prs.values() if p["state"] == "OPEN"), key=lambda p: -p["updated"])[:10]
        repo["open"] = {"nodes": []}
        for p in opened:
            node = {"number": p["number"], "updatedAt": iso(p["updated"])}
            if "statusCheckRollup { state }" in section:
                node["commits"] = checks_node(p)
            if "reviewThreads(first: 20) { nodes { isResolved }" in section:
                node["reviewThreads"] = resolved(p, 20)
            repo["open"]["nodes"].append(node)
        done = sorted((p for p in prs.values() if p["state"] != "OPEN"), key=lambda p: -p["updated"])[:5]
        repo["recent"] = {"nodes": [{"number": p["number"], "title": p["title"], "state": p["state"],
                                     "updatedAt": iso(p["updated"])} for p in done]}
        latest = sorted(issues.values(), key=lambda i: -i["updated"])[:1]
        repo["latest"] = {"nodes": [{"updatedAt": iso(i["updated"])} for i in latest]}
    for n in re.findall(r"\bi(\d+): issue\(", query):
        i = issues.get(n)
        repo["i" + n] = None if i is None else {"title": i["title"], "state": i["state"], "updatedAt": iso(i["updated"]),
                                                "comments": {"totalCount": i["comments"], "nodes": [
                                                    {"createdAt": iso(t)} for t in i.get("times", [])[-20:]]}}
    for n in re.findall(r"\bw(\d+): pullRequest\(", query):
        p = prs.get(int(n))
        repo["w" + n] = None if p is None else {"number": p["number"], "updatedAt": iso(p["updated"]),
                                                "reviewThreads": resolved(p, 20)}
    for n in re.findall(r"\bp(\d+): pullRequest\(", query):
        asked.append(int(n))
        p = prs.get(int(n))
        repo["p" + n] = None if p is None else {
            "number": p["number"], "title": p["title"], "commits": checks_node(p),
            "reviewThreads": {"nodes": [{"isResolved": t[1], "comments": {"totalCount": t[2]},
                                         "opening": {"nodes": [{"databaseId": t[0]}]}} for t in p.get("threads", [])[:50]]},
            "comments": {"totalCount": p.get("comments", 0)}, "reviews": {"totalCount": p.get("reviews", 0)}}
    cost = max(1, round(len(asked) * 0.51)) if asked else 1
    return {"rateLimit": {"cost": cost, "remaining": model.get("remaining", 4990), "limit": 5000,
                          "resetAt": iso(model.get("reset", 2000000000))}, "repository": repo}, asked
'''
fake_github = {}
exec(FAKE_GITHUB, fake_github)


def pr(n, state="OPEN", updated=1000, **more):
    p = {"number": n, "title": "pr %d" % n, "state": state, "updated": updated, "checks": None, "threads": [],
         "comments": 0, "reviews": 0}
    p.update(more)
    return p


def oracle_snapshot(model, issues, watch):
    """The snapshot worked out straight from the whole model, the way one big query would see it."""
    prs = {p["number"]: p for p in model["prs"]}
    snap = {"open": {}, "merged": {}, "closed": {}, "issues": {}, "threads": {}}
    opened = [p for p in prs.values() if p["state"] == "OPEN"]
    done = sorted((p for p in prs.values() if p["state"] != "OPEN"), key=lambda p: -p["updated"])[:5]
    for p in opened:
        t = p["threads"]
        snap["open"][str(p["number"])] = {
            "title": p["title"], "checks": p["checks"] or "NONE", "threads": len(t),
            "open_threads": sum(1 for x in t if not x[1]), "comments": p["comments"],
            "replies": sum(x[2] - 1 for x in t), "reviews": p["reviews"]}
    for p in done:
        snap["merged" if p["state"] == "MERGED" else "closed"][str(p["number"])] = p["title"]
    for n in {p["number"] for p in opened + done} | {n for n in watch if n in prs}:
        if prs[n]["threads"]:
            snap["threads"][str(n)] = {str(x[0]): x[1] for x in prs[n]["threads"]}
    for n in issues:
        i = model.get("issues", {}).get(str(n))
        if i:
            snap["issues"][str(n)] = {"title": i["title"], "state": i["state"], "comments": i["comments"]}
    snap["max"] = max([0] + [p["number"] for p in opened + done])
    return snap


class FakeGraphQL:
    def __init__(self, model):
        self.model = model
        self.queries = []  # (stage, PR numbers read in full)

    def __call__(self, owner, name, query):
        data, asked = fake_github["respond"](query, self.model)
        self.queries.append(("check" if "open: pullRequests(" in query else "detail", asked))
        return data


def two_stage(gql, issues=(), watch=(), cache=None):
    return w.gh_snapshot("o/n", list(issues), list(watch), cache=cache if cache is not None else {}, graphql=gql)


class TwoStage(unittest.TestCase):
    """Stage 1, every read: one cheap query for what changed. Stage 2: the full read, only for the PRs
    that changed."""

    def test_the_first_read_fetches_every_pr_then_nothing_while_nothing_changes(self):
        model = {"prs": [pr(5, updated=1000), pr(6, updated=900), pr(4, "MERGED", updated=800)], "issues": {}}
        gql, cache = FakeGraphQL(model), {}
        two_stage(gql, cache=cache)
        self.assertEqual(gql.queries, [("check", []), ("detail", [5, 6, 4])])
        gql.queries = []
        snap = two_stage(gql, cache=cache)
        self.assertEqual(gql.queries, [("check", [])])
        self.assertEqual(sorted(snap["open"]), ["5", "6"])

    def test_only_what_changed_is_read_in_full(self):
        model = {"prs": [pr(5, updated=1000), pr(6, updated=900)], "issues": {}}
        gql, cache = FakeGraphQL(model), {}
        two_stage(gql, cache=cache)
        model["prs"][0].update(updated=1100, comments=1)
        gql.queries = []
        snap = two_stage(gql, cache=cache)
        self.assertEqual(gql.queries, [("check", []), ("detail", [5])])
        self.assertEqual(snap["open"]["5"]["comments"], 1)

    def test_checks_finishing_and_a_resolved_thread_are_seen_without_an_update(self):
        model = {"prs": [pr(5, checks="PENDING", threads=[[11, False, 1]])], "issues": {}}
        gql, cache = FakeGraphQL(model), {}
        two_stage(gql, cache=cache)
        model["prs"][0]["checks"] = "SUCCESS"
        self.assertEqual(two_stage(gql, cache=cache)["open"]["5"]["checks"], "SUCCESS")
        model["prs"][0]["threads"][0][1] = True
        self.assertEqual(two_stage(gql, cache=cache)["threads"], {"5": {"11": True}})

    def test_cost_budget_and_newest_activity_are_read(self):
        model = {"prs": [pr(n, updated=1000 + n) for n in range(1, 9)], "remaining": 3210, "reset": 1_800_000_000,
                 "issues": {"25": {"title": "C", "state": "OPEN", "comments": 2, "updated": 5000}}}
        gql = FakeGraphQL(model)
        meta = two_stage(gql, issues=[25])["meta"]
        self.assertEqual(meta["cost"], 1 + 4)  # the check, plus 8 PRs read in full
        self.assertEqual((meta["remaining"], meta["reset"], meta["newest"]), (3210, 1_800_000_000, 5000))

    def test_each_issues_latest_comment_times_are_read(self):
        model = {"prs": [], "issues": {"1": {"title": "Card", "state": "OPEN", "comments": 25, "updated": 5000,
                                             "times": list(range(1000, 1025))}}}
        gql = FakeGraphQL(model)
        snap = two_stage(gql, issues=[1])
        self.assertIn("i1: issue(number: 1) { title state updatedAt comments(last: 20) { totalCount nodes { createdAt } } }",
                      w.check_query([1], []))
        self.assertEqual(snap["meta"]["issue_times"], {"1": list(range(1005, 1025))})
        self.assertEqual(snap["issues"]["1"]["comments"], 25)

    def test_more_open_prs_than_one_page_keeps_the_older_ones(self):
        model = {"prs": [pr(n, updated=1000 + n) for n in range(1, 13)], "issues": {}}
        gql, cache = FakeGraphQL(model), {}
        old = two_stage(gql, cache=cache)
        for p in model["prs"][2:]:
            p["updated"] += 100
        new = two_stage(gql, cache=cache)
        self.assertEqual(w.gh_changes({k: v for k, v in old.items() if k != "meta"}, new), [])

    def test_random_histories_match_one_big_read(self):
        rng = random.Random(9494)
        for _ in range(150):
            clock = [1000]

            def tick():
                clock[0] += rng.randint(1, 50)
                return clock[0]

            model = {"prs": [], "issues": {"25": {"title": "C", "state": "OPEN", "comments": 0, "updated": 900}}}
            next_n, thread_id = [1], [100]
            gql, cache, prev, oracle_prev = FakeGraphQL(model), {}, None, None
            for step in range(rng.randint(1, 25)):
                opened = [p for p in model["prs"] if p["state"] == "OPEN"]
                kind = rng.choice(["open", "comment", "review", "thread", "reply", "resolve", "checks", "merge",
                                   "close", "issue", "resolve-merged", "nothing"])
                if kind == "open" and len(opened) < 10 or not model["prs"]:
                    model["prs"].append(pr(next_n[0], updated=tick()))
                    next_n[0] += 1
                elif kind in ("comment", "review", "thread", "reply", "resolve", "checks", "merge", "close") and opened:
                    p = rng.choice(opened)
                    if kind == "comment":
                        p["comments"] += 1
                    elif kind == "review":
                        p["reviews"] += 1
                    elif kind == "thread" and len(p["threads"]) < 5:
                        thread_id[0] += 1
                        p["threads"].append([thread_id[0], False, 1])
                    elif kind == "reply" and p["threads"]:
                        rng.choice(p["threads"])[2] += 1
                    elif kind == "resolve" and p["threads"]:
                        rng.choice(p["threads"])[1] = True
                    elif kind == "checks":
                        p["checks"] = rng.choice(["PENDING", "SUCCESS", "FAILURE"])
                    elif kind in ("merge", "close"):
                        p["state"] = "MERGED" if kind == "merge" else "CLOSED"
                    if kind not in ("resolve", "checks"):  # these two don't move updatedAt on GitHub
                        p["updated"] = tick()
                elif kind == "resolve-merged":
                    done = [p for p in model["prs"] if p["state"] != "OPEN" and any(not t[1] for t in p["threads"])]
                    if done:
                        next(t for t in rng.choice(done)["threads"] if not t[1])[1] = True
                elif kind == "issue":
                    model["issues"]["25"]["comments"] += 1
                    model["issues"]["25"]["updated"] = tick()
                watch = w.still_watched(prev)
                got = two_stage(gql, issues=[25], watch=watch, cache=cache)
                got = json.loads(json.dumps(got))
                got.pop("meta")
                want = oracle_snapshot(model, [25], w.still_watched(oracle_prev))
                self.assertEqual(got, want, (step, kind, model))
                if prev is not None:
                    self.assertEqual(w.gh_changes(prev, got), w.gh_changes(oracle_prev, want))
                prev, oracle_prev = got, want

    def test_rate_limited_is_its_own_error(self):
        def limited(owner, name, query):
            raise w.RateLimited(1_800_000_000)

        with self.assertRaises(w.RateLimited) as e:
            two_stage(limited)
        self.assertEqual(e.exception.reset, 1_800_000_000)

    def test_gh_failures_say_whether_it_is_the_rate_limit(self):
        self.assertTrue(w.is_rate_limit("GraphQL: API rate limit already exceeded for user ID 1."))
        self.assertTrue(w.is_rate_limit("gh: API rate limit exceeded for user ID 1 (HTTP 403)"))
        self.assertFalse(w.is_rate_limit("HTTP 502: Bad Gateway"))


# ---------------------------------------------------------------- the watchers on this machine

class Registry(unittest.TestCase):
    def setUp(self):
        self.folder = os.path.join(tempfile.mkdtemp(), "watchers")

    def test_only_live_watchers_count_and_dead_ones_are_cleared(self):
        for pid in (11, 12, 13):
            w.register(self.folder, pid, {"repo": "o/r", "poll": 120, "started": 1000.0, "issues": []})
        with open(os.path.join(self.folder, "garbled-14.json"), "w") as f:
            f.write("not json")
        self.age(*os.listdir(self.folder))
        procs = {11: {"command": "python3 /x/apb-watch.py --state s"},
                 12: {"command": "vim notes.txt"}}  # 12 is a reused pid; 13 is gone
        self.assertEqual([r["pid"] for r in w.live_watchers(self.folder, procs)], [11])
        self.assertEqual(sorted(os.listdir(self.folder)), ["o_r-11.json", "o_r-12.json"])
        # ps could not be read: count nothing, delete nothing
        self.assertEqual(w.live_watchers(self.folder, {}), [])
        self.assertEqual(len(os.listdir(self.folder)), 2)

    def age(self, *names):
        """Make these files older than the grace period, as if written long ago."""
        old = time.time() - w.FRESH - 5
        for name in names:
            os.utime(os.path.join(self.folder, name), (old, old))

    def test_a_watcher_that_started_after_the_ps_snapshot_is_kept_and_counted(self):
        # The reviewer's repro: A takes its ps snapshot, then B starts and registers, then A counts.
        a_snapshot = {100: {"command": "python3 apb-watch.py --state a"}}
        w.register(self.folder, 100, {"repo": "o/a", "issues": [], "poll": 120, "started": 0})
        w.register(self.folder, 200, {"repo": "o/b", "issues": [], "poll": 120, "started": 1})
        self.assertEqual(sorted(r["pid"] for r in w.live_watchers(self.folder, a_snapshot)), [100, 200])
        now = {**a_snapshot, 200: {"command": "python3 apb-watch.py --state b"}}
        self.assertEqual(sorted(r["repo"] for r in w.live_watchers(self.folder, now)), ["o/a", "o/b"])

    def test_random_orders_never_lose_a_live_watcher(self):
        # Oracle: a watcher is lost only if its file is gone while its process still runs.
        rng = random.Random(100)
        for _ in range(300):
            folder = os.path.join(tempfile.mkdtemp(), "watchers")
            running, procs_seen = set(), {}
            for _ in range(rng.randint(1, 12)):
                step = rng.choice(["start", "start", "snapshot", "count", "stop"])
                if step == "start":
                    pid = rng.randint(1000, 1010)
                    if pid not in running:
                        running.add(pid)
                        w.register(folder, pid, {"repo": "o/r", "issues": [], "poll": 120, "started": 0})
                elif step == "snapshot":
                    procs_seen = {p: {"command": "python3 apb-watch.py"} for p in running}
                elif step == "count":
                    w.live_watchers(folder, procs_seen)
                elif step == "stop" and running:
                    pid = rng.choice(sorted(running))
                    running.discard(pid)
                    w.unregister(folder, pid)
            on_file = {int(n.rsplit("-", 1)[1][:-5]) for n in os.listdir(folder) if n.endswith(".json")} \
                if os.path.isdir(folder) else set()
            self.assertLessEqual(running, on_file)

    def test_unregister_removes_only_its_own(self):
        w.register(self.folder, 21, {"repo": "o/r", "poll": 120, "started": 1.0, "issues": []})
        w.register(self.folder, 22, {"repo": "o/r", "poll": 120, "started": 1.0, "issues": []})
        w.unregister(self.folder, 21)
        self.assertEqual(os.listdir(self.folder), ["o_r-22.json"])

    def test_a_notice_is_claimed_once_per_reset(self):
        self.assertTrue(w.claim_notice(self.folder, 1_800_000_000))
        self.assertFalse(w.claim_notice(self.folder, 1_800_000_000))
        self.assertTrue(w.claim_notice(self.folder, 1_800_003_600))

    def test_the_budget_comes_from_the_newest_watcher_read(self):
        now = 1_000_000.0
        gh = lambda: {"remaining": 4770, "limit": 5000, "reset": now + 900}
        records = [{"pid": 1, "budget": {"remaining": 4300, "limit": 5000, "reset": now + 600, "at": now - 300}},
                   {"pid": 2, "budget": {"remaining": 4167, "limit": 5000, "reset": now + 600, "at": now - 60}},
                   {"pid": 3}]
        b = w.account_budget(records, now, gh)
        self.assertEqual((b["remaining"], b["source"], b["at"]), (4167, "watcher", now - 60))
        # No live watcher has one, or the newest is from an hour that has reset: gh api rate_limit.
        for recs in ([], [{"pid": 3}], [{"pid": 1, "budget": {"remaining": 10, "reset": now - 5, "at": now - 900}}]):
            b = w.account_budget(recs, now, gh)
            self.assertEqual((b["remaining"], b["source"], b["at"]), (4770, "gh", now), recs)
        self.assertIsNone(w.account_budget([], now, lambda: None))

    def test_random_budgets_match_a_second_way(self):
        rng = random.Random(9)
        now = 1_000_000.0
        for _ in range(2000):
            recs = []
            for pid in range(rng.randint(0, 5)):
                r = {"pid": pid}
                if rng.random() < 0.7:
                    r["budget"] = {"remaining": rng.randint(0, 5000), "reset": now + rng.choice([-60, 60, 3000]),
                                   "at": now - rng.randint(0, 3600)}
                recs.append(r)
            usable = [r["budget"] for r in recs if "budget" in r and r["budget"]["reset"] > now]
            got = w.account_budget(recs, now, lambda: {"remaining": -1, "limit": 5000, "reset": now})
            if usable:
                newest = max(b["at"] for b in usable)
                self.assertEqual(got["source"], "watcher")
                self.assertEqual(got["at"], newest)
                self.assertIn(got["remaining"], [b["remaining"] for b in usable if b["at"] == newest])
            else:
                self.assertEqual((got["source"], got["remaining"]), ("gh", -1))

    def test_the_budget_line_says_where_it_came_from_and_how_old_it_is(self):
        now = 1_000_000.0
        line = w.listing([], {"remaining": 4167, "limit": 5000, "reset": now + 600, "source": "watcher",
                              "at": now - 130}, now)[-1]
        self.assertIn("4167 of 5000 points left, resets at %s" % w.clock(now + 600), line)
        self.assertIn("read by a watcher 2 min ago", line)
        line = w.listing([], {"remaining": 4770, "limit": 5000, "reset": now + 600, "source": "gh", "at": now}, now)[-1]
        self.assertIn("read from gh api rate_limit under 1 min ago", line)

    def test_a_listing_for_one_project_counts_the_others_without_naming_them(self):
        now = 1_000_000.0
        recs = [{"pid": 31, "repo": "hornof/apb", "poll": 900, "started": now - 60, "issues": [25]},
                {"pid": 32, "repo": "hornof/tahoe", "poll": 120, "started": now - 60, "issues": [3]},
                {"pid": 33, "repo": "hornof/sketch", "poll": 120, "started": now - 60, "issues": []}]
        text = "\n".join(w.listing(recs, None, now, mine=lambda r: r["repo"] == "hornof/apb"))
        self.assertIn("1 watcher for this project:", text)
        self.assertIn("hornof/apb #25", text)
        self.assertIn("2 other watchers on this machine.", text)
        for other in ("tahoe", "sketch", "pid 32", "pid 33", "#3"):
            self.assertNotIn(other, text)
        text = "\n".join(w.listing(recs[1:2], None, now, mine=lambda r: False))
        self.assertIn("No watchers for this project.", text)
        self.assertIn("1 other watcher on this machine.", text)

    def test_the_listing(self):
        now = 1_000_000.0
        records = [{"pid": 31, "repo": "hornof/apb", "poll": 900, "started": now - 3900, "issues": [25]},
                   {"pid": 32, "repo": "hornof/tahoe", "poll": 120, "started": now - 30, "issues": []}]
        lines = w.listing(records, {"remaining": 4812, "limit": 5000, "reset": now + 600}, now)
        text = "\n".join(lines)
        self.assertIn("2 watchers", lines[0])
        self.assertIn("hornof/apb #25: every 15 min, running 1 h 5 min (pid 31)", text)
        self.assertIn("hornof/tahoe: every 2 min, running under 1 min (pid 32)", text)
        self.assertIn("4812 of 5000 points left, resets at %s" % w.clock(now + 600), lines[-1])
        self.assertIn("No apb watchers", w.listing([], None, now)[0])
        self.assertIn("could not be read", w.listing([], None, now)[-1])

    def test_the_listing_names_the_session_that_holds_each_watcher(self):
        # Issue #133: the owner file next to a watcher's state file names its session.
        folder = os.path.join(tempfile.mkdtemp(), ".apb")
        os.makedirs(folder)
        name = "apb orch 4 · 0.3.59 · Nov 21 7:20 PM"
        with open(os.path.join(folder, "watcher-owner.json"), "w") as f:
            json.dump({"root": 1, "started": 0, "session": "s", "name": name}, f)
        held = {"pid": 31, "repo": "hornof/apb", "poll": 120, "started": 0, "issues": [25],
                "state": os.path.join(folder, "watch.json")}
        loose = {"pid": 32, "repo": "hornof/tahoe", "poll": 120, "started": 0, "issues": [],
                 "state": os.path.join(tempfile.mkdtemp(), "watch.json")}
        unnamed = dict(loose, pid=33)
        self.assertEqual(w.owner_name(held), name)
        self.assertIsNone(w.owner_name(loose))
        self.assertIsNone(w.owner_name({"pid": 34}))
        lines = w.listing([dict(held, owner=w.owner_name(held)), dict(unnamed, owner=None)], None, 0)
        self.assertIn('  hornof/apb #25: every 2 min, running under 1 min (pid 31), held by "%s"' % name, lines)
        self.assertIn("  hornof/tahoe: every 2 min, running under 1 min (pid 33)", lines)


# ---------------------------------------------------------------- the real script

FAKE_GH = FAKE_GITHUB + '''
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
if sys.argv[1:3] == ["repo", "view"]:
    print("o/n"); sys.exit(0)
with open(os.path.join(d, "args"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
if sys.argv[1:3] == ["api", "rate_limit"]:
    print(json.dumps({"resources": {"graphql": {"limit": 5000, "remaining": 4321, "reset": 1800000000}}})); sys.exit(0)
query = [a for a in sys.argv if a.startswith("query=")][0]
models = json.load(open(os.path.join(d, "answers.json")))
count = os.path.join(d, "count")
i = int(open(count).read()) if os.path.exists(count) else 0
if "open: pullRequests(" in query:  # each read starts with the check: the next model
    i += 1
    open(count, "w").write(str(i))
m = models[min(max(i, 1), len(models)) - 1]
if m == "fail":
    print("HTTP 502", file=sys.stderr); sys.exit(1)
if m == "limited":
    print("GraphQL: API rate limit already exceeded for user ID 1.", file=sys.stderr); sys.exit(1)
print(json.dumps({"data": respond(query, m)[0]}))
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
        self.watchers = os.path.join(self.dir, "watchers")
        self.env = dict(os.environ, PATH=bindir + os.pathsep + os.environ["PATH"], FAKE_GH_DIR=self.dir,
                        APB_WATCHERS_DIR=self.watchers)
        self.state = os.path.join(self.dir, "state.json")

    def answers(self, items):
        """One fake-GitHub model per read; the last one stays."""
        with open(os.path.join(self.dir, "answers.json"), "w") as f:
            json.dump(items, f)

    def calls(self):
        with open(os.path.join(self.dir, "args")) as f:
            return [json.loads(line) for line in f.read().splitlines()]

    def run_script(self, *extra, timeout=30):
        args = [sys.executable, SCRIPT, "--state", self.state, "--poll", "0.05",
                "--events", os.path.join(self.dir, "none.jsonl")] + list(extra)
        r = subprocess.run(args, capture_output=True, text=True, env=self.env, cwd=self.dir, timeout=timeout)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        return r.stdout.splitlines()

    def test_a_merge_ends_the_watch(self):
        opened = {"prs": [pr(7)]}
        self.answers([opened, opened, {"prs": [pr(7, "MERGED", updated=1100)]}])
        out = self.run_script("--repo", "o/n", "--limit", "1")
        self.assertIn("o/n", out[0])
        self.assertEqual(out[1:], ["GitHub: PR #7 merged: pr 7"])
        stages = ["check" if any("open: pullRequests(" in a for a in c) else "detail" for c in self.calls()]
        self.assertEqual(stages, ["check", "detail", "check", "check", "detail"])

    def test_a_thread_resolved_after_the_merge_is_reported(self):
        opened = {"prs": [pr(7, threads=[[41, False, 1]])]}
        merged = {"prs": [pr(7, "MERGED", updated=1100, threads=[[41, False, 1]])]}
        later = {"prs": [pr(7, "MERGED", updated=1100, threads=[[41, True, 1]])] +
                 [pr(n, "CLOSED", updated=2000 + n) for n in range(1, 7)]}  # 7 is no longer a recent one
        self.answers([opened, merged, later])
        self.assertEqual(self.run_script("--repo", "o/n", "--limit", "0.02")[1:], ["GitHub: PR #7 merged: pr 7"])
        out = self.run_script("--repo", "o/n", "--limit", "0.02")
        self.assertEqual(out[1:], ["GitHub: PR #7: decision thread resolved: https://github.com/o/n/pull/7#discussion_r41"])
        checks = [c for c in self.calls() if any("open: pullRequests(" in a for a in c)]
        self.assertTrue(any("w7: pullRequest(number: 7)" in a for a in checks[2]), checks[2])

    def test_a_comment_and_a_merge_in_one_read_each_get_their_own_line(self):
        # Issue #96, end to end: the human answers on the card's issue as its PR merges and closes it.
        asked = {"prs": [pr(2)], "issues": {"1": {"title": "Card", "state": "OPEN", "comments": 1, "updated": 900,
                                                  "times": [900]}}}
        answered = {"prs": [pr(2, "MERGED", updated=1100)],
                    "issues": {"1": {"title": "Card", "state": "CLOSED", "comments": 2, "updated": 1100,
                                     "times": [900, 1050]}}}
        self.answers([asked, answered])
        out = self.run_script("--repo", "o/n", "--issue", "1", "--limit", "1")
        self.assertEqual(out[1:], ["GitHub: PR #2 merged: pr 2", "GitHub: Issue #1: 1 new comment (Card)",
                                   "GitHub: Issue #1 is now closed (Card)"])

    def test_the_repo_comes_from_gh_when_not_given_and_issues_are_asked_for(self):
        self.answers([{"prs": [], "issues": {"25": {"title": "C", "state": "OPEN", "comments": 1, "updated": 1}}}])
        out = self.run_script("--limit", "0.005", "--issue", "25")
        self.assertIn("o/n", out[0])
        first = self.calls()[0]
        self.assertIn("owner=o", first)
        self.assertIn("name=n", first)
        self.assertTrue(any("i25: issue(number: 25)" in a for a in first))

    def test_gh_failing_still_ends_at_the_limit(self):
        self.answers(["fail"])
        out = self.run_script("--repo", "o/n", "--limit", "0.005")
        self.assertTrue(out[1].startswith("No change in"), out)
        self.assertIn("HTTP 502", "\n".join(out))

    def test_rate_limited_says_until_when_without_a_traceback(self):
        self.answers(["limited"])
        out = self.run_script("--repo", "o/n", "--limit", "0.005")
        self.assertIn("rate-limited until %s" % w.clock(1800000000), "\n".join(out))

    def test_it_lists_itself_while_it_runs_and_not_after(self):
        self.answers([{"prs": []}])
        proc = subprocess.Popen([sys.executable, SCRIPT, "--state", self.state, "--repo", "o/n", "--limit", "1",
                                 "--events", os.path.join(self.dir, "none.jsonl")], env=self.env, cwd=self.dir,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            listed = None
            for _ in range(100):
                r = subprocess.run([sys.executable, SCRIPT, "--list"], capture_output=True, text=True, env=self.env,
                                   timeout=30)
                if "o/n" in r.stdout and "read by a watcher" in r.stdout:
                    listed = r.stdout
                    break
                time.sleep(0.1)
            self.assertIsNotNone(listed)
            self.assertIn("1 watcher", listed)
            self.assertIn("o/n: every 2 min", listed)
            self.assertIn("pid %d" % proc.pid, listed)
            # The budget the watcher's own GraphQL answer gave, not gh api rate_limit's (4321).
            self.assertIn("4990 of 5000 points left", listed)
        finally:
            proc.terminate()
            proc.communicate(timeout=10)
        r = subprocess.run([sys.executable, SCRIPT, "--list"], capture_output=True, text=True, env=self.env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("No apb watchers", r.stdout)
        self.assertIn("4321 of 5000 points left", r.stdout)  # no watcher running: gh api rate_limit
        self.assertIn("from gh api rate_limit", r.stdout)

    def test_a_real_idle_process_is_reported(self):
        self.answers([{"prs": []}])
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
