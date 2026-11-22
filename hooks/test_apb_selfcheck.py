"""Tests for apb-selfcheck.py (issue #133): the orchestrator's startup self-check.
Run from the repo root: python3 -m unittest discover -s hooks -t hooks

The card's acceptance:
- a fresh Code session on the apb repo prints the block without being asked: the SessionStart hook
  gives Claude the block, with the instruction to put it at the top of its first message;
- a deliberately broken item (a stopped watcher, say) shows ❌.
And from the card: one block, ✅ or ❌ per line, the five checks in order, easy to add one more; and
from its comment: the block names the session, `apb orch N · <version> · <start time PT>`.

Each test runs the hook the way Claude Code does (a JSON payload on stdin, the folder in
CLAUDE_PROJECT_DIR), with fakes for what it reads:
- a fake gh (--gh): `gh api user` answers Claude's login when GH_TOKEN is set and the human's when it
  isn't; `gh api graphql` answers the board; the manifest lookup answers main's plugin version;
- a fake watcher: a sleeping process whose command names apb-watch, with its file in a watcher
  folder (APB_WATCHERS_DIR) and an owner file naming a Claude Code process (--root-pid);
- a folder standing in for the installed plugin (--plugin-root), with hooks.json;
- a home folder (--home) for ~/.claude/settings.json.
Every check line is run both ways: healthy and broken. The random test breaks a random set of items
and checks that exactly those lines show ❌ (the oracle is the set the test broke).
"""
import importlib.util
import json
import os
import random
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "apb-selfcheck.py")
HOOKS_JSON = os.path.join(HERE, "hooks.json")
sys.path.insert(0, HERE)
import apb_session  # noqa: E402

LABELS = ["Watcher", "GitHub account", "Board", "Hooks", "Version"]
ROOT_PID = os.getpid()  # stands in for Claude Code: a live process this test owns
CREATED, PROCS = [], []


def tmp():
    d = tempfile.mkdtemp()
    CREATED.append(d)
    return d


def tearDownModule():
    for p in PROCS:
        if p.poll() is None:
            p.kill()
            p.wait(timeout=10)
    for d in CREATED:
        shutil.rmtree(d, ignore_errors=True)


