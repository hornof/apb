"""Tests for apb-watch-start.py and its entries in hooks.json. Run from the repo root:
python3 -m unittest discover -s hooks -t hooks

Each test runs the hook the way Claude Code does: a JSON payload on stdin, the project folder in
CLAUDE_PROJECT_DIR. A fake watcher stands in for scripts/apb-watch.py: it records its arguments and
then does what the test asks (report something, report no change, fail, or hang).
"""
import importlib.util
import json
import os
import random
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "apb-watch-start.py")
HOOKS_JSON = os.path.join(HERE, "hooks.json")

FAKE = r'''
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "argv.log"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd(), "pid": os.getpid()}) + "\n")
name = "mode"
if "--repo" in sys.argv and os.path.exists(os.path.join(here, "mode-coordination")):
    name = "mode-coordination"  # the coordination watcher is the one given a --repo
with open(os.path.join(here, name)) as f:
    mode = f.read().split()
if mode[0] == "found":
    print("12:00:00 apb-watch (o/r):\nGitHub: Issue #25: 1 new comment (Coordination)", flush=True)
elif mode[0] == "nochange":
    print("12:00:00 apb-watch (o/r):\nNo change in 0 min.", flush=True)
elif mode[0] == "nochange-errors":
    print("12:00:00 apb-watch (o/r):\nNo change in 0 min.\n"
          "GitHub could not be read 3 times; last error: HTTP 401", flush=True)
elif mode[0] == "fail":
    sys.exit("apb-watch: no --repo given")
elif mode[0] == "sleep":
    time.sleep(float(mode[1]))
    print("12:00:00 apb-watch (o/r):\nGitHub: PR #3 merged: x", flush=True)
elif mode[0] == "hang":
    time.sleep(600)
'''


def apb_project(claude_md=None):
    """A temporary folder that looks like an apb project (it has docs/MAP.md)."""
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "docs", "MAP.md"), "w") as f:
        f.write("# map\n")
    if claude_md is not None:
        with open(os.path.join(d, "CLAUDE.md"), "w") as f:
            f.write(claude_md)
    return d


def apb_repo(manifest=None, hook=True):
    """A temporary folder that looks like the apb repo itself: the plugin's manifest naming "apb"
    (or the given manifest text) and this hook at hooks/apb-watch-start.py."""
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, ".claude-plugin"))
    if manifest is None:
        with open(os.path.join(os.path.dirname(HERE), ".claude-plugin", "plugin.json")) as f:
            manifest = f.read()  # the real one
    if manifest is not False:
        with open(os.path.join(d, ".claude-plugin", "plugin.json"), "w") as f:
            f.write(manifest)
    if hook:
        os.makedirs(os.path.join(d, "hooks"))
        with open(os.path.join(d, "hooks", "apb-watch-start.py"), "w") as f:
            f.write("# the hook\n")
    return d


def fake_watcher(mode, coordination=None):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "fake-watch.py")
    with open(path, "w") as f:
        f.write(FAKE)
    set_mode(path, mode, coordination)
    return path


def write_whole(path, text):
    """Write a file that another process may read at any moment: it sees the old text or the new,
    never an empty or half-written file (#136: a read between open and write gave -1 for an rc)."""
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)


def set_mode(fake, mode, coordination=None):
    """What the fake does; coordination, if given, is what the coordination watcher does instead."""
    write_whole(os.path.join(os.path.dirname(fake), "mode"), mode)
    if coordination is not None:
        write_whole(os.path.join(os.path.dirname(fake), "mode-coordination"), coordination)


def which(start):
    """'coordination' or 'project', from a fake watcher's recorded arguments."""
    return "coordination" if "--repo" in start["argv"] else "project"


def starts_of(fake):
    path = os.path.join(os.path.dirname(fake), "argv.log")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        text = f.read()
    # A fake that is writing its line right now has not reached the newline: it hasn't started yet.
    return [json.loads(line) for line in text.split("\n")[:-1]]


def payload(event="PreToolUse", **extra):
    p = {"session_id": "s1", "transcript_path": "/x.jsonl", "cwd": "/somewhere", "hook_event_name": event}
    if event == "PreToolUse":
        p.update(tool_name="Bash", tool_input={"command": "ls"})
    p.update(extra)
    return p


def args(fake, budget=30, extra=()):
    # --gh: no version check against GitHub here (test_apb_version.py covers it).
    return [sys.executable, SCRIPT, "--watch-script", fake, "--budget", str(budget), "--grace", "2",
            "--backoff", "0", "--gh", "/no/such/gh"] + list(extra)


def env_for(project):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if project is not None:
        env["CLAUDE_PROJECT_DIR"] = project
    return env


def run(project, fake, p=None, budget=30, extra=(), timeout=20):
    """Run the hook to the end. Returns the finished process."""
    data = json.dumps(p or payload()).encode()
    return subprocess.run(args(fake, budget, extra), input=data, capture_output=True, env=env_for(project),
                          cwd=tempfile.gettempdir(), timeout=timeout)


def start(project, fake, p=None, budget=30, extra=()):
    """Start the hook in the background, as Claude Code does with an asyncRewake hook."""
    with tempfile.TemporaryFile() as stdin:
        stdin.write(json.dumps(p or payload()).encode())
        stdin.seek(0)
        return subprocess.Popen(args(fake, budget, extra), stdin=stdin, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, env=env_for(project), cwd=tempfile.gettempdir())


def events(project, kind=None):
    path = os.path.join(project, ".apb", "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        out = [json.loads(line) for line in f.read().splitlines()]
    return [e for e in out if kind is None or e["event"] == kind]


def wait_for(check, seconds=10):
    end = time.time() + seconds
    while time.time() < end:
        if check():
            return True
        time.sleep(0.05)
    return False


def pid_file(project):
    return os.path.join(project, ".apb", "watcher.pid")


def coordination_pid_file(project):
    return os.path.join(project, ".apb", "watcher-coordination.pid")


def finish(proc):
    if proc.poll() is None:
        proc.terminate()
    proc.communicate(timeout=10)


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A finished child that nobody has collected yet still answers kill(pid, 0). Any state that starts
    # with Z is one, whatever flags follow: the hook's own rule (command_of), so the test and the hook
    # never disagree on whether a process has exited (#136).
    out = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, timeout=10)
    stat = out.stdout.strip()
    return bool(stat) and not stat.startswith("Z")


