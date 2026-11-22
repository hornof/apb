"""Tests for apb_folder.py, and for every hook acting in the apb repo itself (issue #132).
Run from the repo root: python3 -m unittest discover hooks

apb's hooks act in an apb project (a folder with docs/MAP.md) and in the apb repo itself (its
.claude-plugin/plugin.json names the plugin "apb" and it holds hooks/apb-watch-start.py). #118 made
the watcher hook run there; the others skipped it, so on Nov 21 the Stop hook let "Nothing for you to
do" through in a session on the apb repo while nothing was running.

The rule lives in one place, hooks/apb_folder.py. These tests check:
- the rule against an oracle written from the issue, over every folder shape;
- each hook, run the way Claude Code runs it, acts exactly where the rule says, in both directions:
  in a project and in an apb-repo-shaped folder it acts; in a folder that is neither it does nothing
  and writes nothing;
- the card's acceptance: in an apb-repo-shaped folder, with events written by apb-events.py itself,
  the Stop hook sends back a closing line that says "Nothing for you to do" while nothing is running,
  and lets it through while something is;
- in the apb repo, with no .apb folder, no events file and no board, every hook exits 0.
"""
import importlib.util
import itertools
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
HOOKS_JSON = os.path.join(HERE, "hooks.json")
EVENTS = os.path.join(HERE, "apb-events.py")
STOP = os.path.join(HERE, "apb-stop.py")
BRIEF = os.path.join(HERE, "apb-brief.py")
POPUPS = os.path.join(HERE, "apb-popups.py")
WATCH_START = os.path.join(HERE, "apb-watch-start.py")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


folder_rule = load("apb_folder_under_test", os.path.join(HERE, "apb_folder.py"))