def load():
    spec = importlib.util.spec_from_file_location("apb_selfcheck_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


FAKE_GH = r'''
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "gh.json")) as f:
    cfg = json.load(f)
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(json.dumps({"argv": sys.argv[1:], "token": bool(os.environ.get("GH_TOKEN"))}) + "\n")
if cfg.get("hang"):
    time.sleep(60)
a = sys.argv[1:]
if a[:2] == ["api", "user"]:
    login = cfg["token_login"] if os.environ.get("GH_TOKEN") else cfg["human_login"]
    if login is None:
        sys.exit("gh: not logged in")
    print(json.dumps({"login": login}))
elif a[:2] == ["api", "graphql"]:
    print(json.dumps(cfg["board"]))
elif any("plugin.json" in x for x in a):
    print(json.dumps({"name": "apb", "version": cfg["main_version"]}))
else:
    sys.exit("fake gh: unknown call %r" % a)
'''

FAKE_WATCHER = "import time\ntime.sleep(120)\n"


def card(n, status, state="OPEN", blocked=(), prs=(), title=None):
    return {"fieldValueByName": {"name": status} if status else None,
            "content": {"number": n, "title": title or "Card %d" % n, "state": state,
                        "url": "https://github.com/o/r/issues/%d" % n,
                        "blockedBy": {"nodes": [{"number": b, "state": s} for b, s in blocked]},
                        "closedByPullRequestsReferences": {"nodes": [{"number": p, "state": "OPEN"} for p in prs]}}}


def board(items):
    return {"data": {"repository": {"projectsV2": {"nodes": [
        {"title": "r", "url": "https://github.com/users/o/projects/1",
         "items": {"pageInfo": {"hasNextPage": False, "endCursor": None}, "nodes": items}}]}}}}


GOOD_BOARD = [card(7, "In progress"), card(9, "In review"), card(130, "Todo", title="Next one"),
              card(131, "Todo"), card(5, "Done", state="CLOSED")]


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


class World:
    """One orchestrator's surroundings, healthy unless a test breaks a part."""

    def __init__(self):
        self.project = tmp()
        git(self.project, "init", "-q", "-b", "main")
        commit(self.project, "docs/MAP.md", "# map\n")
        with open(os.path.join(self.project, "CLAUDE.md"), "w") as f:
            f.write("# r\n\ncoordination: #25\n")
        git(self.project, "remote", "add", "origin", "https://github.com/o/r.git")
        git(self.project, "branch", "issue-7-build-it")
        self.apb = os.path.join(self.project, ".apb")
        os.makedirs(self.apb)
        # the installed plugin
        self.plugin = tmp()
        os.makedirs(os.path.join(self.plugin, ".claude-plugin"))
        os.makedirs(os.path.join(self.plugin, "hooks"))
        with open(os.path.join(self.plugin, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "apb", "version": "0.3.60", "repository": "https://github.com/o/apb"}, f)
        with open(HOOKS_JSON) as f:
            self.hooks = json.load(f)
        self.write_hooks()
        for name in os.listdir(HERE):
            if name.endswith(".py") and not name.startswith("test_"):
                open(os.path.join(self.plugin, "hooks", name), "w").close()
        # home
        self.home = tmp()
        os.makedirs(os.path.join(self.home, ".claude"))
        # gh
        d = tmp()
        self.gh = os.path.join(d, "gh")
        with open(self.gh, "w") as f:
            f.write("#!%s\n%s" % (sys.executable, FAKE_GH))
        os.chmod(self.gh, 0o755)
        self.cfg = {"token_login": "hornof-claude", "human_login": "hornof", "main_version": "0.3.60",
                    "board": board(GOOD_BOARD), "hang": False}
        self.write_gh()
        # the watcher
        self.registry = tmp()
        self.start_watcher()
        self.owner(ROOT_PID, "earlier name")

    def write_hooks(self):
        with open(os.path.join(self.plugin, "hooks", "hooks.json"), "w") as f:
            json.dump(self.hooks, f)

    def write_gh(self):
        with open(os.path.join(os.path.dirname(self.gh), "gh.json"), "w") as f:
            json.dump(self.cfg, f)

    def start_watcher(self):
        script = os.path.join(tmp(), "apb-watch.py")
        with open(script, "w") as f:
            f.write(FAKE_WATCHER)
        self.watcher = subprocess.Popen([sys.executable, script])
        PROCS.append(self.watcher)
        record = {"repo": "o/r", "issues": [25], "poll": 120, "started": time.time(),
                  "state": os.path.join(self.apb, "watch.json"), "pid": self.watcher.pid}
        with open(os.path.join(self.registry, "o_r-%d.json" % self.watcher.pid), "w") as f:
            json.dump(record, f)

    def owner(self, root, name):
        with open(os.path.join(self.apb, "watcher-owner.json"), "w") as f:
            json.dump({"root": root, "started": time.time(), "session": "s", "name": name}, f)

    def settings(self, path, data):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as f:
            json.dump(data, f)

    # ------------------------------------------------ the breaks, one per check line
    def break_watcher(self):
        self.watcher.kill()
        self.watcher.wait(timeout=10)
        for name in os.listdir(self.registry):  # its file outlives it, past the fresh window
            old = time.time() - 600
            os.utime(os.path.join(self.registry, name), (old, old))

    def break_owner(self):
        self.owner(1, "apb orch 2 · 0.3.58 · Nov 20 9:00 AM")  # pid 1: alive, not this session

    def break_login(self):
        self.cfg["token_login"] = "hornof"  # no Claude token: gh posts as the human
        self.write_gh()

    def break_board_blocked(self):
        self.cfg["board"] = board([card(7, "In progress"), card(130, "Todo", blocked=[(120, "OPEN")])])
        self.write_gh()

    def break_board_closed(self):
        self.cfg["board"] = board([card(7, "In progress"), card(130, "Todo", state="CLOSED")])
        self.write_gh()

    def break_board_branch(self):
        self.cfg["board"] = board([card(7, "In progress"), card(8, "In progress"), card(130, "Todo")])
        self.write_gh()

    def break_stop_hook(self):
        self.hooks["hooks"].pop("Stop")
        self.write_hooks()

    def break_hooks_disabled(self):
        self.settings(os.path.join(self.project, ".claude", "settings.local.json"), {"disableAllHooks": True})

    def break_plugin_off(self):
        self.settings(os.path.join(self.home, ".claude", "settings.json"), {"enabledPlugins": {"apb@apb": False}})

    def break_version(self):
        self.cfg["main_version"] = "0.3.61"
        self.write_gh()

    # ------------------------------------------------ running it
    def args(self, wait=1.0, extra=()):
        return ["--gh", self.gh, "--plugin-root", self.plugin, "--home", self.home, "--root-pid", str(ROOT_PID),
                "--wait", str(wait), "--timeout", "5"] + list(extra)

    def env(self):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=self.project, APB_WATCHERS_DIR=self.registry, GH_TOKEN="t")
        env.pop("GITHUB_TOKEN", None)
        return env

    def run(self, payload=None, wait=1.0, extra=(), timeout=60):
        p = {"hook_event_name": "SessionStart", "source": "startup", "session_id": "sess-1",
             "cwd": self.project} if payload is None else payload
        started = time.time()
        r = subprocess.run([sys.executable, SCRIPT] + self.args(wait, extra), input=json.dumps(p),
                           capture_output=True, text=True, timeout=timeout, env=self.env())
        self.took = time.time() - started
        return r

    def context(self, **kw):
        r = self.run(**kw)
        if r.returncode != 0:
            raise AssertionError("exit %d: %s" % (r.returncode, r.stderr))
        if not r.stdout.strip():
            return None
        out = json.loads(r.stdout)
        self.assert_shape(out)
        return out["hookSpecificOutput"]["additionalContext"]

    @staticmethod
    def assert_shape(out):
        assert out["hookSpecificOutput"]["hookEventName"] == "SessionStart", out