class StartsOnce(unittest.TestCase):
    def test_a_second_tool_call_does_not_start_a_second_watcher(self):
        project, fake = apb_project(), fake_watcher("sleep 4")
        first = start(project, fake)
        try:
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) == 1))
            t = time.time()
            second = run(project, fake)
            self.assertLess(time.time() - t, 3, "a second hook must return at once")
            self.assertEqual(second.returncode, 0)
            self.assertEqual(second.stderr, b"")
            time.sleep(0.3)
            self.assertEqual(len(starts_of(fake)), 1)
            self.assertEqual(len(events(project, "watcher-start")), 1)
        finally:
            finish(first)

    def test_hooks_fired_at_once_start_one_watcher(self):
        project, fake = apb_project(), fake_watcher("sleep 3")
        procs = [start(project, fake) for _ in range(6)]
        try:
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) >= 1))
            time.sleep(1)
            self.assertEqual(len(starts_of(fake)), 1)
            self.assertEqual(len(events(project, "watcher-start")), 1)
        finally:
            for p in procs:
                finish(p)

    def test_the_pid_file_names_claude_code_not_a_shell_between(self):
        # Run the hook through a shell, as Claude Code may. "; true" keeps the shell from exec'ing python,
        # so the hook's direct parent is the shell, and the first ancestor that isn't one is this test.
        project, fake = apb_project(), fake_watcher("sleep 3")
        with tempfile.NamedTemporaryFile("wb", delete=False) as f:
            f.write(json.dumps(payload()).encode())
        line = " ".join(shlex.quote(a) for a in args(fake)) + " < " + shlex.quote(f.name) + "; true"
        proc = subprocess.Popen(["/bin/sh", "-c", line], env=env_for(project), cwd=tempfile.gettempdir(),
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        try:
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) == 1))
            with open(pid_file(project)) as held:
                written = json.load(held)
            self.assertEqual(written["parent"], os.getpid())
            self.assertNotEqual(written["parent"], proc.pid)
        finally:
            finish(proc)
            os.remove(f.name)

    def test_a_stale_pid_file_does_not_block_a_start(self):
        project, fake = apb_project(), fake_watcher("found")
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(timeout=10)
        os.makedirs(os.path.join(project, ".apb"), exist_ok=True)
        with open(pid_file(project), "w") as f:
            json.dump({"pid": dead.pid, "parent": os.getpid(), "session": "s0"}, f)
        run(project, fake)
        self.assertEqual(len(starts_of(fake)), 1)

    def test_a_reused_pid_that_is_not_a_watcher_does_not_block_a_start(self):
        project, fake = apb_project(), fake_watcher("found")
        os.makedirs(os.path.join(project, ".apb"), exist_ok=True)
        with open(pid_file(project), "w") as f:  # this test's own process: alive, but not the hook
            json.dump({"pid": os.getpid(), "parent": os.getpid(), "session": "s0"}, f)
        run(project, fake)
        self.assertEqual(len(starts_of(fake)), 1)

    def test_a_garbled_pid_file_does_not_block_a_start(self):
        project, fake = apb_project(), fake_watcher("found")
        os.makedirs(os.path.join(project, ".apb"), exist_ok=True)
        with open(pid_file(project), "w") as f:
            f.write("not json")
        run(project, fake)
        self.assertEqual(len(starts_of(fake)), 1)

    def test_a_watcher_whose_session_is_gone_is_replaced(self):
        project, fake = apb_project(), fake_watcher("found")
        # A live process that looks like the hook, left behind by a Claude Code that has exited.
        orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "apb-watch-start.py"])
        dead = subprocess.Popen([sys.executable, "-c", "pass"])
        dead.wait(timeout=10)
        try:
            os.makedirs(os.path.join(project, ".apb"), exist_ok=True)
            with open(pid_file(project), "w") as f:
                json.dump({"pid": orphan.pid, "parent": dead.pid, "session": "s0"}, f)
            run(project, fake)
            self.assertEqual(len(starts_of(fake)), 1)
            orphan.wait(timeout=10)  # the hook stopped it
        finally:
            if orphan.poll() is None:
                orphan.kill()
                orphan.wait(timeout=10)

    def test_a_watcher_whose_session_is_alive_is_kept(self):
        project, fake = apb_project(), fake_watcher("found")
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "apb-watch-start.py"])
        try:
            os.makedirs(os.path.join(project, ".apb"), exist_ok=True)
            with open(pid_file(project), "w") as f:
                json.dump({"pid": other.pid, "parent": os.getpid(), "session": "s0"}, f)
            r = run(project, fake)
            self.assertEqual(r.returncode, 0)
            self.assertEqual(starts_of(fake), [])
            self.assertIsNone(other.poll())
        finally:
            other.kill()
            other.wait(timeout=10)


class Restarts(unittest.TestCase):
    def test_a_report_wakes_claude_and_the_next_call_starts_it_again(self):
        project, fake = apb_project(), fake_watcher("found")
        first = run(project, fake)
        self.assertEqual(first.returncode, 2, "exit 2 is what wakes the model")
        self.assertIn(b"Issue #25: 1 new comment", first.stderr)
        self.assertFalse(os.path.exists(pid_file(project)))
        second = run(project, fake)
        self.assertEqual(second.returncode, 2)
        self.assertEqual(len(starts_of(fake)), 2)
        self.assertEqual(len(events(project, "watcher-start")), 2)

    def test_no_change_runs_it_again_without_waking_claude(self):
        project, fake = apb_project(), fake_watcher("nochange")
        r = run(project, fake, budget=1.5)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, b"")
        self.assertGreaterEqual(len(starts_of(fake)), 2)
        self.assertEqual(len(events(project, "watcher-start")), len(starts_of(fake)))
        self.assertFalse(os.path.exists(pid_file(project)))

    def test_no_change_with_github_errors_runs_it_again_without_waking_claude(self):
        project, fake = apb_project(), fake_watcher("nochange-errors")
        r = run(project, fake, budget=1.5)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, b"")
        self.assertGreaterEqual(len(starts_of(fake)), 2)
        self.assertEqual({e["reason"] for e in events(project, "watcher-exit")}, {"no change"})

    def test_an_error_does_not_wake_claude_and_the_next_call_retries(self):
        project, fake = apb_project(), fake_watcher("fail")
        r = run(project, fake)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stderr, b"")
        exits = events(project, "watcher-exit")
        self.assertEqual(len(exits), 1)
        self.assertEqual(exits[0]["reason"], "error")
        self.assertIn("no --repo", exits[0]["detail"])
        set_mode(fake, "found")
        self.assertEqual(run(project, fake).returncode, 2)
        self.assertEqual(len(starts_of(fake)), 2)

    def test_after_an_error_it_waits_before_trying_again(self):
        project, fake = apb_project(), fake_watcher("fail")
        run(project, fake, extra=["--backoff", "60"])
        set_mode(fake, "found")
        again = run(project, fake, extra=["--backoff", "60"])
        self.assertEqual(again.returncode, 0)
        self.assertEqual(len(starts_of(fake)), 1)

    def test_a_watcher_that_overruns_its_limit_is_stopped(self):
        project, fake = apb_project(), fake_watcher("hang")
        t = time.time()
        r = run(project, fake, budget=0.5, extra=["--limit", "0.01"])
        self.assertLess(time.time() - t, 15)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(events(project, "watcher-exit")[0]["reason"], "timeout")
        self.assertFalse(alive(starts_of(fake)[0]["pid"]))

    def test_stopping_the_hook_stops_its_watcher_and_frees_the_pid_file(self):
        project, fake = apb_project(), fake_watcher("hang")
        proc = start(project, fake)
        self.assertTrue(wait_for(lambda: len(starts_of(fake)) == 1))
        child = starts_of(fake)[0]["pid"]
        proc.send_signal(signal.SIGTERM)
        proc.communicate(timeout=10)
        self.assertTrue(wait_for(lambda: not alive(child)))
        self.assertFalse(os.path.exists(pid_file(project)))


COORD = "# rules\n- coordination: hornof/apb#25\n"


