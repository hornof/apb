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
with open(os.path.join(here, "mode")) as f:
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


def fake_watcher(mode):
    d = tempfile.mkdtemp()
    path = os.path.join(d, "fake-watch.py")
    with open(path, "w") as f:
        f.write(FAKE)
    set_mode(path, mode)
    return path


def set_mode(fake, mode):
    with open(os.path.join(os.path.dirname(fake), "mode"), "w") as f:
        f.write(mode)


def starts_of(fake):
    path = os.path.join(os.path.dirname(fake), "argv.log")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f.read().splitlines()]


def payload(event="PreToolUse", **extra):
    p = {"session_id": "s1", "transcript_path": "/x.jsonl", "cwd": "/somewhere", "hook_event_name": event}
    if event == "PreToolUse":
        p.update(tool_name="Bash", tool_input={"command": "ls"})
    p.update(extra)
    return p


def args(fake, budget=30, extra=()):
    return [sys.executable, SCRIPT, "--watch-script", fake, "--budget", str(budget), "--grace", "2",
            "--backoff", "0"] + list(extra)


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


def finish(proc):
    if proc.poll() is None:
        proc.terminate()
    proc.communicate(timeout=10)


def alive(pid):
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    # A finished child that nobody has collected yet still answers kill(pid, 0).
    out = subprocess.run(["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, timeout=10)
    return out.stdout.strip() not in ("", "Z", "Z+")


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


class Events(unittest.TestCase):
    def test_the_start_line_names_the_session_repo_and_issue(self):
        project = apb_project("# rules\n- coordination: hornof/apb#25\n")
        fake = fake_watcher("found")
        run(project, fake)
        [line] = events(project, "watcher-start")
        self.assertEqual(line["session"], "s1")
        self.assertEqual(line["repo"], "hornof/apb")
        self.assertEqual(line["issue"], 25)
        self.assertIsInstance(line["pid"], int)
        self.assertRegex(line["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z$")
        [done] = events(project, "watcher-exit")
        self.assertEqual(done["reason"], "found")

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

    def test_the_coordination_line_gives_the_issue_and_repo(self):
        argv, _ = self.argv("Rules\n- coordination: hornof/apb#25\n")
        self.assertEqual(argv[argv.index("--issue") + 1], "25")
        self.assertEqual(argv[argv.index("--repo") + 1], "hornof/apb")

    def test_a_coordination_line_without_a_repo_watches_this_repo(self):
        for text in ["coordination: #7\n", "Coordination: #7\n", "* **coordination:** #7 (where we talk)\n"]:
            argv, _ = self.argv(text)
            self.assertEqual(argv[argv.index("--issue") + 1], "7", text)
            self.assertNotIn("--repo", argv, text)

    def test_the_flag_wins_over_the_file(self):
        argv, _ = self.argv("coordination: #7\n", extra=["--coordination", "a/b#9"])
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


if __name__ == "__main__":
    unittest.main()