def lines_of(text):
    """{label: (ok, rest)} for the block's check lines."""
    found = {}
    for line in text.splitlines():
        for mark, ok in (("✅ ", True), ("❌ ", False)):
            if line.startswith(mark):
                label, _, rest = line[len(mark):].partition(": ")
                found[label] = (ok, rest)
    return found


def check_lines(text):
    return [l for l in text.splitlines() if l.startswith(("✅ ", "❌ "))]


BREAKS = {
    "Watcher": ["break_watcher", "break_owner"],
    "GitHub account": ["break_login"],
    "Board": ["break_board_blocked", "break_board_closed", "break_board_branch"],
    "Hooks": ["break_stop_hook", "break_hooks_disabled", "break_plugin_off"],
    "Version": ["break_version"],
}


class Healthy(unittest.TestCase):
    def test_a_fresh_session_gets_the_block_unprompted_all_green(self):
        w = World()
        text = w.context()
        self.assertIsNotNone(text, "the SessionStart hook gives Claude the block")
        self.assertIn("top of your first message", text)
        self.assertEqual([l.split(": ")[0][2:] for l in check_lines(text)], LABELS, "five lines, in the card's order")
        self.assertTrue(all(ok for ok, _ in lines_of(text).values()), text)

    def test_each_line_says_what_it_found(self):
        text = World().context()
        found = lines_of(text)
        self.assertIn("o/r", found["Watcher"][1])
        self.assertIn("#25", found["Watcher"][1])
        self.assertIn("hornof-claude", found["GitHub account"][1])
        self.assertIn("#130", found["Board"][1])
        self.assertIn("Next one", found["Board"][1])
        self.assertIn("#7", found["Board"][1])
        self.assertIn("Stop", found["Hooks"][1])
        self.assertIn("0.3.60", found["Version"][1])

    def test_the_block_names_the_session(self):
        w = World()
        text = w.context()
        name = apb_session.session_name(w.apb, "sess-1", "ignored")
        self.assertRegex(name, r"^apb orch 1 · 0\.3\.60 · [A-Z][a-z]{2} \d{1,2} \d{1,2}:\d\d [AP]M$")
        self.assertIn(name, text)
        self.assertIn("set_session_title", text, "the model renames the session with the app's tool")
        self.assertIn("rename", text.lower())

    def test_a_resumed_session_keeps_its_name(self):
        w = World()
        first = w.context()
        again = w.context(payload={"hook_event_name": "SessionStart", "source": "resume", "session_id": "sess-1",
                                   "cwd": w.project})
        name = apb_session.session_name(w.apb, "sess-1", "x")
        self.assertIn(name, first)
        self.assertIn(name, again)

    def test_without_root_pid_any_ancestor_counts_as_this_session(self):
        # Run by hand, a wrapper such as `timeout` sits between Claude Code and the script; the owner
        # file names Claude Code, an ancestor further up (here, this test process).
        w = World()
        argv = [a for a in w.args() if a not in ("--root-pid", str(ROOT_PID))]
        r = subprocess.run(["env", sys.executable, SCRIPT, "--print", "--project", w.project] + argv,
                           capture_output=True, text=True, timeout=60, env=w.env(), stdin=subprocess.DEVNULL)
        self.assertTrue(lines_of(r.stdout)["Watcher"][0], r.stdout)
        w.break_owner()
        r = subprocess.run(["env", sys.executable, SCRIPT, "--print", "--project", w.project] + argv,
                           capture_output=True, text=True, timeout=60, env=w.env(), stdin=subprocess.DEVNULL)
        self.assertFalse(lines_of(r.stdout)["Watcher"][0], r.stdout)

    def test_with_no_owner_file_the_holder_is_unknown(self):
        # Review Fix 1: "held by this session" only when the owner file names one of this session's processes.
        w = World()
        os.remove(os.path.join(w.apb, "watcher-owner.json"))
        ok, rest = lines_of(w.context())["Watcher"]
        self.assertTrue(ok, "the watcher runs")
        self.assertIn("unknown", rest)
        self.assertNotIn("held by this session", rest)
        self.assertLess(w.took, 15, "waits at most --wait for the owner file")

    def test_with_no_ancestors_to_compare_the_holder_is_unknown(self):
        w = World()
        m = load()
        saved = os.environ.get("APB_WATCHERS_DIR")
        os.environ["APB_WATCHERS_DIR"] = w.registry
        try:
            ctx = m.Context(project=w.project, plugin_root=w.plugin, gh=w.gh, timeout=5, wait=0.5, roots=[],
                            home=w.home, hook=False)
            (label, ok, rest), = m.run_checks(ctx, [("Watcher", m.check_watcher)])
        finally:
            if saved is None:
                os.environ.pop("APB_WATCHERS_DIR")
            else:
                os.environ["APB_WATCHERS_DIR"] = saved
        self.assertTrue(ok, rest)
        self.assertIn("unknown", rest)
        self.assertNotIn("held by this session", rest)

    def test_an_in_progress_card_with_an_open_pr_has_a_branch(self):
        w = World()
        w.cfg["board"] = board([card(8, "In progress", prs=[140]), card(130, "Todo")])
        w.write_gh()
        self.assertTrue(lines_of(w.context())["Board"][0])

    def test_a_card_blocked_only_by_closed_issues_is_free(self):
        w = World()
        w.cfg["board"] = board([card(7, "In progress"), card(130, "Todo", blocked=[(120, "CLOSED")])])
        w.write_gh()
        self.assertTrue(lines_of(w.context())["Board"][0])

    def test_an_empty_todo_is_fine(self):
        w = World()
        w.cfg["board"] = board([card(7, "In progress")])
        w.write_gh()
        ok, rest = lines_of(w.context())["Board"]
        self.assertTrue(ok)
        self.assertIn("Todo is empty", rest)

    def test_without_a_human_login_the_repo_owner_is_the_human(self):
        w = World()
        w.cfg["human_login"] = None
        w.write_gh()
        self.assertTrue(lines_of(w.context())["GitHub account"][0])
        w.cfg["token_login"] = "o"
        w.write_gh()
        self.assertFalse(lines_of(w.context())["GitHub account"][0])