class TwoWatchers(unittest.TestCase):
    """The coordination issue is in another repo: one watcher on the project's repo, one on that repo."""

    def test_each_watcher_starts_once(self):
        project, fake = apb_project(COORD), fake_watcher("sleep 4")
        first = start(project, fake)
        try:
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) == 2))
            t = time.time()
            second = run(project, fake)
            self.assertLess(time.time() - t, 3, "a second hook must return at once")
            self.assertEqual((second.returncode, second.stderr), (0, b""))
            time.sleep(0.3)
            self.assertEqual(sorted(which(s) for s in starts_of(fake)), ["coordination", "project"])
            self.assertEqual(sorted(e["watcher"] for e in events(project, "watcher-start")),
                             ["coordination", "project"])
            self.assertTrue(os.path.exists(pid_file(project)))
            self.assertTrue(os.path.exists(coordination_pid_file(project)))
        finally:
            finish(first)

    def test_hooks_fired_at_once_start_each_watcher_once(self):
        project, fake = apb_project(COORD), fake_watcher("sleep 3")
        procs = [start(project, fake) for _ in range(6)]
        try:
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) >= 2))
            time.sleep(1)
            self.assertEqual(sorted(which(s) for s in starts_of(fake)), ["coordination", "project"])
        finally:
            for p in procs:
                finish(p)

    def test_the_watchers_get_their_own_repo_issue_and_state(self):
        project, fake = apb_project(COORD), fake_watcher("hang", coordination="sleep 1")
        run(project, fake)
        by = {which(s): s["argv"] for s in starts_of(fake)}
        own, other = by["project"], by["coordination"]
        self.assertNotIn("--repo", own)
        self.assertNotIn("--issue", own)
        self.assertNotIn("--events", own)
        self.assertEqual(own[own.index("--state") + 1], os.path.join(project, ".apb", "watch.json"))
        self.assertEqual(other[other.index("--repo") + 1], "hornof/apb")
        self.assertEqual(other[other.index("--issue") + 1], "25")
        self.assertEqual(other[other.index("--state") + 1],
                         os.path.join(project, ".apb", "watch-coordination.json"))
        # Stuck commands and quiet agents are the project watcher's, so they wake Claude once.
        self.assertNotIn("--root-pid", other)
        self.assertFalse(os.path.exists(other[other.index("--events") + 1]))

    def test_a_report_from_the_project_watcher_wakes_claude_and_stops_the_other(self):
        project, fake = apb_project(COORD), fake_watcher("sleep 1", coordination="hang")
        r = run(project, fake)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"project watcher", r.stderr)
        self.assertIn(b"PR #3 merged", r.stderr)
        other = [s for s in starts_of(fake) if which(s) == "coordination"]
        self.assertEqual(len(other), 1)
        self.assertTrue(wait_for(lambda: not alive(other[0]["pid"])))
        self.assertFalse(os.path.exists(pid_file(project)))
        self.assertFalse(os.path.exists(coordination_pid_file(project)))

    def test_a_report_from_the_coordination_watcher_wakes_claude_and_stops_the_other(self):
        project, fake = apb_project(COORD), fake_watcher("hang", coordination="sleep 1")
        r = run(project, fake)
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"coordination watcher", r.stderr)
        own = [s for s in starts_of(fake) if which(s) == "project"]
        self.assertEqual(len(own), 1)
        self.assertTrue(wait_for(lambda: not alive(own[0]["pid"])))
        self.assertFalse(os.path.exists(pid_file(project)))
        self.assertFalse(os.path.exists(coordination_pid_file(project)))

    def test_both_start_again_after_a_wake(self):
        project, fake = apb_project(COORD), fake_watcher("hang", coordination="sleep 0.5")
        self.assertEqual(run(project, fake).returncode, 2)
        self.assertEqual(run(project, fake).returncode, 2)
        self.assertEqual(sorted(which(s) for s in starts_of(fake)),
                         ["coordination", "coordination", "project", "project"])

    def test_a_hook_takes_only_the_watcher_that_is_free(self):
        project, fake = apb_project(COORD), fake_watcher("hang", coordination="found")
        other = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", "apb-watch-start.py"])
        try:
            os.makedirs(os.path.join(project, ".apb"), exist_ok=True)
            with open(pid_file(project), "w") as f:  # the project watcher runs in another live hook
                json.dump({"pid": other.pid, "parent": os.getpid(), "session": "s1"}, f)
            r = run(project, fake)
            self.assertEqual(r.returncode, 2)
            self.assertEqual([which(s) for s in starts_of(fake)], ["coordination"])
            self.assertIsNone(other.poll())
        finally:
            other.kill()
            other.wait(timeout=10)

    def with_origin(self, project, url):
        subprocess.run(["git", "init", "-q", project], check=True, capture_output=True, timeout=30)
        subprocess.run(["git", "-C", project, "remote", "add", "origin", url], check=True,
                       capture_output=True, timeout=30)

    def test_a_line_naming_the_projects_own_repo_runs_one_watcher(self):
        for url in ["https://github.com/hornof/sketch-gallery.git", "git@github.com:hornof/sketch-gallery.git",
                    "ssh://git@github.com/Hornof/Sketch-Gallery", "https://github.com/hornof/sketch-gallery/"]:
            project = apb_project("- coordination: hornof/sketch-gallery#5\n")
            self.with_origin(project, url)
            fake = fake_watcher("found")
            self.assertEqual(run(project, fake).returncode, 2, url)
            [s] = starts_of(fake)
            self.assertEqual(s["argv"][s["argv"].index("--issue") + 1], "5", url)
            self.assertNotIn("--repo", s["argv"], url)
            self.assertEqual([e["watcher"] for e in events(project, "watcher-start")], ["project"], url)

    def test_a_line_naming_another_repo_still_runs_two(self):
        project = apb_project("- coordination: hornof/apb#25\n")
        self.with_origin(project, "https://github.com/hornof/sketch-gallery.git")
        fake = fake_watcher("hang", coordination="sleep 0.5")
        run(project, fake)
        self.assertEqual(sorted(which(s) for s in starts_of(fake)), ["coordination", "project"])

    def test_remote_urls_give_owner_and_name(self):
        hook = load_hook()
        cases = {"https://github.com/a/b.git\n": "a/b", "git@github.com:a/b.git": "a/b",
                 "ssh://git@github.com/a/b": "a/b", "https://github.com/a.x/b-y_z/": "a.x/b-y_z",
                 "": None, "not a url": None}
        for url, want in cases.items():
            self.assertEqual(hook.repo_of(url), want, url)

    def test_a_failing_watcher_leaves_the_other_running(self):
        project, fake = apb_project(COORD), fake_watcher("sleep 1", coordination="fail")
        r = run(project, fake, extra=["--backoff", "60"])
        self.assertEqual(r.returncode, 2)
        self.assertIn(b"project watcher", r.stderr)
        exits = {e["watcher"]: e["reason"] for e in events(project, "watcher-exit")}
        self.assertEqual(exits, {"coordination": "error", "project": "found"})
        self.assertTrue(os.path.exists(os.path.join(project, ".apb", "watcher-coordination.failed")))
        self.assertFalse(os.path.exists(os.path.join(project, ".apb", "watcher.failed")))
        # Within the backoff, the next hook starts only the project watcher.
        run(project, fake, extra=["--backoff", "60"])
        self.assertEqual(sorted(which(s) for s in starts_of(fake)), ["coordination", "project", "project"])


