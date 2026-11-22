"""Tests for apb-version.py (issue #128): say when the apb plugin or the apb checkout is older than main.
Run from the repo root: python3 -m unittest discover -s hooks -t hooks

The card's "Done when":
- a session started on an older plugin than main says so in its first message: the SessionStart hook
  gives Claude a note with the two versions, the two update commands and "then open a new session";
- and again after the next merge to main: when a watcher wakes the orchestrator with a merge, the wake
  carries the same note;
- a session in an apb checkout that is off main or behind it says so in its first message, with the
  branch name and the commands to switch and pull; the hook never switches or pulls;
- a session on the current version and an up-to-date main says nothing.

Each hook test runs the script the way Claude Code does: a JSON payload on stdin, the folder in
CLAUDE_PROJECT_DIR. A fake gh stands in for GitHub (--gh): it prints main's manifest from a file,
fails, or hangs, and records each call. The checkout tests use real git repos with a bare origin.
"""
import importlib.util
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(HERE, "apb-version.py")
WATCH_START = os.path.join(HERE, "apb-watch-start.py")
HOOKS_JSON = os.path.join(HERE, "hooks.json")

UPDATE = "claude plugin marketplace update apb"
UPDATE_PLUGIN = "claude plugin update apb@apb"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


version = load("apb_version_under_test", SCRIPT)

CREATED = []


def tmp():
    d = tempfile.mkdtemp()
    CREATED.append(d)
    return d


FAKE_GH = r'''
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
with open(os.path.join(here, "mode")) as f:
    mode = f.read()
if mode == "fail":
    sys.exit("gh: HTTP 404")
if mode == "hang":
    time.sleep(60)
sys.stdout.write(mode)
'''


def fake_gh(main_version):
    """A fake gh that prints a manifest with main_version, or fails ("fail"), or hangs ("hang"), or
    prints the given text as is (a string starting with "raw:")."""
    d = tmp()
    path = os.path.join(d, "gh")
    with open(path, "w") as f:
        f.write("#!%s\n%s" % (sys.executable, FAKE_GH))
    os.chmod(path, 0o755)
    set_gh(path, main_version)
    return path


def set_gh(gh, main_version):
    if main_version in ("fail", "hang"):
        text = main_version
    elif main_version.startswith("raw:"):
        text = main_version[4:]
    else:
        text = json.dumps({"name": "apb", "version": main_version, "repository": "https://github.com/hornof/apb"})
    with open(os.path.join(os.path.dirname(gh), "mode"), "w") as f:
        f.write(text)


def gh_calls(gh):
    path = os.path.join(os.path.dirname(gh), "calls.log")
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f.read().splitlines()]


def plugin_root(installed):
    """A folder standing in for the installed plugin: its .claude-plugin/plugin.json gives the version."""
    d = tmp()
    os.makedirs(os.path.join(d, ".claude-plugin"))
    with open(os.path.join(d, ".claude-plugin", "plugin.json"), "w") as f:
        json.dump({"name": "apb", "version": installed, "repository": "https://github.com/hornof/apb"}, f)
    return d