class Broken(unittest.TestCase):
    """Each check line, broken: that line shows ❌, and the others stay ✅."""

    def one(self, label, brk):
        w = World()
        getattr(w, brk)()
        text = w.context()
        found = lines_of(text)
        self.assertFalse(found[label][0], "%s: %s" % (brk, text))
        for other in LABELS:
            if other != label:
                self.assertTrue(found[other][0], "%s broke %s too: %s" % (brk, other, text))
        self.assertIn("raise each ❌", text)
        return found[label][1]

    def test_a_stopped_watcher(self):
        self.assertIn("no watcher", self.one("Watcher", "break_watcher").lower())

    def test_a_watcher_held_by_another_session(self):
        self.assertIn("apb orch 2", self.one("Watcher", "break_owner"))

    def test_posting_as_the_human(self):
        self.assertIn("hornof", self.one("GitHub account", "break_login"))

    def test_a_blocked_top_of_todo(self):
        self.assertIn("#120", self.one("Board", "break_board_blocked"))

    def test_a_closed_top_of_todo(self):
        self.assertIn("closed", self.one("Board", "break_board_closed"))

    def test_in_progress_without_a_branch(self):
        rest = self.one("Board", "break_board_branch")
        self.assertIn("#8", rest)
        self.assertNotIn("#7 ", rest)

    def test_no_stop_hook(self):
        self.assertIn("Stop", self.one("Hooks", "break_stop_hook"))

    def test_hooks_turned_off_in_settings(self):
        self.assertIn("disableAllHooks", self.one("Hooks", "break_hooks_disabled"))

    def test_the_plugin_turned_off(self):
        self.assertIn("apb@apb", self.one("Hooks", "break_plugin_off"))

    def test_an_older_plugin_than_main(self):
        rest = self.one("Version", "break_version")
        self.assertIn("0.3.61", rest)
        self.assertIn("claude plugin update apb@apb", rest)

    def test_random_breaks_show_exactly_those_lines_red(self):
        rnd = random.Random(1330)
        for _ in range(8):
            broken = set(rnd.sample(LABELS, rnd.randrange(0, len(LABELS) + 1)))
            w = World()
            for label in broken:
                getattr(w, rnd.choice(BREAKS[label]))()
            text = w.context()
            red = {label for label, (ok, _) in lines_of(text).items() if not ok}
            self.assertEqual(red, broken, text)
            self.assertEqual(len(check_lines(text)), len(LABELS))
            self.assertEqual("raise each ❌" in text, bool(broken))