class Events(unittest.TestCase):
    def test_the_start_line_names_the_watcher_session_repo_and_issue(self):
        project = apb_project("# rules\n- coordination: #25\n")
        fake = fake_watcher("found")
        run(project, fake)
        [line] = events(project, "watcher-start")
        self.assertEqual(line["watcher"], "project")
        self.assertEqual(line["session"], "s1")
        self.assertIsNone(line["repo"])
        self.assertEqual(line["issue"], 25)
        self.assertIsInstance(line["pid"], int)
        self.assertRegex(line["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        [done] = events(project, "watcher-exit")
        self.assertEqual((done["watcher"], done["reason"]), ("project", "found"))

    def test_the_coordination_start_line_names_the_other_repo(self):
        project, fake = apb_project(COORD), fake_watcher("hang", coordination="sleep 0.5")
        run(project, fake)
        line = [e for e in events(project, "watcher-start") if e["watcher"] == "coordination"][0]
        self.assertEqual((line["repo"], line["issue"]), ("hornof/apb", 25))

    def test_the_apb_folder_ignores_itself_in_git(self):
        project, fake = apb_project(), fake_watcher("found")
        run(project, fake)
        with open(os.path.join(project, ".apb", ".gitignore")) as f:
            self.assertEqual(f.read(), "*\n")


class Arguments(unittest.TestCase):
    def argv(self, claude_md=None, extra=()):
        project, fake = apb_project(claude_md), fake_watcher("found")
        run(project, fake, extra=extra)
        [s] = starts_of(fake)
        self.assertEqual(os.path.realpath(s["cwd"]), os.path.realpath(project))
        return s["argv"], project

    def coordination_argv(self, claude_md, extra=()):
        project, fake = apb_project(claude_md), fake_watcher("hang", coordination="sleep 0.5")
        run(project, fake, extra=extra)
        return [s["argv"] for s in starts_of(fake) if which(s) == "coordination"]

    def test_a_coordination_line_with_a_repo_gives_the_second_watcher_its_issue_and_repo(self):
        [argv] = self.coordination_argv("Rules\n- coordination: hornof/apb#25\n")
        self.assertEqual(argv[argv.index("--issue") + 1], "25")
        self.assertEqual(argv[argv.index("--repo") + 1], "hornof/apb")

    def test_a_coordination_line_without_a_repo_watches_this_repo(self):
        for text in ["coordination: #7\n", "Coordination: #7\n", "* **coordination:** #7 (where we talk)\n"]:
            argv, _ = self.argv(text)
            self.assertEqual(argv[argv.index("--issue") + 1], "7", text)
            self.assertNotIn("--repo", argv, text)

    def test_the_flag_wins_over_the_file(self):
        [argv] = self.coordination_argv("coordination: #7\n", extra=["--coordination", "a/b#9"])
        self.assertEqual(argv[argv.index("--issue") + 1], "9")
        self.assertEqual(argv[argv.index("--repo") + 1], "a/b")

    def test_with_neither_it_runs_without_an_issue(self):
        argv, _ = self.argv("# nothing about coordination here\n")
        self.assertNotIn("--issue", argv)
        self.assertNotIn("--repo", argv)
        argv, _ = self.argv(None)
        self.assertNotIn("--issue", argv)

    def test_the_state_file_lives_in_the_apb_folder(self):
        argv, project = self.argv()
        self.assertEqual(argv[argv.index("--state") + 1], os.path.join(project, ".apb", "watch.json"))

    def test_the_watcher_gets_a_limit(self):
        argv, _ = self.argv()
        self.assertGreater(float(argv[argv.index("--limit") + 1]), 0)

    def test_hook_started_watchers_read_github_at_the_new_pace(self):
        # The hook passes no --poll, so its watchers get the watcher's own pace: every 2 min while the
        # repo is active, every 15 min once it has been quiet for an hour. Checked with the real watcher.
        watch = load_watch()
        for claude_md in (None, COORD):
            project, fake = apb_project(claude_md), fake_watcher("hang" if claude_md else "found",
                                                                 coordination="sleep 0.5")
            run(project, fake)
            for s in starts_of(fake):
                self.assertNotIn("--poll", s["argv"])
                opts = watch.parse_args(s["argv"])
                t = 10 ** 6
                self.assertEqual(watch.github_interval(t, t - 60, 1, 2, fixed=opts.poll), 120)
                self.assertEqual(watch.github_interval(t, t - 2 * 3600, 1, 2, fixed=opts.poll), 900)

    def test_an_idle_sessions_watchers_live_23_hours_as_the_playbook_says(self):
        hook = load_hook()
        self.assertEqual(hook.BUDGET, 23 * 3600)
        with open(os.path.join(os.path.dirname(HERE), "docs", "playbook.md"), encoding="utf-8") as f:
            playbook = f.read()
        self.assertIn("23 hours", playbook)
        self.assertIn("every 15 minutes", playbook)


def load_watch():
    spec = importlib.util.spec_from_file_location(
        "apb_watch", os.path.join(os.path.dirname(HERE), "scripts", "apb-watch.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def load_hook():
    spec = importlib.util.spec_from_file_location("apb_watch_start", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def oracle(text):
    """A second way to find the coordination line: by hand, line by line, without a regex."""
    for line in text.split("\n"):
        rest = line.lstrip(" \t>*+-")
        rest = rest.lstrip("*")
        if not rest.lower().startswith("coordination"):
            continue
        rest = rest[len("coordination"):].lstrip(":*").lstrip(" \t")
        if "#" not in rest:
            continue
        repo, _, tail = rest.partition("#")
        digits = ""
        for ch in tail:
            if not ch.isdigit():
                break
            digits += ch
        ok_repo = repo == "" or (repo.count("/") == 1 and all(
            part and all(c.isalnum() or c in "_.-" for c in part) for part in repo.split("/")))
        if digits and ok_repo:
            return (repo or None, int(digits))
    return (None, None)


class RandomFiles(unittest.TestCase):
    def test_random_claude_files_agree_with_a_second_reading(self):
        hook = load_hook()
        rng = random.Random(67)
        prefixes = ["", "- ", "* ", "  - ", "> ", "**", "- **", "+ "]
        names = ["coordination", "Coordination", "COORDINATION", "coordinate", "coordinations", "co-ordination"]
        colons = [":", "", ":**", "**:", " :"]
        repos = ["", "hornof/apb", "a.b/c-d", "x/y_z", "bad repo/x", "only"]
        tails = ["", " (where we talk)", "abc", " #3"]
        others = ["# Rules", "", "- issue #12 is open", "coordination is how we talk", "see #4"]
        for _ in range(3000):
            lines = []
            for _ in range(rng.randint(0, 4)):
                if rng.random() < 0.5:
                    lines.append(rng.choice(others))
                else:
                    num = rng.choice(["", str(rng.randint(0, 500))])
                    lines.append(rng.choice(prefixes) + rng.choice(names) + rng.choice(colons) + " "
                                 * rng.randint(0, 2) + rng.choice(repos) + "#" + num + rng.choice(tails))
            text = "\n".join(lines)
            self.assertEqual(hook.coordination(text), oracle(text), repr(text))


class TheApbRepo(unittest.TestCase):
    """The apb repo has no docs/MAP.md, but its sessions need waking on a merge too (#118)."""

    def test_the_apb_repo_gets_a_watcher_on_its_own_repo(self):
        project, fake = apb_repo(), fake_watcher("found")
        r = run(project, fake)
        self.assertEqual(r.returncode, 2, r.stderr)
        starts = starts_of(fake)
        self.assertEqual(len(starts), 1)
        self.assertEqual(starts[0]["cwd"], os.path.realpath(project))
        self.assertNotIn("--repo", starts[0]["argv"])  # the folder's own repo, as in a project
        self.assertEqual([e["watcher"] for e in events(project, "watcher-start")], ["project"])

    def test_it_starts_once_and_again_after_a_wake(self):
        project, fake = apb_repo(), fake_watcher("sleep 3")
        first = start(project, fake)
        try:
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) == 1))
            self.assertEqual(run(project, fake).returncode, 0)  # already running
            self.assertEqual(first.wait(timeout=15), 2)
        finally:
            finish(first)
        self.assertEqual(len(starts_of(fake)), 1)
        set_mode(fake, "found")
        self.assertEqual(run(project, fake).returncode, 2)
        self.assertEqual(len(starts_of(fake)), 2)

    def test_a_repo_that_is_neither_gets_nothing(self):
        cases = {
            "another plugin, with this hook copied in": (json.dumps({"name": "sketch-gallery"}), True),
            "another plugin, no hook": (json.dumps({"name": "other", "version": "1.0.0"}), False),
            "a manifest naming apb, without the hook": (json.dumps({"name": "apb"}), False),
            "the hook, without a manifest": (False, True),
            "a name that only starts with apb": (json.dumps({"name": "apb-tools"}), True),
            "a name in another case": (json.dumps({"name": "APB"}), True),
            "a garbled manifest": ('{"name": "apb"', True),
            "a manifest that is a list": ('["apb"]', True),
            "a name that is not a string": ('{"name": ["apb"]}', True),
        }
        for why, (manifest, hook) in cases.items():
            project, fake = apb_repo(manifest, hook), fake_watcher("found")
            r = run(project, fake)
            self.assertEqual(r.returncode, 0, why)
            self.assertEqual(starts_of(fake), [], why)
            self.assertFalse(os.path.exists(os.path.join(project, ".apb")), why)

    def test_random_manifests_agree_with_a_second_reading(self):
        spec = importlib.util.spec_from_file_location("apb_watch_start", SCRIPT)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)

        def oracle(text, has_hook):  # written apart from the hook: exactly the name "apb", and the hook
            try:
                data = json.loads(text)
            except ValueError:
                return False
            return has_hook and type(data) is dict and data.get("name") == "apb"

        rng = random.Random(118)
        names = ["apb", "APB", "apb ", " apb", "apb2", "", "sketch-gallery", "apb-tools", None, 1, ["apb"]]
        for _ in range(300):
            body = {}
            if rng.random() < 0.9:
                body["name"] = rng.choice(names)
            if rng.random() < 0.5:
                body["version"] = "0.%d" % rng.randint(0, 99)
            text = json.dumps(body)
            if rng.random() < 0.1:
                text = text[:rng.randint(0, len(text))]
            if rng.random() < 0.05:
                text = json.dumps([body])
            has_hook = rng.random() < 0.8
            project = apb_repo(text, has_hook)
            self.assertEqual(hook.is_apb_repo(project), oracle(text, has_hook), (text, has_hook))


class StaysOut(unittest.TestCase):
    def test_a_subagent_tool_call_starts_nothing(self):
        project, fake = apb_project(), fake_watcher("found")
        r = run(project, fake, payload(agent_id="a1", agent_type="apb:builder"))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(starts_of(fake), [])
        self.assertEqual(events(project), [])

    def test_a_folder_that_is_not_an_apb_project_gets_nothing(self):
        project, fake = tempfile.mkdtemp(), fake_watcher("found")
        r = run(project, fake)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(starts_of(fake), [])
        self.assertFalse(os.path.exists(os.path.join(project, ".apb")))

    def test_bad_input_gets_nothing(self):
        project, fake = apb_project(), fake_watcher("found")
        for data in [b"", b"not json", b"[1, 2]"]:
            r = subprocess.run(args(fake), input=data, capture_output=True, env=env_for(project), timeout=20)
            self.assertEqual(r.returncode, 0)
        self.assertEqual(starts_of(fake), [])

    def test_every_event_it_is_registered_for_can_start_it(self):
        for event in ["SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"]:
            project, fake = apb_project(), fake_watcher("found")
            self.assertEqual(run(project, fake, payload(event)).returncode, 2, event)


class HooksFile(unittest.TestCase):
    def entries(self):
        with open(HOOKS_JSON) as f:
            hooks = json.load(f)["hooks"]
        out = []
        for event, groups in hooks.items():
            for group in groups:
                for hook in group["hooks"]:
                    if hook["args"][0].endswith("/apb-watch-start.py"):
                        out.append((event, group.get("matcher"), hook))
        return out

    def test_it_runs_at_session_start_on_each_prompt_tool_call_and_stop(self):
        found = self.entries()
        self.assertEqual(sorted(e for e, _, _ in found), ["PreToolUse", "SessionStart", "Stop", "UserPromptSubmit"])
        for event, matcher, _ in found:
            self.assertIn(matcher, (None, "*", ""), event)

    def test_it_runs_in_the_background_and_can_wake_claude(self):
        for event, _, hook in self.entries():
            self.assertTrue(hook.get("asyncRewake"), event)
            self.assertNotIn("async", hook, event)
            self.assertEqual(hook["command"], "python3")
            self.assertEqual(hook["args"], ["${CLAUDE_PLUGIN_ROOT}/hooks/apb-watch-start.py"])
            # Claude Code ends the hook at its timeout; it must outlast the watcher's default budget.
            self.assertGreaterEqual(hook["timeout"], 24 * 3600)



# ---------------------------------------------------------------- only the orchestrator (#123)

def git(folder, *a):
    subprocess.run(["git", "-C", folder, "-c", "user.name=t", "-c", "user.email=t@t", *a], check=True,
                   capture_output=True, timeout=20)


def in_git(project):
    """Make project a git repo, its files committed, its main checkout on main."""
    git(project, "init", "-q", "-b", "main")
    git(project, "add", "-A")
    git(project, "commit", "-q", "--allow-empty", "-m", "start")
    return project


def worktree_of(project, path=None, branch="build-1"):
    """A linked worktree of project, at .claude/worktrees/<branch> unless a path is given."""
    path = path or os.path.join(project, ".claude", "worktrees", branch)
    git(project, "worktree", "add", "-q", "-b", branch, path)
    return path


class OnlyTheOrchestrator(unittest.TestCase):
    """Build and review sessions run in linked worktrees; only the main checkout's session, the
    orchestrator's, gets a watcher (#123)."""

    def test_the_main_checkout_gets_a_watcher(self):
        for make in (apb_project, apb_repo):
            project, fake = in_git(make()), fake_watcher("found")
            r = run(project, fake)
            self.assertEqual(r.returncode, 2, (make.__name__, r.stderr))
            self.assertEqual(len(starts_of(fake)), 1, make.__name__)

    def test_a_session_in_a_worktree_gets_none(self):
        for make in (apb_project, apb_repo):
            project = in_git(make())
            elsewhere = os.path.join(tempfile.mkdtemp(), "anywhere")
            for folder in (worktree_of(project), worktree_of(project, elsewhere, "review-1")):
                fake = fake_watcher("found")
                for event in ["SessionStart", "UserPromptSubmit", "PreToolUse", "Stop"]:
                    r = run(folder, fake, payload(event))
                    self.assertEqual(r.returncode, 0, (make.__name__, folder, event, r.stderr))
                self.assertEqual(starts_of(fake), [], folder)
                self.assertFalse(os.path.exists(os.path.join(folder, ".apb")), folder)

    def test_the_rule_is_gits_not_the_folder_name(self):
        # A repo's main checkout that happens to sit under .claude/worktrees still gets its watcher.
        parent = os.path.join(tempfile.mkdtemp(), ".claude", "worktrees")
        os.makedirs(parent)
        project = apb_project()
        moved = os.path.join(parent, "main-checkout")
        os.rename(project, moved)
        in_git(moved)
        fake = fake_watcher("found")
        self.assertEqual(run(moved, fake).returncode, 2)
        self.assertEqual(len(starts_of(fake)), 1)

    def test_a_folder_outside_git_still_gets_a_watcher(self):
        project, fake = apb_project(), fake_watcher("found")
        self.assertEqual(run(project, fake).returncode, 2)

    def test_the_orchestrator_and_two_build_sessions_run_one_watcher(self):
        project = in_git(apb_repo())
        builds = [worktree_of(project, branch="build-1"), worktree_of(project, branch="build-2")]
        fake = fake_watcher("sleep 4")
        procs = [start(project, fake, payload(session_id="orchestrator"))]
        procs += [start(b, fake, payload(session_id="build-%d" % i)) for i, b in enumerate(builds)]
        try:
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) >= 1))
            for p in procs[1:]:
                self.assertEqual(p.wait(timeout=15), 0)  # the build sessions' hooks end at once
            time.sleep(0.5)
            starts = starts_of(fake)
            self.assertEqual(len(starts), 1)
            self.assertEqual(starts[0]["cwd"], os.path.realpath(project))
            self.assertEqual([e["session"] for e in events(project, "watcher-start")], ["orchestrator"])
            # The merge the watcher reports wakes the orchestrator's hook alone.
            self.assertEqual(procs[0].wait(timeout=15), 2)
            self.assertIn(b"PR #3 merged", procs[0].stderr.read())
        finally:
            for p in procs:
                finish(p)

    def test_random_layouts_agree_with_a_second_reading(self):
        # The hook's worktree test against an oracle that reads .git directly: a linked worktree's
        # .git is a file pointing into the main repo's .git/worktrees/.
        spec = importlib.util.spec_from_file_location("apb_watch_start", SCRIPT)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)

        def oracle(folder):
            dot = os.path.join(folder, ".git")
            if not os.path.isfile(dot):
                return False
            with open(dot) as f:
                return "/worktrees/" in f.read()

        rng = random.Random(123)
        for i in range(12):
            project = apb_project() if rng.random() < 0.5 else apb_repo()
            if rng.random() < 0.2:
                self.assertEqual(hook.linked_worktree(project), oracle(project), project)
                continue
            in_git(project)
            folders = [project]
            for n in range(rng.randint(0, 2)):
                path = None if rng.random() < 0.5 else os.path.join(tempfile.mkdtemp(), "w")
                folders.append(worktree_of(project, path, "b%d-%d" % (i, n)))
            for folder in folders:
                self.assertEqual(hook.linked_worktree(folder), oracle(folder), folder)
                self.assertEqual(hook.linked_worktree(folder), folder != project, folder)