with open(os.path.join(ROOT, ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
    REAL_MANIFEST = f.read()

# The manifest texts a folder can have: the real one, other names, broken files, none.
MANIFESTS = [REAL_MANIFEST, json.dumps({"name": "apb"}), json.dumps({"name": "APB"}),
             json.dumps({"name": "other-plugin"}), json.dumps({"name": ["apb"]}), json.dumps(["apb"]),
             "not json", "", None]


CREATED = []  # every temporary folder make() made; Clean.tearDown removes them


class Clean(unittest.TestCase):
    def tearDown(self):
        while CREATED:
            shutil.rmtree(CREATED.pop(), ignore_errors=True)


def make(map_md=False, manifest=None, hook=False):
    """A temporary folder: docs/MAP.md if map_md, .claude-plugin/plugin.json with manifest (unless
    None), hooks/apb-watch-start.py if hook. Clean removes it after the test."""
    d = tempfile.mkdtemp()
    CREATED.append(d)
    if map_md:
        os.makedirs(os.path.join(d, "docs"))
        with open(os.path.join(d, "docs", "MAP.md"), "w") as f:
            f.write("# map\n")
    if manifest is not None:
        os.makedirs(os.path.join(d, ".claude-plugin"))
        with open(os.path.join(d, ".claude-plugin", "plugin.json"), "w") as f:
            f.write(manifest)
    if hook:
        os.makedirs(os.path.join(d, "hooks"))
        with open(os.path.join(d, "hooks", "apb-watch-start.py"), "w") as f:
            f.write("# the hook\n")
    return d


def apb_repo():
    """A folder shaped like the apb repo: the real manifest and the watcher hook, no docs/MAP.md."""
    return make(manifest=REAL_MANIFEST, hook=True)


def neither():
    """A folder that is neither an apb project nor the apb repo: another plugin's repo."""
    return make(manifest=json.dumps({"name": "other-plugin"}), hook=True)


def oracle(map_md, manifest, hook):
    """The rule, from the issue: a project has docs/MAP.md; the apb repo's manifest is a JSON object
    whose name is exactly "apb", and it holds hooks/apb-watch-start.py."""
    if map_md:
        return True
    try:
        data = json.loads(manifest) if manifest is not None else None
    except ValueError:
        data = None
    return hook and isinstance(data, dict) and data.get("name") == "apb"


def run(script, payload, project, args=(), cwd=None, extra_env=None):
    """Run a hook the way Claude Code does: JSON on stdin, the folder in CLAUDE_PROJECT_DIR."""
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if project is not None:
        env["CLAUDE_PROJECT_DIR"] = project
    env.update(extra_env or {})
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    return subprocess.run([sys.executable, script] + list(args), input=data, capture_output=True,
                          env=env, cwd=cwd or tempfile.gettempdir(), timeout=20)


def events(project):
    path = os.path.join(project, ".apb", "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f.read().splitlines()]


def event(name, session="s1", **extra):
    p = {"session_id": session, "transcript_path": "/none.jsonl", "cwd": "/somewhere",
         "hook_event_name": name}
    p.update(extra)
    return p


def stop(message, **extra):
    return event("Stop", stop_hook_active=False, last_assistant_message=message, **extra)


def sent_back(r):
    if not r.stdout.strip():
        return None
    return json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]


def refused(r):
    if not r.stdout.strip():
        return False
    return json.loads(r.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


def ran_out(project):
    """Write, through apb-events.py, a turn whose work has run out: the human's prompt, an agent
    started, the agent stopped. Nothing is left running."""
    for p in [event("UserPromptSubmit", prompt="build card 132"),
              event("SubagentStart", agent_id="b1", agent_type="apb:builder"),
              event("SubagentStop", agent_id="b1", agent_type="apb:builder",
                    last_assistant_message="PR opened.")]:
        r = run(EVENTS, p, project)
        assert r.returncode == 0, r.stderr


class Rule(Clean):
    """apb_folder.py against the oracle, over every folder shape."""

    def test_every_shape_matches_the_oracle(self):
        for map_md, manifest, hook in itertools.product([False, True], MANIFESTS, [False, True]):
            d = make(map_md, manifest, hook)
            try:
                expected = oracle(map_md, manifest, hook)
                self.assertEqual(folder_rule.is_apb_folder(d), expected, (map_md, manifest, hook))
                # The apb repo is the rule without docs/MAP.md.
                self.assertEqual(folder_rule.is_apb_repo(d), oracle(False, manifest, hook),
                                 (map_md, manifest, hook))
                # project_dir: CLAUDE_PROJECT_DIR first, else the event's cwd; the folder or None.
                old = os.environ.pop("CLAUDE_PROJECT_DIR", None)
                try:
                    self.assertEqual(folder_rule.project_dir({"cwd": d}), d if expected else None)
                    os.environ["CLAUDE_PROJECT_DIR"] = d
                    self.assertEqual(folder_rule.project_dir({"cwd": "/somewhere"}), d if expected else None)
                finally:
                    os.environ.pop("CLAUDE_PROJECT_DIR", None)
                    if old is not None:
                        os.environ["CLAUDE_PROJECT_DIR"] = old
            finally:
                shutil.rmtree(d, ignore_errors=True)

    def test_this_checkout_is_the_apb_repo(self):
        self.assertTrue(folder_rule.is_apb_repo(ROOT))
        self.assertTrue(folder_rule.is_apb_folder(ROOT))

    def test_bad_input_is_no_folder(self):
        for p in [{}, {"cwd": None}, {"cwd": 5}, {"cwd": ["/x"]}, {"cwd": ""}, None, "x", []]:
            old = os.environ.pop("CLAUDE_PROJECT_DIR", None)
            try:
                self.assertIsNone(folder_rule.project_dir(p), p)
            finally:
                if old is not None:
                    os.environ["CLAUDE_PROJECT_DIR"] = old
        for bad in [None, 5, "", "/no/such/folder"]:
            self.assertFalse(folder_rule.is_apb_folder(bad), bad)
            self.assertFalse(folder_rule.is_apb_repo(bad), bad)

    def test_the_watcher_uses_the_same_rule(self):
        # The rule #118 wrote for the watcher now lives in apb_folder.py; the watcher calls it.
        hook = load("apb_watch_start_for_rule", WATCH_START)
        d = make(manifest=REAL_MANIFEST, hook=True)
        self.assertTrue(hook.is_apb_repo(d))
        self.assertEqual(hook.is_apb_repo.__module__, "apb_folder")


class EveryHookUsesTheRule(Clean):
    def test_every_script_in_hooks_json_uses_apb_folder(self):
        # A hook added later must use the shared rule too, so it can't skip the apb repo again.
        with open(HOOKS_JSON, encoding="utf-8") as f:
            config = json.load(f)
        scripts = sorted({os.path.basename(arg) for groups in config["hooks"].values() for group in groups
                          for hook in group["hooks"] for arg in hook.get("args", []) if arg.endswith(".py")})
        self.assertEqual(scripts, ["apb-brief.py", "apb-events.py", "apb-popups.py", "apb-stop.py",
                                   "apb-watch-start.py"])
        for name in scripts:
            with open(os.path.join(HERE, name), encoding="utf-8") as f:
                source = f.read()
            self.assertIn("from apb_folder import", source, name)
            self.assertNotIn('"docs", "MAP.md"', source, name)  # no hook checks MAP.md on its own


class Events(Clean):
    """apb-events.py writes the events file the Stop hook reads."""

    def test_writes_in_the_apb_repo(self):
        d = apb_repo()
        r = run(EVENTS, event("UserPromptSubmit", prompt="hi"), d)
        self.assertEqual(r.returncode, 0)
        self.assertEqual([e["event"] for e in events(d)], ["UserPromptSubmit"])
        with open(os.path.join(d, ".apb", ".gitignore")) as f:
            self.assertEqual(f.read(), "*\n")  # git never sees the events file

    def test_writes_nothing_elsewhere(self):
        for d in [neither(), make(manifest=REAL_MANIFEST), make(hook=True), make()]:
            r = run(EVENTS, event("UserPromptSubmit", prompt="hi"), d)
            self.assertEqual(r.returncode, 0)
            self.assertFalse(os.path.exists(os.path.join(d, ".apb")), d)

    def test_folder_from_cwd_when_no_env(self):
        d = apb_repo()
        r = run(EVENTS, event("UserPromptSubmit", prompt="hi", cwd=d), None)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(events(d)), 1)


class Stop(Clean):
    """The card's acceptance, end to end: apb-events.py writes the turn, apb-stop.py judges the stop."""

    BARE = ["Nothing for you to do.", "Nothing for you to do right now.",
            "Nothing for you to do. Four cards wait in Todo."]
    GOOD = "Nothing for you to do right now. Waiting on the builder for card #132, ETA 7:10 PM."

    def test_sends_back_nothing_for_you_while_nothing_runs_in_the_apb_repo(self):
        for message in self.BARE:
            for tasks in ([], None):  # Claude Code's background_tasks, or the events file without it
                d = apb_repo()
                ran_out(d)
                extra = {} if tasks is None else {"background_tasks": tasks}
                r = run(STOP, stop(message, **extra), d)
                self.assertEqual(r.returncode, 0)
                note = sent_back(r)
                self.assertIsNotNone(note, (message, tasks))
                self.assertTrue(note.startswith("Say who holds the ball"), note)
                self.assertTrue(events(d)[-1].get("sent_back"), (message, tasks))

    def test_sends_back_after_a_watcher_wake_in_the_apb_repo(self):
        # The Nov 21 incident: a finished turn ended with a full line; then a watcher woke the
        # orchestrator (Claude Code's own prompt, which apb-events.py logs as automatic) with nothing
        # running, and it closed with "Nothing for you to do." The hook must send that back.
        wakes = ["<task-notification>watcher found: PR #129 merged</task-notification>",
                 "Another Claude session sent a message: <agent-message>done</agent-message>"]
        for wake in wakes:
            for message in self.BARE:
                d = apb_repo()
                ran_out(d)
                r = run(STOP, stop(self.GOOD, background_tasks=[]), d)
                self.assertIsNone(sent_back(r))
                run(EVENTS, event("UserPromptSubmit", prompt=wake), d)
                self.assertTrue(events(d)[-1].get("automatic"), wake)
                r = run(STOP, stop(message, background_tasks=[]), d)
                note = sent_back(r)
                self.assertIsNotNone(note, (wake, message))
                self.assertTrue(note.startswith("Say who holds the ball"), note)

    def test_lets_the_humans_answer_through(self):
        # The other direction: the same turn started by the human's own prompt is an answer, not a stop.
        for prompt in ["merged", "what does the watcher do?"]:
            d = apb_repo()
            ran_out(d)
            run(STOP, stop(self.GOOD, background_tasks=[]), d)
            run(EVENTS, event("UserPromptSubmit", prompt=prompt), d)
            self.assertFalse(events(d)[-1].get("automatic"), prompt)
            r = run(STOP, stop("Nothing for you to do.", background_tasks=[]), d)
            self.assertIsNone(sent_back(r), prompt)

    def test_lets_it_through_while_something_runs(self):
        # The other direction: an agent still running means the work hasn't run out.
        for message in self.BARE:
            d = apb_repo()
            ran_out(d)
            r = run(STOP, stop(message, background_tasks=[{"id": "b2", "type": "agent"}]), d)
            self.assertEqual(r.returncode, 0)
            self.assertIsNone(sent_back(r), message)
            d = apb_repo()
            ran_out(d)
            run(EVENTS, event("SubagentStart", agent_id="b2", agent_type="apb:builder"), d)
            r = run(STOP, stop(message), d)  # no background_tasks: the events file says b2 runs
            self.assertIsNone(sent_back(r), message)

    def test_lets_a_full_first_line_through(self):
        d = apb_repo()
        ran_out(d)
        r = run(STOP, stop(self.GOOD, background_tasks=[]), d)
        self.assertEqual(r.returncode, 0)
        self.assertIsNone(sent_back(r))
        self.assertEqual(events(d)[-1].get("holder"), "claude")

    def test_does_nothing_elsewhere(self):
        for d in [neither(), make(manifest=REAL_MANIFEST), make(hook=True), make()]:
            # Even a turn written by hand into the events file doesn't wake the hook there.
            os.makedirs(os.path.join(d, ".apb"))
            with open(os.path.join(d, ".apb", "events.jsonl"), "w") as f:
                for e in ["UserPromptSubmit", "SubagentStart", "SubagentStop"]:
                    f.write(json.dumps({"time": "2026-11-21T10:00:00.000Z", "event": e, "session": "s1",
                                        "agent_id": None if e == "UserPromptSubmit" else "b1"}) + "\n")
            before = events(d)
            r = run(STOP, stop("Nothing for you to do.", background_tasks=[]), d)
            self.assertEqual(r.returncode, 0)
            self.assertIsNone(sent_back(r))
            self.assertEqual(events(d), before, d)  # no Stop line written

    def test_no_events_file_lets_the_stop_through(self):
        # A fresh clone of the apb repo: no .apb folder yet. The hook says nothing and never fails.
        d = apb_repo()
        r = run(STOP, stop("Nothing for you to do.", background_tasks=[]), d)
        self.assertEqual(r.returncode, 0)
        self.assertIsNone(sent_back(r))


class Brief(Clean):
    DEPARTED = "Done. I departed from agents/builder.md: I skipped the LOG.md line."

    def test_records_a_departure_in_the_apb_repo(self):
        d = apb_repo()
        r = run(BRIEF, event("SubagentStop", agent_id="b1", agent_type="apb:builder",
                             last_assistant_message=self.DEPARTED), d)
        self.assertEqual(r.returncode, 0)
        self.assertEqual([e["event"] for e in events(d)], ["Departure"])

    def test_records_nothing_elsewhere(self):
        for d in [neither(), make(manifest=REAL_MANIFEST), make(hook=True), make()]:
            run(BRIEF, event("SubagentStop", agent_id="b1", agent_type="apb:builder",
                             last_assistant_message=self.DEPARTED), d)
            self.assertFalse(os.path.exists(os.path.join(d, ".apb")), d)

    def test_resolve_works_in_the_apb_repo(self):
        d = apb_repo()
        run(BRIEF, event("SubagentStop", agent_id="b1", agent_type="apb:builder",
                         last_assistant_message=self.DEPARTED), d)
        r = run(BRIEF, b"", None, args=["resolve", "launched the builder again"], cwd=d)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("Resolved 1 departure.", r.stdout.decode())
        self.assertEqual([e["event"] for e in events(d)], ["Departure", "DepartureResolved"])

    def test_resolve_refuses_elsewhere(self):
        d = neither()
        r = run(BRIEF, b"", None, args=["resolve", "x"], cwd=d)
        self.assertEqual(r.returncode, 2)
        self.assertFalse(os.path.exists(os.path.join(d, ".apb")))

    def test_departure_holds_the_hand_off_in_the_apb_repo(self):
        # The chain: apb-brief.py records it, apb-stop.py holds the hand-off to the human.
        d = apb_repo()
        run(BRIEF, event("SubagentStop", agent_id="b1", agent_type="apb:builder",
                         last_assistant_message=self.DEPARTED), d)
        r = run(STOP, stop("1 thing for you to do right now: read the builder's report.", background_tasks=[]), d)
        note = sent_back(r)
        self.assertIsNotNone(note)
        self.assertIn("departed from its file", note)


class Popups(Clean):
    QUESTION = {"questions": [{"question": "Which?", "options": [{"label": "a"}, {"label": "b"}]}]}

    def ask(self, d):
        return run(POPUPS, event("PreToolUse", tool_name="AskUserQuestion", tool_input=self.QUESTION), d)

    def test_refuses_in_the_apb_repo(self):
        self.assertTrue(refused(self.ask(apb_repo())))

    def test_lets_through_elsewhere(self):
        for d in [neither(), make(manifest=REAL_MANIFEST), make(hook=True), make()]:
            r = self.ask(d)
            self.assertEqual(r.returncode, 0)
            self.assertFalse(refused(r), d)


def acts(script, d):
    """True if the hook acted in folder d on an event it acts on wherever it acts."""
    if script == EVENTS:
        run(EVENTS, event("UserPromptSubmit", prompt="hi"), d)
        return bool(events(d))
    if script == POPUPS:
        return refused(run(POPUPS, event("PreToolUse", tool_name="AskUserQuestion",
                                         tool_input=Popups.QUESTION), d))
    if script == BRIEF:
        run(BRIEF, event("SubagentStop", agent_id="b1", agent_type="apb:builder",
                         last_assistant_message=Brief.DEPARTED), d)
        return bool(events(d))
    # STOP: a turn that ran out, written by hand, so this checks the Stop hook alone.
    os.makedirs(os.path.join(d, ".apb"), exist_ok=True)
    with open(os.path.join(d, ".apb", "events.jsonl"), "w") as f:
        for e, agent in [("UserPromptSubmit", None), ("SubagentStart", "b1"), ("SubagentStop", "b1")]:
            line = {"time": "2026-11-21T10:00:00.000Z", "event": e, "session": "s1"}
            if agent:
                line["agent_id"] = agent
            f.write(json.dumps(line) + "\n")
    return sent_back(run(STOP, stop("Nothing for you to do.", background_tasks=[]), d)) is not None


class RandomFolders(Clean):
    def test_every_hook_acts_exactly_where_the_rule_says(self):
        # Random folder shapes and hooks, against the oracle: one rule for all four hooks (the watcher's
        # own random test is in test_apb_watch_start.py).
        rng = random.Random(132)
        for i in range(48):
            shape = (rng.random() < 0.3, rng.choice(MANIFESTS), rng.random() < 0.6)
            script = rng.choice([EVENTS, STOP, BRIEF, POPUPS])
            d = make(*shape)
            try:
                self.assertEqual(acts(script, d), oracle(*shape), (i, os.path.basename(script), shape))
            finally:
                shutil.rmtree(d, ignore_errors=True)


class NeverInTheWay(Clean):
    def test_every_hook_exits_0_on_bad_input_in_the_apb_repo(self):
        d = apb_repo()
        for script in [EVENTS, STOP, BRIEF, POPUPS]:
            for raw in [b"", b"not json", b"[]", b"{}", json.dumps({"hook_event_name": "Stop"}).encode()]:
                r = run(script, raw, d)
                self.assertEqual(r.returncode, 0, (script, raw, r.stderr))


if __name__ == "__main__":
    unittest.main()