class Waiting(unittest.TestCase):
    def test_it_waits_for_the_watcher_the_same_hook_event_starts(self):
        # At session start the watcher hook runs alongside this one: the watcher may come up a moment later.
        w = World()
        w.break_watcher()
        os.remove(os.path.join(w.apb, "watcher-owner.json"))
        payload = os.path.join(tmp(), "payload.json")
        with open(payload, "w") as f:
            json.dump({"hook_event_name": "SessionStart", "source": "startup", "session_id": "sess-1",
                       "cwd": w.project}, f)
        with open(payload) as stdin:  # a file, so the hook reads its whole payload at once
            proc = subprocess.Popen([sys.executable, SCRIPT] + w.args(wait=15), stdin=stdin,
                                    stdout=subprocess.PIPE, text=True, env=w.env())
        try:
            time.sleep(1.5)
            w.start_watcher()
            w.owner(ROOT_PID, "x")
            out, _ = proc.communicate(timeout=30)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.communicate(timeout=10)
        text = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(lines_of(text)["Watcher"][0], text)

    def test_a_missing_watcher_costs_at_most_the_wait(self):
        w = World()
        w.break_watcher()
        w.context(wait=2)
        self.assertLess(w.took, 10)

    def test_a_hanging_gh_gives_red_lines_in_time(self):
        w = World()
        w.cfg["hang"] = True
        w.write_gh()
        text = w.context(extra=["--timeout", "1"])
        found = lines_of(text)
        self.assertFalse(found["GitHub account"][0])
        self.assertFalse(found["Board"][0])
        self.assertFalse(found["Version"][0])
        self.assertTrue(found["Watcher"][0])
        self.assertLess(w.took, 20)