FAKE_CLAUDE = r'''
# Stands in for a Claude Code session: the hooks' root process. Each time the test writes go-<n> in
# its folder it starts the hook, as Claude Code starts an async hook, and when that hook ends it
# writes its exit code to rc-<n>. At "quit" it stops the hooks still running and exits.
import os, signal, subprocess, sys, time
here, hook = sys.argv[1], sys.argv[2:]
n, running, end = 1, {}, time.time() + 90
while time.time() < end and not os.path.exists(os.path.join(here, "quit")):
    if os.path.exists(os.path.join(here, "go-%d" % n)):
        with open(os.path.join(here, "payload.json"), "rb") as stdin:
            running[n] = subprocess.Popen(hook, stdin=stdin, stdout=subprocess.DEVNULL,
                                          stderr=subprocess.DEVNULL)
        n += 1
    for k, p in list(running.items()):
        if p.poll() is not None:
            # Written whole, then renamed: the test never reads an empty rc file (#136).
            with open(os.path.join(here, "rc-%d.tmp" % k), "w") as f:
                f.write(str(p.returncode))
            os.replace(os.path.join(here, "rc-%d.tmp" % k), os.path.join(here, "rc-%d" % k))
            del running[k]
    time.sleep(0.05)
for p in running.values():
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=10)
'''