def apb_project():
    d = tmp()
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "docs", "MAP.md"), "w") as f:
        f.write("# map\n")
    return d


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=30,
                       env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    if r.returncode:
        raise AssertionError("git %s: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


def commit(cwd, name, text="x\n"):
    path = os.path.join(cwd, name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)
    git(cwd, "add", name)
    git(cwd, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", name)


def apb_checkout(project=False):
    """A clone of a bare origin whose main looks like the apb repo (or, with project, an apb project).
    Returns (clone, origin)."""
    origin = os.path.join(tmp(), "origin.git")
    git(tmp(), "init", "-q", "--bare", "-b", "main", origin)
    seed = os.path.join(tmp(), "seed")
    git(tmp(), "clone", "-q", origin, seed)
    git(seed, "symbolic-ref", "HEAD", "refs/heads/main")
    if project:
        commit(seed, "docs/MAP.md", "# map\n")
    else:
        commit(seed, ".claude-plugin/plugin.json", json.dumps({"name": "apb", "version": "0.3.2"}))
        commit(seed, "hooks/apb-watch-start.py", "# the hook\n")
    git(seed, "push", "-q", "origin", "main")
    clone = os.path.join(tmp(), "apb")
    git(tmp(), "clone", "-q", origin, clone)
    return clone, origin


def push_to_origin(origin, n):
    """n new commits on origin's main, from another clone."""
    other = os.path.join(tmp(), "other")
    git(tmp(), "clone", "-q", origin, other)
    for i in range(n):
        commit(other, "later-%d.txt" % i)
    git(other, "push", "-q", "origin", "main")


def payload(event="SessionStart", **extra):
    p = {"session_id": "s1", "transcript_path": "/x.jsonl", "cwd": "/somewhere", "hook_event_name": event,
         "source": "startup"}
    p.update(extra)
    return p


def run(project, gh, installed="0.3.50", p=None, raw=None, timeout_arg=None):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if project is not None:
        env["CLAUDE_PROJECT_DIR"] = project
    cmd = [sys.executable, SCRIPT, "--gh", gh, "--plugin-root", plugin_root(installed)]
    if timeout_arg is not None:
        cmd += ["--timeout", str(timeout_arg)]
    data = raw if raw is not None else json.dumps(p or payload()).encode()
    return subprocess.run(cmd, input=data, capture_output=True, env=env, cwd=tempfile.gettempdir(), timeout=60)


def note_of(r):
    """The note the hook gives Claude at session start, or None if it said nothing."""
    out = r.stdout.decode().strip()
    if not out:
        return None
    data = json.loads(out)
    hso = data["hookSpecificOutput"]
    assert hso["hookEventName"] == "SessionStart", hso
    return hso["additionalContext"]


class Clean(unittest.TestCase):
    def tearDown(self):
        while CREATED:
            shutil.rmtree(CREATED.pop(), ignore_errors=True)


# ---------------------------------------------------------------- comparing versions

def oracle_newer(a, b):
    """True if version a is newer than b, written apart from the hook: compare the dotted numbers
    one by one, a missing part counting as 0."""
    pa, pb = [int(x) for x in a.split(".")], [int(x) for x in b.split(".")]
    n = max(len(pa), len(pb))
    pa, pb = pa + [0] * (n - len(pa)), pb + [0] * (n - len(pb))
    for x, y in zip(pa, pb):
        if x != y:
            return x > y
    return False


class Versions(Clean):
    def test_newer_matches_the_oracle_both_ways(self):
        rng = random.Random(128)
        for _ in range(2000):
            a = ".".join(str(rng.randint(0, 12)) for _ in range(rng.choice([1, 2, 3, 3, 3])))
            b = ".".join(str(rng.randint(0, 12)) for _ in range(rng.choice([1, 2, 3, 3, 3])))
            self.assertEqual(version.newer(a, b), oracle_newer(a, b), (a, b))
            self.assertFalse(version.newer(a, b) and version.newer(b, a), (a, b))

    def test_numbers_not_text(self):
        self.assertTrue(version.newer("0.3.10", "0.3.9"))
        self.assertFalse(version.newer("0.3.9", "0.3.10"))
        self.assertFalse(version.newer("0.3.57", "0.3.57"))

    def test_unreadable_versions_are_never_newer(self):
        for bad in [None, "", "abc", "1.x.3", 5, ["0.3.57"]]:
            self.assertFalse(version.newer(bad, "0.3.50"), bad)
            self.assertFalse(version.newer("0.3.50", bad), bad)

    def test_the_repo_comes_from_the_manifest(self):
        self.assertEqual(version.repo_of_manifest({"repository": "https://github.com/hornof/apb"}), "hornof/apb")
        self.assertEqual(version.repo_of_manifest({"repository": "git@github.com:o/r.git"}), "o/r")
        self.assertEqual(version.repo_of_manifest({"repository": {"url": "https://github.com/o/r"}}), "o/r")
        self.assertEqual(version.repo_of_manifest({}), "hornof/apb")


# ---------------------------------------------------------------- the plugin at session start

class PluginAtStart(Clean):
    def test_an_older_plugin_says_so_with_both_commands(self):
        project, gh = apb_project(), fake_gh("0.3.55")
        r = run(project, gh, installed="0.3.50")
        self.assertEqual(r.returncode, 0, r.stderr)
        note = note_of(r)
        self.assertIsNotNone(note)
        for part in ["0.3.50", "0.3.55", UPDATE, UPDATE_PLUGIN, "then open a new session"]:
            self.assertIn(part, note)
        self.assertLess(note.index(UPDATE), note.index(UPDATE_PLUGIN))
        # It asked GitHub for main's manifest, in the repo the installed manifest names.
        calls = gh_calls(gh)
        self.assertEqual(len(calls), 1)
        self.assertIn("repos/hornof/apb/contents/.claude-plugin/plugin.json?ref=main", " ".join(calls[0]))

    def test_the_current_plugin_says_nothing(self):
        for installed, main in [("0.3.57", "0.3.57"), ("0.3.58", "0.3.57"), ("1.0.0", "0.9.99")]:
            r = run(apb_project(), fake_gh(main), installed=installed)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIsNone(note_of(r), (installed, main))
            self.assertEqual(r.stderr, b"")

    def test_random_versions_say_something_exactly_when_main_is_newer(self):
        rng = random.Random(1281)
        gh = fake_gh("0.0.0")
        for i in range(12):
            installed = "0.%d.%d" % (rng.randint(2, 4), rng.randint(0, 60))
            main = "0.%d.%d" % (rng.randint(2, 4), rng.randint(0, 60))
            set_gh(gh, main)
            note = note_of(run(apb_project(), gh, installed=installed))
            self.assertEqual(note is not None, oracle_newer(main, installed), (installed, main, note))

    def test_github_unreadable_says_nothing_and_never_gets_in_the_way(self):
        for mode in ["fail", "raw:not json", "raw:[]", 'raw:{"name": "apb"}', 'raw:{"version": "x.y"}']:
            r = run(apb_project(), fake_gh(mode))
            self.assertEqual(r.returncode, 0, (mode, r.stderr))
            self.assertIsNone(note_of(r), mode)

    def test_a_hanging_gh_is_stopped(self):
        t = time.time()
        r = run(apb_project(), fake_gh("hang"), timeout_arg=1)
        self.assertLess(time.time() - t, 15)
        self.assertEqual(r.returncode, 0)
        self.assertIsNone(note_of(r))

    def test_no_gh_at_all_says_nothing(self):
        r = run(apb_project(), "/no/such/gh")
        self.assertEqual(r.returncode, 0)
        self.assertIsNone(note_of(r))

    def test_works_in_the_apb_repo_too(self):
        clone, _ = apb_checkout()
        note = note_of(run(clone, fake_gh("0.3.55"), installed="0.3.50"))
        self.assertIn("0.3.55", note)

    def test_does_nothing_outside_an_apb_folder(self):
        d, gh = tmp(), fake_gh("9.9.9")
        r = run(d, gh)
        self.assertEqual(r.returncode, 0)
        self.assertIsNone(note_of(r))
        self.assertEqual(gh_calls(gh), [])  # not even a call to GitHub
        self.assertFalse(os.path.exists(os.path.join(d, ".apb")))

    def test_does_nothing_for_an_agent(self):
        gh = fake_gh("9.9.9")
        r = run(apb_project(), gh, p=payload(agent_id="a1", agent_type="apb:builder"))
        self.assertIsNone(note_of(r))
        self.assertEqual(gh_calls(gh), [])

    def test_bad_input_exits_0_quietly(self):
        for raw in [b"", b"not json", b"[]", b"{}"]:
            r = run(apb_project(), fake_gh("9.9.9"), raw=raw)
            self.assertEqual(r.returncode, 0, (raw, r.stderr))
        r = run(apb_project(), fake_gh("9.9.9"), raw=b"{}")
        self.assertIsNotNone(note_of(r))  # an empty payload is still the main session


# ---------------------------------------------------------------- the apb checkout at session start

class CheckoutAtStart(Clean):
    def test_an_up_to_date_main_and_current_plugin_say_nothing(self):
        clone, _ = apb_checkout()
        r = run(clone, fake_gh("0.3.57"), installed="0.3.57")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIsNone(note_of(r))

    def test_off_main_says_the_branch_and_how_to_switch_and_never_switches(self):
        clone, _ = apb_checkout()
        git(clone, "checkout", "-q", "-b", "map-stays-current")
        with open(os.path.join(clone, "work.txt"), "w") as f:
            f.write("uncommitted\n")
        note = note_of(run(clone, fake_gh("0.3.57"), installed="0.3.57"))
        self.assertIsNotNone(note)
        for part in ["map-stays-current", "git switch main", "git pull"]:
            self.assertIn(part, note)
        self.assertLess(note.index("git switch main"), note.index("git pull"))
        self.assertEqual(git(clone, "branch", "--show-current"), "map-stays-current")
        with open(os.path.join(clone, "work.txt")) as f:
            self.assertEqual(f.read(), "uncommitted\n")

    def test_main_behind_origin_says_so_and_never_pulls(self):
        clone, origin = apb_checkout()
        before = git(clone, "rev-parse", "main")
        push_to_origin(origin, 2)  # the clone hasn't fetched: the hook must look for itself
        note = note_of(run(clone, fake_gh("0.3.57"), installed="0.3.57"))
        self.assertIsNotNone(note)
        self.assertIn("behind", note)
        self.assertIn("2 commits", note)
        self.assertIn("git pull", note)
        self.assertNotIn("git switch", note)
        self.assertEqual(git(clone, "rev-parse", "main"), before)
        self.assertEqual(git(clone, "branch", "--show-current"), "main")

    def test_ahead_of_origin_is_not_behind(self):
        clone, _ = apb_checkout()
        commit(clone, "local.txt")
        self.assertIsNone(note_of(run(clone, fake_gh("0.3.57"), installed="0.3.57")))

    def test_off_main_with_main_behind_says_switch_then_pull(self):
        clone, origin = apb_checkout()
        git(clone, "checkout", "-q", "-b", "old")
        push_to_origin(origin, 1)
        note = note_of(run(clone, fake_gh("0.3.57"), installed="0.3.57"))
        self.assertIn("`old`", note)
        self.assertIn("git switch main", note)
        self.assertIn("git pull", note)

    def test_a_detached_head_is_off_main(self):
        clone, _ = apb_checkout()
        git(clone, "checkout", "-q", "--detach")
        note = note_of(run(clone, fake_gh("0.3.57"), installed="0.3.57"))
        self.assertIn("git switch main", note)

    def test_plugin_and_checkout_both_said_in_one_note(self):
        clone, _ = apb_checkout()
        git(clone, "checkout", "-q", "-b", "map-stays-current")
        note = note_of(run(clone, fake_gh("0.3.55"), installed="0.3.50"))
        for part in ["0.3.50", "0.3.55", UPDATE, "map-stays-current", "git switch main"]:
            self.assertIn(part, note)

    def test_a_linked_worktree_is_left_alone(self):
        # Claude Code makes worktrees on their own branches on purpose; "git switch main" fails there.
        clone, _ = apb_checkout()
        wt = os.path.join(tmp(), "wt")
        git(clone, "worktree", "add", "-q", "-b", "worktree-agent-1", wt)
        self.assertIsNone(note_of(run(wt, fake_gh("0.3.57"), installed="0.3.57")))

    def test_an_apb_project_on_a_branch_is_not_checked(self):
        # The checkout check is for the apb repo only; a project's branches are its own business.
        clone, _ = apb_checkout(project=True)
        git(clone, "checkout", "-q", "-b", "feature")
        self.assertIsNone(note_of(run(clone, fake_gh("0.3.57"), installed="0.3.57")))

    def test_no_origin_says_nothing_about_the_checkout(self):
        clone, _ = apb_checkout()
        git(clone, "remote", "remove", "origin")
        self.assertIsNone(note_of(run(clone, fake_gh("0.3.57"), installed="0.3.57")))


def old_branch(clone, name="map-stays-current", manifest=None):
    """Check out a branch made before hooks/ existed: only an old plugin.json, no hooks/ folder."""
    git(clone, "checkout", "-q", "--orphan", name)
    git(clone, "rm", "-rq", "--cached", ".")
    shutil.rmtree(os.path.join(clone, "hooks"))
    os.remove(os.path.join(clone, ".claude-plugin", "plugin.json"))
    commit(clone, ".claude-plugin/plugin.json",
           manifest if manifest is not None else json.dumps({"name": "apb", "version": "0.3.2"}))


class OldBranchAtStart(Clean):
    """The card's own case (review of #137): the checkout is on a branch from before hooks/ existed, so
    the folder rule other hooks use doesn't see the apb repo. main still says it is the apb repo."""

    def test_the_cards_case_says_the_branch_and_the_plugin(self):
        clone, _ = apb_checkout()
        old_branch(clone)
        self.assertFalse(os.path.exists(os.path.join(clone, "hooks")))
        note = note_of(run(clone, fake_gh("0.3.55"), installed="0.3.50"))
        self.assertIsNotNone(note)
        for part in ["map-stays-current", "git switch main", "git pull", "0.3.50", "0.3.55", UPDATE]:
            self.assertIn(part, note)
        self.assertEqual(git(clone, "branch", "--show-current"), "map-stays-current")

    def test_the_cards_case_on_the_current_plugin_still_says_the_branch(self):
        clone, _ = apb_checkout()
        old_branch(clone)
        note = note_of(run(clone, fake_gh("0.3.57"), installed="0.3.57"))
        self.assertIn("map-stays-current", note)
        self.assertNotIn(UPDATE, note)

    def test_a_repo_whose_main_is_not_apb_says_nothing(self):
        # Another plugin's repo: main has the hook file but names another plugin.
        clone, _ = apb_checkout()
        with open(os.path.join(clone, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "other"}, f)
        git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "other")
        git(clone, "push", "-q", "origin", "main")
        old_branch(clone, "feature", manifest=json.dumps({"name": "other"}))
        gh = fake_gh("9.9.9")
        self.assertIsNone(note_of(run(clone, gh)))
        self.assertEqual(gh_calls(gh), [])

    def test_a_repo_whose_main_has_no_hook_file_says_nothing(self):
        clone, _ = apb_checkout()
        git(clone, "rm", "-rq", "hooks")
        git(clone, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qm", "no hooks")
        git(clone, "push", "-q", "origin", "main")
        git(clone, "checkout", "-q", "-b", "feature")
        gh = fake_gh("9.9.9")
        self.assertIsNone(note_of(run(clone, gh)))
        self.assertEqual(gh_calls(gh), [])


# ---------------------------------------------------------------- after a merge to main

def report(merge=True):
    lines = ["12:00:00 apb-watch (o/r):"]
    lines.append("GitHub: PR #3 merged: x" if merge else "GitHub: Issue #25: 1 new comment (Coordination)")
    return "\n".join(lines)


class AtAWake(Clean):
    def wake(self, project, gh, out, session="s1", installed="0.3.50"):
        return version.wake_note(project, session, out, gh=gh, root=plugin_root(installed), timeout=10)

    def test_a_merge_with_an_older_plugin_says_so_again(self):
        project, gh = apb_project(), fake_gh("0.3.55")
        run(project, gh, installed="0.3.50")  # said at session start
        note = self.wake(project, gh, report(merge=True))
        for part in ["0.3.50", "0.3.55", UPDATE, UPDATE_PLUGIN, "then open a new session"]:
            self.assertIn(part, note)
        self.assertIn(note, self.wake(project, gh, report(merge=True)))  # every merge, while it's older

    def test_a_merge_on_the_current_plugin_says_nothing(self):
        project, gh = apb_project(), fake_gh("0.3.57")
        self.assertEqual(self.wake(project, gh, report(merge=True), installed="0.3.57"), "")

    def test_a_wake_without_a_merge_does_not_repeat_what_was_said(self):
        project, gh = apb_project(), fake_gh("0.3.55")
        run(project, gh, installed="0.3.50")
        self.assertEqual(self.wake(project, gh, report(merge=False)), "")

    def test_a_wake_without_a_merge_says_a_version_not_yet_said(self):
        # main moved on since the session last heard (the version bump lands just after a merge).
        project, gh = apb_project(), fake_gh("0.3.55")
        run(project, gh, installed="0.3.50")
        set_gh(gh, "0.3.56")
        note = self.wake(project, gh, report(merge=False))
        self.assertIn("0.3.56", note)
        self.assertEqual(self.wake(project, gh, report(merge=False)), "")  # said once

    def test_another_session_has_its_own_record(self):
        project, gh = apb_project(), fake_gh("0.3.55")
        run(project, gh, installed="0.3.50")
        self.assertIn("0.3.55", self.wake(project, gh, report(merge=False), session="s2"))

    def test_github_unreadable_at_a_wake_says_nothing(self):
        project = apb_project()
        self.assertEqual(self.wake(project, fake_gh("fail"), report(merge=True)), "")


class WatcherWake(Clean):
    """End to end: the watcher hook wakes the orchestrator with a merge and the version note."""

    FAKE_WATCH = ('import sys\nprint("12:00:00 apb-watch (o/r):\\nGitHub: PR #3 merged: x", flush=True)\n')

    def wake(self, main_version):
        project = apb_project()
        watch = os.path.join(tmp(), "fake-watch.py")
        with open(watch, "w") as f:
            f.write(self.FAKE_WATCH)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=project)
        p = {"session_id": "s1", "cwd": project, "hook_event_name": "PreToolUse", "tool_name": "Bash",
             "tool_input": {"command": "ls"}}
        return subprocess.run([sys.executable, WATCH_START, "--watch-script", watch, "--budget", "20",
                               "--grace", "2", "--backoff", "0", "--gh", fake_gh(main_version)],
                              input=json.dumps(p).encode(), capture_output=True, env=env,
                              cwd=tempfile.gettempdir(), timeout=60)

    def installed(self):
        with open(os.path.join(ROOT, ".claude-plugin", "plugin.json")) as f:
            return json.load(f)["version"]

    def test_the_wake_carries_the_note_when_main_is_newer(self):
        r = self.wake("999.0.0")
        self.assertEqual(r.returncode, 2, r.stderr)
        err = r.stderr.decode()
        self.assertIn("PR #3 merged", err)
        for part in ["999.0.0", self.installed(), UPDATE, UPDATE_PLUGIN, "then open a new session"]:
            self.assertIn(part, err)

    def test_the_wake_has_no_note_on_the_current_version(self):
        r = self.wake(self.installed())
        self.assertEqual(r.returncode, 2, r.stderr)
        err = r.stderr.decode()
        self.assertIn("PR #3 merged", err)
        self.assertNotIn(UPDATE, err)

    def test_the_wake_still_comes_when_github_fails(self):
        r = self.wake("fail")
        self.assertEqual(r.returncode, 2)
        self.assertIn("PR #3 merged", r.stderr.decode())


# ---------------------------------------------------------------- wiring

class Wiring(Clean):
    def test_session_start_runs_the_check_and_waits_for_it(self):
        with open(HOOKS_JSON, encoding="utf-8") as f:
            config = json.load(f)
        found = [h for g in config["hooks"]["SessionStart"] for h in g["hooks"]
                 if any(a.endswith("/hooks/apb-version.py") for a in h.get("args", []))]
        self.assertEqual(len(found), 1)
        hook = found[0]
        # Not async: its note must be in the context before Claude's first message.
        self.assertFalse(hook.get("async") or hook.get("asyncRewake"))
        self.assertGreaterEqual(hook["timeout"], 2 * version.TIMEOUT + 5)
        self.assertEqual(hook["args"][0], "${CLAUDE_PLUGIN_ROOT}/hooks/apb-version.py")

    def test_only_at_session_start(self):
        with open(HOOKS_JSON, encoding="utf-8") as f:
            config = json.load(f)
        for event, groups in config["hooks"].items():
            if event == "SessionStart":
                continue
            for g in groups:
                for h in g["hooks"]:
                    self.assertFalse(any("apb-version.py" in a for a in h.get("args", [])), event)

    def test_uses_the_shared_folder_rule(self):
        with open(SCRIPT, encoding="utf-8") as f:
            self.assertIn("from apb_folder import", f.read())


if __name__ == "__main__":
    unittest.main()