def resumed(w, session="bf4e8d74"):
    return {"hook_event_name": "SessionStart", "source": "resume", "session_id": session, "cwd": w.project}


class Reopened(unittest.TestCase):
    """Issue #146: a reopened (resumed) session gets no orchestrator number, and its block doesn't claim
    the watcher when another open session holds it."""

    def test_a_reopened_session_with_another_holder_gets_no_number_and_says_who_holds_it(self):
        w = World()
        w.break_owner()  # an open session (pid 1), "apb orch 2 · 0.3.58 · Nov 20 9:00 AM", holds it
        text = w.context(payload=resumed(w))
        ok, rest = lines_of(text)["Watcher"]
        self.assertNotIn("held by this session", rest)
        self.assertIn("apb orch 2 · 0.3.58", rest)
        self.assertIn("reopened", rest)
        self.assertTrue(ok, "a reopened session leaving the watcher to an open holder is how it should be")
        self.assertIsNone(apb_session.session_name(w.apb, "bf4e8d74", "x", assign=False), "no number given")
        self.assertFalse(os.path.exists(os.path.join(w.apb, apb_session.SESSIONS)), "sessions.json not written")
        self.assertNotIn("set_session_title", text, "no name, so nothing to rename the session to")
        heads = [l for l in text.splitlines() if l.startswith("apb startup self-check")]
        self.assertEqual(heads[-1], "apb startup self-check", "the block's head names no session")

    def test_a_session_first_active_before_the_open_holder_counts_as_reopened(self):
        # Review Fix 1: no "resume" source, but its first line in the events file comes before the holder's.
        w = World()
        w.break_owner()  # session "s", open (pid 1), "apb orch 2 · 0.3.58 · Nov 20 9:00 AM", holds it
        with open(os.path.join(w.apb, "events.jsonl"), "w") as f:
            f.write(json.dumps({"event": "Stop", "session": "bf4e8d74"}) + "\n")
            f.write(json.dumps({"event": "watcher-start", "session": "s"}) + "\n")
        p = dict(resumed(w), source="startup")
        ok, rest = lines_of(w.context(payload=p))["Watcher"]
        self.assertTrue(ok, rest)
        self.assertIn("reopened", rest)
        self.assertNotIn("held by this session", rest)
        self.assertIsNone(apb_session.session_name(w.apb, "bf4e8d74", "x", assign=False), "no number given")
        self.assertFalse(os.path.exists(os.path.join(w.apb, apb_session.SESSIONS)), "sessions.json not written")

    def test_a_session_first_active_after_the_holder_is_new(self):
        w = World()
        w.break_owner()
        with open(os.path.join(w.apb, "events.jsonl"), "w") as f:
            f.write(json.dumps({"event": "watcher-start", "session": "s"}) + "\n")
            f.write(json.dumps({"event": "Stop", "session": "fresh"}) + "\n")
        p = dict(resumed(w, "fresh"), source="startup")
        ok, rest = lines_of(w.context(payload=p))["Watcher"]
        self.assertFalse(ok, rest)
        self.assertNotIn("reopened", rest)
        self.assertIsNotNone(apb_session.session_name(w.apb, "fresh", "x", assign=False), "it got a number")

    def test_a_reopened_session_that_holds_the_watcher_says_so(self):
        # No open session held it, so the reopened one started it (the owner file names it).
        w = World()
        ok, rest = lines_of(w.context(payload=resumed(w)))["Watcher"]
        self.assertTrue(ok)
        self.assertIn("held by this session", rest)

    def test_a_reopened_session_whose_holder_is_closed_does_not_say_it_leaves_it(self):
        w = World()
        closed = subprocess.Popen([sys.executable, "-c", "pass"])
        closed.wait(timeout=10)
        w.owner(closed.pid, "apb orch 5 · 0.3.60 · Nov 21 9:00 PM")
        ok, rest = lines_of(w.context(payload=resumed(w), wait=0.5))["Watcher"]
        self.assertFalse(ok, rest)
        self.assertNotIn("held by this session", rest)
        self.assertNotIn("reopened", rest)

    def test_a_new_session_still_gets_the_next_number(self):
        w = World()
        w.context(payload=resumed(w))
        text = w.context()
        self.assertIn(apb_session.session_name(w.apb, "sess-1", "x"), text)
        self.assertTrue(apb_session.session_name(w.apb, "sess-1", "x").startswith("apb orch 1 · "))