class Session:
    """A fake Claude Code session in project: hooks run as its children, so it is their root."""

    def __init__(self, project, fake, session_id):
        self.dir = tempfile.mkdtemp()
        self.session_id = session_id
        with open(os.path.join(self.dir, "payload.json"), "w") as f:
            json.dump(payload(session_id=session_id), f)
        script = os.path.join(self.dir, "fake-claude.py")
        with open(script, "w") as f:
            f.write(FAKE_CLAUDE)
        self.proc = subprocess.Popen([sys.executable, script, self.dir] + args(fake, budget=60),
                                     env=env_for(project), cwd=tempfile.gettempdir())
        self.pid = self.proc.pid
        self.runs = 0

    def hook(self, event=None, **extra):
        """Run the hook once more; returns its number. With event, the payload is that event's, with
        extra (such as source="resume" at SessionStart); without, a tool call's."""
        p = payload(event or "PreToolUse", session_id=self.session_id, **extra)
        # A new file, not the old one rewritten: a hook still reading its payload keeps its own (#136).
        write_whole(os.path.join(self.dir, "payload.json"), json.dumps(p))
        self.runs += 1
        open(os.path.join(self.dir, "go-%d" % self.runs), "w").close()
        return self.runs

    def rc(self, n):
        path = os.path.join(self.dir, "rc-%d" % n)
        if not os.path.exists(path):
            return None
        with open(path) as f:
            return int(f.read() or "-1")

    def close(self):
        open(os.path.join(self.dir, "quit"), "w").close()
        try:
            self.proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=10)


def read_json(path):
    with open(path) as f:
        return json.load(f)


class NewestSessionTakesOver(unittest.TestCase):
    """After a plugin update the human opens a new orchestrator session while the old one is still
    open: the new one takes the watcher over, and the old one doesn't take it back (#123)."""

    def held(self, project):
        try:
            with open(pid_file(project)) as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def test_a_newer_session_takes_the_watcher_and_the_older_one_leaves_it(self):
        project, fake = apb_project(), fake_watcher("sleep 30")
        old = Session(project, fake, "old")
        new = None
        try:
            first = old.hook()
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) == 1))
            self.assertEqual(self.held(project)["parent"], old.pid)
            old_hook, old_watcher = self.held(project)["pid"], starts_of(fake)[0]["pid"]
            time.sleep(1.5)  # ps tells start times apart to the second
            new = Session(project, fake, "new")
            new.hook()
            asked = time.time()
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) == 2, 15))
            if alive(old_hook):
                # #136: seen once on GitHub's macOS runner (Python 3.15), never in local runs. Say what the
                # old hook was doing, so a repeat shows whether it was slow to exit (the hook waits 10 s at
                # most) or not stopped.
                ps = subprocess.run(["ps", "-o", "pid=,ppid=,stat=,etime=,command=", "-ww", "-p", str(old_hook)],
                                    capture_output=True, text=True, timeout=10).stdout.strip()
                self.fail("the old hook has not exited %.1f s after the new hook started, yet the new watcher "
                          "runs.\nold hook: %s\nevents: %s" % (time.time() - asked, ps, events(project)))
            # One hook run: the new session holds the pid file, and its watcher is the only one.
            held = self.held(project)
            self.assertEqual((held["parent"], held["session"]), (new.pid, "new"))
            argv = starts_of(fake)[1]["argv"]
            self.assertEqual(argv[argv.index("--root-pid") + 1], str(new.pid))
            self.assertTrue(wait_for(lambda: old.rc(first) is not None, 15))
            self.assertEqual(old.rc(first), 0, "the old session is not woken")
            self.assertTrue(wait_for(lambda: not alive(old_watcher)))
            self.assertTrue(alive(starts_of(fake)[1]["pid"]))
            self.assertEqual([e["session"] for e in events(project, "watcher-start")], ["old", "new"])
            # The old session's next hook leaves the watcher with the new one.
            again = old.hook()
            self.assertTrue(wait_for(lambda: old.rc(again) is not None, 15))
            self.assertEqual(old.rc(again), 0)
            self.assertEqual(len(starts_of(fake)), 2)
            self.assertEqual(self.held(project)["parent"], new.pid)
            self.assertTrue(alive(starts_of(fake)[1]["pid"]))
        finally:
            old.close()
            if new:
                new.close()
            for s in starts_of(fake):
                if alive(s["pid"]):
                    os.kill(s["pid"], signal.SIGTERM)

    def test_the_older_session_stays_out_after_the_newer_ones_hook_exits(self):
        # The newer session's hook ends (a wake, a failure, the budget) and frees the pid file; the
        # older session must still not take the watcher back while the newer Claude Code is open.
        project, fake = apb_project(), fake_watcher("sleep 30")
        old = Session(project, fake, "old")
        new = None
        try:
            old.hook()
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) == 1))
            time.sleep(1.5)
            set_mode(fake, "found")
            new = Session(project, fake, "new")
            woken = new.hook()
            self.assertTrue(wait_for(lambda: new.rc(woken) is not None, 15))
            self.assertEqual(new.rc(woken), 2)
            self.assertFalse(os.path.exists(pid_file(project)))
            again = old.hook()
            self.assertTrue(wait_for(lambda: old.rc(again) is not None, 15))
            self.assertEqual(old.rc(again), 0)
            self.assertEqual(len(starts_of(fake)), 2, "the older session started no watcher")
            # The newer session's next hook starts it again.
            back = new.hook()
            self.assertTrue(wait_for(lambda: new.rc(back) is not None, 15))
            self.assertEqual(len(starts_of(fake)), 3)
            # Once the newer session is closed, the older one gets the watcher again.
            new.close()
            last = old.hook()
            self.assertTrue(wait_for(lambda: old.rc(last) is not None, 15))
            self.assertEqual(old.rc(last), 2)
            self.assertEqual(len(starts_of(fake)), 4)
        finally:
            old.close()
            if new:
                new.close()
            for s in starts_of(fake):
                if alive(s["pid"]):
                    os.kill(s["pid"], signal.SIGTERM)

    def test_a_takeover_waits_for_the_old_hook_to_exit(self):
        # The old hook takes a moment to stop its watchers; the new one writes the pid file only after.
        spec = importlib.util.spec_from_file_location("apb_watch_start", SCRIPT)
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        slow = subprocess.Popen([sys.executable, "-c", "import signal, sys, time\n"
                                 "signal.signal(signal.SIGTERM, lambda *a: (time.sleep(1), sys.exit(0)))\n"
                                 "time.sleep(60)", "apb-watch-start.py"])
        try:
            self.assertTrue(wait_for(lambda: alive(slow.pid)))
            time.sleep(0.3)  # its SIGTERM handler is set
            folder = tempfile.mkdtemp()
            path = os.path.join(folder, "watcher.pid")
            with open(path, "w") as f:
                json.dump({"pid": slow.pid, "parent": os.getpid(), "session": "old"}, f)
            self.assertTrue(hook.claim(path, lambda: {"pid": os.getpid(), "parent": 1, "session": "new"},
                                       lambda held: True))
            self.assertIsNotNone(slow.poll(), "the old hook has exited when the claim returns")
            with open(path) as f:
                self.assertEqual(json.load(f)["session"], "new")
        finally:
            if slow.poll() is None:
                slow.kill()
            slow.wait(timeout=10)

    def stubborn(self):
        """A process that looks like the hook and doesn't exit on SIGTERM."""
        p = subprocess.Popen([sys.executable, "-c", "import signal, time\n"
                              "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                              "open(__import__('sys').argv[1], 'w').close()\n"
                              "time.sleep(60)", os.path.join(tempfile.mkdtemp(), "ready"), "apb-watch-start.py"])
        self.assertTrue(wait_for(lambda: os.path.exists(p.args[3])))  # its SIGTERM handler is set
        return p

    def test_a_takeover_is_refused_and_logged_while_the_old_hook_still_runs(self):
        # Issue #170: the hook used to give up waiting after 10 s and start a second watcher anyway.
        hook = load_hook()
        hook.TAKEOVER_WAIT = 1
        old = self.stubborn()
        try:
            folder = tempfile.mkdtemp()
            path = os.path.join(folder, "watcher.pid")
            with open(path, "w") as f:
                json.dump({"pid": old.pid, "parent": os.getpid(), "session": "old"}, f)
            t = time.time()
            self.assertFalse(hook.claim(path, lambda: {"pid": os.getpid(), "parent": 1, "session": "new"},
                                        lambda held: True))
            self.assertGreaterEqual(time.time() - t, 1, "it waited for the old hook first")
            self.assertIsNone(old.poll(), "the old hook still runs")
            with open(path) as f:
                self.assertEqual(json.load(f)["session"], "old", "the pid file still names the old hook")
            with open(os.path.join(folder, "events.jsonl")) as f:
                refused = [json.loads(l) for l in f.read().splitlines()]
            self.assertEqual([(e["event"], e["from"], e["holder"]) for e in refused],
                             [("watcher-takeover-refused", "old", old.pid)])
        finally:
            old.kill()
            old.wait(timeout=10)

    def test_a_failed_ps_is_not_taken_for_an_exit(self):
        # Issue #170, PR #164's Note 4: command_of gave "" when ps failed, and gone() took that as "exited".
        hook = load_hook()
        hook.TAKEOVER_WAIT = 1
        fake_ps = os.path.join(tempfile.mkdtemp(), "ps")
        with open(fake_ps, "w") as f:
            f.write("#!/bin/sh\necho 'ps: cannot read the process table' >&2\nexit 1\n")
        os.chmod(fake_ps, 0o755)
        old = self.stubborn()
        try:
            self.assertIn("apb-watch-start.py", hook.command_of(old.pid))
            for broken in (fake_ps, "/no/such/ps"):
                hook.PS = broken
                self.assertIsNone(hook.command_of(old.pid), broken)
                self.assertFalse(hook.gone(old.pid, 0.3), broken)
                folder = tempfile.mkdtemp()
                path = os.path.join(folder, "watcher.pid")
                with open(path, "w") as f:
                    json.dump({"pid": old.pid, "parent": os.getpid(), "session": "old"}, f)
                self.assertEqual(hook.holder(path)["pid"], old.pid, "a holder ps can't check still holds")
                self.assertFalse(hook.claim(path, lambda: {"pid": os.getpid(), "parent": 1, "session": "new"},
                                            lambda held: True), broken)
            hook.PS = "ps"
            dead = subprocess.Popen([sys.executable, "-c", "pass"])
            dead.wait(timeout=10)
            self.assertEqual(hook.command_of(dead.pid), "", "no such process: ps says it is gone")
            old.kill()
            old.wait(timeout=10)
            self.assertTrue(hook.gone(old.pid, 1))
        finally:
            if old.poll() is None:
                old.kill()
            old.wait(timeout=10)

    def test_a_second_hook_in_the_same_session_takes_nothing_over(self):
        project, fake = apb_project(), fake_watcher("sleep 30")
        s = Session(project, fake, "one")
        try:
            first = s.hook()
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) == 1))
            time.sleep(1.5)
            second = s.hook()  # the same session's next tool call, while the first hook runs
            self.assertTrue(wait_for(lambda: s.rc(second) is not None, 15))
            self.assertEqual(s.rc(second), 0)
            self.assertEqual(len(starts_of(fake)), 1)
            self.assertEqual(self.held(project)["parent"], s.pid)
            self.assertIsNone(s.rc(first), "the first hook still runs")
        finally:
            s.close()
            for st in starts_of(fake):
                if alive(st["pid"]):
                    os.kill(st["pid"], signal.SIGTERM)

    def test_the_owner_file_names_the_session_and_a_newer_one_takes_the_next_number(self):
        # Issue #133's comment: the owner file carries the session's name, so `apb-watch.py --list`
        # shows which named session holds the watcher; the newer session has the higher number.
        sys.path.insert(0, HERE)
        import apb_session
        project, fake = apb_project(), fake_watcher("sleep 30")
        version = read_json(os.path.join(os.path.dirname(HERE), ".claude-plugin", "plugin.json"))["version"]
        old, new = Session(project, fake, "old"), None
        owner = os.path.join(project, ".apb", "watcher-owner.json")
        try:
            old.hook()
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) == 1))
            with open(owner) as f:
                first = json.load(f)
            self.assertEqual(first["name"], apb_session.session_name(os.path.join(project, ".apb"), "old", "x"))
            self.assertTrue(first["name"].startswith("apb orch 1 · %s · " % version), first)
            time.sleep(1.5)
            new = Session(project, fake, "new")
            new.hook()
            self.assertTrue(wait_for(lambda: len(starts_of(fake)) == 2, 15))
            self.assertTrue(wait_for(lambda: read_json(owner).get("session") == "new"))
            self.assertTrue(read_json(owner)["name"].startswith("apb orch 2 · "))
        finally:
            old.close()
            if new:
                new.close()
            for st in starts_of(fake):
                if alive(st["pid"]):
                    os.kill(st["pid"], signal.SIGTERM)