class StaysOut(unittest.TestCase):
    def test_a_subagent_gets_nothing(self):
        w = World()
        r = w.run(payload={"hook_event_name": "SessionStart", "session_id": "s", "agent_id": "a", "cwd": w.project})
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_a_folder_that_is_not_apb_gets_nothing(self):
        w = World()
        os.remove(os.path.join(w.project, "docs", "MAP.md"))
        r = w.run()
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_after_a_compaction_it_says_nothing(self):
        w = World()
        r = w.run(payload={"hook_event_name": "SessionStart", "source": "compact", "session_id": "s", "cwd": w.project})
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_a_linked_worktree_gets_nothing(self):
        # Build and review sessions run in worktrees; only the orchestrator's checkout runs the check.
        w = World()
        wt = os.path.join(tmp(), "wt")
        git(w.project, "worktree", "add", "-q", "-b", "build-1", wt)
        env = dict(w.env(), CLAUDE_PROJECT_DIR=wt)
        r = subprocess.run([sys.executable, SCRIPT] + w.args(), capture_output=True, text=True, timeout=60, env=env,
                           input=json.dumps({"hook_event_name": "SessionStart", "source": "startup",
                                             "session_id": "s", "cwd": wt}))
        self.assertEqual((r.returncode, r.stdout), (0, ""))

    def test_bad_input_gets_nothing(self):
        w = World()
        r = subprocess.run([sys.executable, SCRIPT] + w.args(), input="not json", capture_output=True, text=True,
                           timeout=60, env=w.env())
        self.assertEqual((r.returncode, r.stdout), (0, ""))


class ByHand(unittest.TestCase):
    def test_print_runs_it_in_a_folder_without_a_payload(self):
        w = World()
        w.break_version()
        r = subprocess.run([sys.executable, SCRIPT, "--print", "--project", w.project] + w.args(),
                           capture_output=True, text=True, timeout=60, env=w.env(), stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 1, "a ❌ line gives exit 1")
        self.assertEqual(len(check_lines(r.stdout)), len(LABELS))
        self.assertFalse(lines_of(r.stdout)["Version"][0])

    def test_print_exits_0_when_all_green(self):
        w = World()
        r = subprocess.run([sys.executable, SCRIPT, "--print", "--project", w.project] + w.args(),
                           capture_output=True, text=True, timeout=60, env=w.env(), stdin=subprocess.DEVNULL)
        self.assertEqual(r.returncode, 0, r.stdout)