def file_bytes(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


class ReopenedSessionTakesNothing(unittest.TestCase):
    """Issue #146: reopening an old session starts a new Claude Code process, so by start time it looked
    like the newest; it took the watcher and the next orchestrator number. A reopened session leaves an
    open holder alone and gets no number; it still starts the watcher when no open session holds it."""

    def setUp(self):
        sys.path.insert(0, HERE)
        import apb_session
        self.names = apb_session
        self.project, self.fake = apb_project(), fake_watcher("sleep 30")
        self.apb = os.path.join(self.project, ".apb")
        self.sessions = []

    def tearDown(self):
        for s in self.sessions:
            s.close()
        for st in starts_of(self.fake):
            if alive(st["pid"]):
                os.kill(st["pid"], signal.SIGTERM)

    def session(self, sid):
        s = Session(self.project, self.fake, sid)
        self.sessions.append(s)
        return s

    def ran(self, s, n):
        self.assertTrue(wait_for(lambda: s.rc(n) is not None, 15), "hook %d of %s ended" % (n, s.session_id))
        return s.rc(n)

    def held(self):
        try:
            return read_json(pid_file(self.project))
        except (OSError, ValueError):
            return None

    def owner(self):
        return read_json(os.path.join(self.apb, "watcher-owner.json"))

    def newest_holds(self):
        """A new session starts and holds the watcher, named apb orch 1."""
        new = self.session("orch1")
        new.hook("SessionStart", source="startup")
        self.assertTrue(wait_for(lambda: len(starts_of(self.fake)) == 1))
        self.assertTrue(wait_for(lambda: (self.owner() or {}).get("session") == "orch1"))
        self.assertTrue(self.owner()["name"].startswith("apb orch 1 · "))
        time.sleep(1.5)  # ps tells start times apart to the second: the reopened process starts later
        return new

    def assert_untouched(self, new, before):
        self.assertEqual(len(starts_of(self.fake)), 1, "the reopened session started no watcher")
        self.assertEqual(self.held()["parent"], new.pid)
        self.assertEqual(self.owner()["session"], "orch1")
        self.assertEqual(file_bytes(os.path.join(self.apb, "sessions.json")), before, "sessions.json unchanged")
        self.assertEqual(events(self.project, "watcher-takeover"), [])
        self.assertTrue(alive(starts_of(self.fake)[0]["pid"]), "the holder's watcher still runs")

    def test_a_reopened_old_session_leaves_a_live_newer_holder_alone(self):
        new = self.newest_holds()
        before = file_bytes(os.path.join(self.apb, "sessions.json"))
        old = self.session("bf4e8d74")
        self.assertEqual(self.ran(old, old.hook("SessionStart", source="resume")), 0)
        self.assertEqual(self.ran(old, old.hook("UserPromptSubmit")), 0)
        self.assertEqual(self.ran(old, old.hook()), 0, "a later tool call, which carries no source")
        self.assert_untouched(new, before)
        self.assertIsNone(self.names.session_name(self.apb, "bf4e8d74", "x", assign=False), "no number")

    def test_it_stays_out_while_the_holders_hook_is_between_runs(self):
        new = self.newest_holds()
        old = self.session("bf4e8d74")
        self.assertEqual(self.ran(old, old.hook("SessionStart", source="resume")), 0)
        # The holder's watcher ends; its hook exits and frees the pid file.
        os.kill(starts_of(self.fake)[0]["pid"], signal.SIGTERM)
        self.assertTrue(wait_for(lambda: not os.path.exists(pid_file(self.project)), 15))
        before = file_bytes(os.path.join(self.apb, "sessions.json"))
        self.assertEqual(self.ran(old, old.hook()), 0)
        self.assertEqual(len(starts_of(self.fake)), 1, "the reopened session started no watcher")
        self.assertEqual(self.owner()["session"], "orch1")
        self.assertEqual(file_bytes(os.path.join(self.apb, "sessions.json")), before)
        self.assertEqual(events(self.project, "watcher-takeover"), [])

    def test_a_session_first_active_before_the_holder_counts_as_reopened(self):
        # Without the SessionStart source (an older plugin at that start, say): its first line in the
        # events file comes before the holder's.
        os.makedirs(self.apb)
        with open(os.path.join(self.apb, "events.jsonl"), "w") as f:
            f.write(json.dumps({"time": "2026-11-21T17:31:00.000Z", "event": "Stop", "session": "bf4e8d74"}) + "\n")
        new = self.newest_holds()
        before = file_bytes(os.path.join(self.apb, "sessions.json"))
        old = self.session("bf4e8d74")
        self.assertEqual(self.ran(old, old.hook()), 0)
        self.assert_untouched(new, before)

    def test_a_new_session_still_takes_over_and_gets_the_next_number(self):
        new = self.newest_holds()
        old = self.session("bf4e8d74")
        self.assertEqual(self.ran(old, old.hook("SessionStart", source="resume")), 0)
        time.sleep(1.5)
        newest = self.session("orch2")
        newest.hook("SessionStart", source="startup")
        self.assertTrue(wait_for(lambda: len(starts_of(self.fake)) == 2, 15))
        self.assertTrue(wait_for(lambda: (self.owner() or {}).get("session") == "orch2"))
        self.assertTrue(self.owner()["name"].startswith("apb orch 2 · "), "the reopened one used no number")
        self.assertEqual(self.held()["parent"], newest.pid)
        self.assertEqual([(e["session"], e["from"]) for e in events(self.project, "watcher-takeover")],
                         [("orch2", "orch1")])

    def test_a_reopened_session_with_no_live_holder_starts_the_watcher(self):
        gone = self.newest_holds()
        gone.close()
        self.assertTrue(wait_for(lambda: not alive(starts_of(self.fake)[0]["pid"]), 15))
        before = file_bytes(os.path.join(self.apb, "sessions.json"))
        old = self.session("bf4e8d74")
        old.hook("SessionStart", source="resume")
        self.assertTrue(wait_for(lambda: len(starts_of(self.fake)) == 2, 15))
        self.assertEqual((self.held()["parent"], self.held()["session"]), (old.pid, "bf4e8d74"))
        self.assertTrue(wait_for(lambda: (self.owner() or {}).get("session") == "bf4e8d74"))
        self.assertIsNone(self.owner()["name"], "it holds the watcher but gets no number")
        self.assertEqual(file_bytes(os.path.join(self.apb, "sessions.json")), before)

    def test_a_reopened_session_keeps_the_name_it_had(self):
        os.makedirs(self.apb)
        named = self.names.session_name(self.apb, "bf4e8d74", "0.3.1")
        old = self.session("bf4e8d74")
        old.hook("SessionStart", source="resume")
        self.assertTrue(wait_for(lambda: len(starts_of(self.fake)) == 1, 15))
        self.assertTrue(wait_for(lambda: (self.owner() or {}).get("session") == "bf4e8d74"))
        self.assertEqual(self.owner()["name"], named)


if __name__ == "__main__":
    unittest.main()