class OneMoreLine(unittest.TestCase):
    def test_a_new_check_is_one_function_and_one_entry(self):
        m = load()
        before = len(m.CHECKS)
        m.CHECKS.append(("Extra", lambda ctx: (False, "something new is wrong")))
        results = m.run_checks(m.Context(project=tmp(), plugin_root=tmp(), gh="false", timeout=1, wait=0,
                                         roots=[], home=tmp(), hook=False), m.CHECKS[before:])
        self.assertEqual(results, [("Extra", False, "something new is wrong")])
        self.assertIn("❌ Extra: something new is wrong", m.block("apb orch 1 · 1 · Jan 1 1:00 AM", results))

    def test_a_check_that_raises_shows_red_not_a_crash(self):
        m = load()

        def boom(ctx):
            raise RuntimeError("kaput")
        results = m.run_checks(m.Context(project=tmp(), plugin_root=tmp(), gh="false", timeout=1, wait=0,
                                         roots=[], home=tmp(), hook=False), [("Boom", boom)])
        self.assertEqual(results[0][:2], ("Boom", False))
        self.assertIn("kaput", results[0][2])


class TheApbRepo(unittest.TestCase):
    def test_a_checkout_off_main_shows_red_on_the_version_line(self):
        origin = os.path.join(tmp(), "origin.git")
        git(tmp(), "init", "-q", "--bare", "-b", "main", origin)
        clone = os.path.join(tmp(), "apb")
        git(tmp(), "clone", "-q", origin, clone)
        git(clone, "checkout", "-q", "-b", "main")
        commit(clone, ".claude-plugin/plugin.json", json.dumps({"name": "apb", "version": "0.3.60"}))
        commit(clone, "hooks/apb-watch-start.py", "")
        git(clone, "push", "-q", "origin", "main")
        git(clone, "checkout", "-q", "-b", "feature")
        w = World()
        w.project = clone
        w.apb = os.path.join(clone, ".apb")
        os.makedirs(w.apb)
        w.break_watcher()
        w.start_watcher()
        w.owner(ROOT_PID, "x")
        w.cfg["board"] = board([card(7, "In progress"), card(130, "Todo")])
        w.write_gh()
        git(clone, "branch", "issue-7-x")
        found = lines_of(w.context())
        self.assertFalse(found["Version"][0])
        self.assertIn("feature", found["Version"][1])
        self.assertTrue(found["Hooks"][0])


class HooksFile(unittest.TestCase):
    def test_it_runs_at_session_start_before_the_first_message(self):
        with open(HOOKS_JSON) as f:
            hooks = json.load(f)["hooks"]
        entries = [h for group in hooks["SessionStart"] for h in group["hooks"]
                   if any("apb-selfcheck.py" in a for a in h.get("args", []))]
        self.assertEqual(len(entries), 1)
        h = entries[0]
        self.assertFalse(h.get("async") or h.get("asyncRewake"), "its output must be in context for the first message")
        m = load()
        self.assertGreater(h["timeout"], m.WAIT + 2 * m.TIMEOUT, "room for the wait and the gh calls")

    def test_no_other_event_runs_it(self):
        with open(HOOKS_JSON) as f:
            hooks = json.load(f)["hooks"]
        for event, groups in hooks.items():
            if event != "SessionStart":
                for group in groups:
                    for h in group["hooks"]:
                        self.assertFalse(any("apb-selfcheck.py" in a for a in h.get("args", [])), event)


if __name__ == "__main__":
    unittest.main()
