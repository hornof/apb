"""Tests for apb-popups.py, its entry in hooks.json and the setup steps that drive it. Run from the repo
root: python3 -m unittest hooks.test_apb_popups   (or: python3 hooks/test_apb_popups.py)

The hook runs the way Claude Code runs it, with the event's JSON on stdin, in a temporary project
folder. Both directions are tested: pop-ups refused outside setup and let through during it, and the
marker's whole round trip (start, claim, done, start again). A random test checks the hook against an
oracle written here from the rule, not from the hook's code.
"""
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(HERE, "apb-popups.py")
HOOKS_JSON = os.path.join(HERE, "hooks.json")
SETUP = os.path.join(ROOT, "skills", "apb-setup", "SKILL.md")
PLAYBOOK = os.path.join(ROOT, "docs", "playbook.md")

ASK = "AskUserQuestion"
WRAP = "mcp__ccd_turn__wrap_up"
UNCLAIMED_MAX = 2 * 3600  # the rule: an unclaimed marker older than 2 hours is stale


def question():
    return {"questions": [{"question": "Public or private?", "header": "Visibility", "multiSelect": False,
                           "options": [{"label": "Private", "description": "Only you"},
                                       {"label": "Public", "description": "Anyone"}]}]}


def run_hook(project, tool, tool_input, session="s1", env_project=True):
    event = {"hook_event_name": "PreToolUse", "session_id": session, "cwd": project, "tool_name": tool,
             "tool_input": tool_input}
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if env_project:
        env["CLAUDE_PROJECT_DIR"] = project
    r = subprocess.run([sys.executable, SCRIPT], input=json.dumps(event).encode(), capture_output=True,
                       env=env, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout) if r.stdout.strip() else None


def cli(project, *args):
    return subprocess.run([sys.executable, SCRIPT] + list(args), cwd=project, capture_output=True, text=True,
                          timeout=20)


def refused(out):
    return bool(out) and out["hookSpecificOutput"]["permissionDecision"] == "deny"


def reason(out):
    return out["hookSpecificOutput"]["permissionDecisionReason"]


class Project(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.makedirs(os.path.join(self.dir, "docs"))
        with open(os.path.join(self.dir, "docs", "MAP.md"), "w") as f:
            f.write("# Map\n")
        self.marker = os.path.join(self.dir, ".apb", "setup-running")

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def write_marker(self, started, session=None, raw=None):
        os.makedirs(os.path.dirname(self.marker), exist_ok=True)
        with open(self.marker, "w") as f:
            f.write(raw if raw is not None else json.dumps({"started": started, "session": session}))


class OutsideSetup(Project):
    def test_ask_user_question_is_refused_with_the_hand_off_format(self):
        out = run_hook(self.dir, ASK, question())
        self.assertTrue(refused(out))
        text = reason(out)
        self.assertIn("in chat", text)
        self.assertIn("1 thing for you to do right now", text)
        self.assertIn("things for you to do right now", text)
        self.assertIn("/apb-setup", text)

    def test_wrap_up_with_a_question_or_buttons_is_refused(self):
        for tool_input in ({"recap": "Done.", "ask": "Merge it?"},
                           {"recap": "Done.", "actions": [{"label": "Yes"}]},
                           {"recap": "Done.", "ask": "Merge it?", "actions": [{"action": "commit"}]}):
            out = run_hook(self.dir, WRAP, tool_input)
            self.assertTrue(refused(out), tool_input)
            self.assertIn("recap only", reason(out))
            self.assertIn("1 thing for you to do right now", reason(out))

    def test_recap_only_wrap_up_goes_through(self):
        for tool_input in ({"recap": "Done."}, {"recap": "Done.", "ask": "", "actions": []},
                           {"recap": "Done.", "ask": "   "}):
            self.assertIsNone(run_hook(self.dir, WRAP, tool_input), tool_input)

    def test_other_tools_go_through(self):
        for tool in ("Bash", "Agent", "Read", "mcp__ccd_session__start_session"):
            self.assertIsNone(run_hook(self.dir, tool, {"ask": "x", "questions": [1]}))

    def test_not_an_apb_project_is_left_alone(self):
        os.remove(os.path.join(self.dir, "docs", "MAP.md"))
        self.assertIsNone(run_hook(self.dir, ASK, question()))
        self.assertIsNone(run_hook(self.dir, WRAP, {"recap": "x", "ask": "y"}))

    def test_the_apb_repo_itself_counts(self):
        # The pop-up that started issue #117 came from a session on the apb repo, which has no docs/MAP.md.
        os.remove(os.path.join(self.dir, "docs", "MAP.md"))
        os.makedirs(os.path.join(self.dir, ".claude-plugin"))
        with open(os.path.join(self.dir, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "apb", "version": "0.0.1"}, f)
        self.assertTrue(refused(run_hook(self.dir, ASK, question())))
        with open(os.path.join(self.dir, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "other-plugin"}, f)
        self.assertIsNone(run_hook(self.dir, ASK, question()))

    def test_project_from_cwd_when_no_env(self):
        self.assertTrue(refused(run_hook(self.dir, ASK, question(), env_project=False)))

    def test_bad_input_never_fails(self):
        for raw in (b"", b"not json", b"[]", b'{"hook_event_name": "PreToolUse"}',
                    json.dumps({"hook_event_name": "PreToolUse", "tool_name": WRAP, "tool_input": "x"}).encode()):
            r = subprocess.run([sys.executable, SCRIPT], input=raw, capture_output=True, timeout=20,
                               env=dict(os.environ, CLAUDE_PROJECT_DIR=self.dir))
            self.assertEqual(r.returncode, 0)
            self.assertEqual(r.stdout.strip(), b"")


class DuringSetup(Project):
    def test_round_trip(self):
        # start -> allowed (and claimed) -> other session refused -> done -> refused -> start -> allowed
        self.assertTrue(refused(run_hook(self.dir, ASK, question(), session="a")))
        self.assertEqual(cli(self.dir, "setup-start").returncode, 0)
        self.assertTrue(os.path.isfile(self.marker))
        self.assertIsNone(run_hook(self.dir, ASK, question(), session="a"))
        self.assertIsNone(run_hook(self.dir, WRAP, {"recap": "x", "ask": "Public?"}, session="a"))
        self.assertTrue(refused(run_hook(self.dir, ASK, question(), session="b")))
        self.assertIsNone(run_hook(self.dir, ASK, question(), session="a"))
        self.assertEqual(cli(self.dir, "setup-done").returncode, 0)
        self.assertFalse(os.path.exists(self.marker))
        self.assertTrue(refused(run_hook(self.dir, ASK, question(), session="a")))
        # A setup run again, in a new session, starts fresh.
        cli(self.dir, "setup-start")
        self.assertIsNone(run_hook(self.dir, ASK, question(), session="c"))
        self.assertTrue(refused(run_hook(self.dir, ASK, question(), session="a")))

    def test_setup_start_works_before_docs_map_exists(self):
        # Step 0 runs in an empty folder: no docs/MAP.md yet, no .apb/.
        shutil.rmtree(os.path.join(self.dir, "docs"))
        r = cli(self.dir, "setup-start")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(os.path.isfile(self.marker))
        with open(os.path.join(self.dir, ".apb", ".gitignore")) as f:
            self.assertEqual(f.read(), "*\n")

    def test_setup_done_without_a_marker_is_fine(self):
        self.assertEqual(cli(self.dir, "setup-done").returncode, 0)

    def test_unknown_command_fails(self):
        self.assertNotEqual(cli(self.dir, "setup-maybe").returncode, 0)

    def test_marker_present_fresh_and_unclaimed_is_claimed_by_the_first_session(self):
        self.write_marker(time.time() - 60)
        self.assertIsNone(run_hook(self.dir, ASK, question(), session="first"))
        with open(self.marker) as f:
            self.assertEqual(json.load(f)["session"], "first")

    def test_stale_unclaimed_marker_is_ignored(self):
        # A setup that crashed before its first pop-up.
        self.write_marker(time.time() - UNCLAIMED_MAX - 60)
        out = run_hook(self.dir, ASK, question(), session="later")
        self.assertTrue(refused(out))

    def test_marker_claimed_by_a_crashed_setup_is_ignored_by_other_sessions(self):
        self.write_marker(time.time() - 3 * 86400, session="crashed")
        self.assertTrue(refused(run_hook(self.dir, ASK, question(), session="later")))
        # ...but the setup's own session, resumed, still gets its cards, however long the pause.
        self.assertIsNone(run_hook(self.dir, ASK, question(), session="crashed"))

    def test_broken_marker_counts_as_absent(self):
        for raw in ("", "{", "[]", '{"started": "soon"}', '{"session": "a"}'):
            self.write_marker(0, raw=raw)
            self.assertTrue(refused(run_hook(self.dir, ASK, question(), session="a")), raw)


def oracle(apb, marker, session, tool, tool_input, now):
    """The rule, written from the issue: refuse a pop-up in an apb project unless this session runs setup."""
    if not apb:
        return False
    if tool == ASK:
        popup = True
    elif tool == WRAP and isinstance(tool_input, dict):
        ask = tool_input.get("ask")
        popup = (isinstance(ask, str) and ask.strip() != "") or bool(tool_input.get("actions"))
    else:
        popup = False
    if not popup:
        return False
    if marker is None:
        return True
    started, owner = marker
    if owner is None:
        return now - started > UNCLAIMED_MAX
    return owner != session


class RandomAgainstOracle(Project):
    def test_random_events(self):
        rng = random.Random(117)
        for i in range(60):
            apb = rng.random() < 0.85
            map_md = os.path.join(self.dir, "docs", "MAP.md")
            if apb and not os.path.exists(map_md):
                open(map_md, "w").close()
            if not apb and os.path.exists(map_md):
                os.remove(map_md)
            marker = rng.choice([None, "fresh", "stale", "mine", "other"])
            session = rng.choice(["s1", "s2"])
            now = time.time()
            if marker is None:
                if os.path.exists(self.marker):
                    os.remove(self.marker)
                state = None
            else:
                started = now - (60 if marker in ("fresh", "mine", "other") else UNCLAIMED_MAX + 600)
                owner = {"mine": session, "other": "s9"}.get(marker)
                self.write_marker(started, owner)
                state = (started, owner)
            tool = rng.choice([ASK, WRAP, WRAP, "Bash"])
            tool_input = {"recap": "r"}
            if rng.random() < 0.5:
                tool_input["ask"] = rng.choice(["", " ", "Go ahead?"])
            if rng.random() < 0.5:
                tool_input["actions"] = rng.choice([[], [{"label": "Yes"}]])
            expected = oracle(apb, state, session, tool, tool_input, now)
            got = refused(run_hook(self.dir, tool, tool_input, session=session))
            self.assertEqual(got, expected, (i, apb, marker, session, tool, tool_input))


class Wiring(unittest.TestCase):
    def test_hooks_json_runs_it_before_both_tools(self):
        with open(HOOKS_JSON) as f:
            entries = json.load(f)["hooks"]["PreToolUse"]
        mine = [e for e in entries if any("apb-popups.py" in " ".join(h.get("args", [])) for h in e["hooks"])]
        self.assertEqual(len(mine), 1)
        matcher = mine[0]["matcher"]
        for tool in (ASK, WRAP):
            self.assertTrue(re.fullmatch(matcher, tool), tool)
        for tool in ("Bash", "Agent", "mcp__ccd_turn__update_plan"):
            self.assertFalse(re.fullmatch(matcher, tool), tool)
        hook = mine[0]["hooks"][0]
        self.assertFalse(hook.get("async"))
        self.assertLessEqual(hook.get("timeout", 60), 10)

    def test_setup_writes_the_marker_at_step_0_and_removes_it_at_step_7(self):
        with open(SETUP) as f:
            text = f.read()
        steps = re.split(r"\n(?=\d+\. )", text)
        step0 = next(s for s in steps if s.startswith("0. "))
        step7 = next(s for s in steps if s.startswith("7. "))
        self.assertIn("apb-popups.py setup-start", step0)
        self.assertIn("apb-popups.py setup-done", step7)
        # The marker comes after the folder check, which writes nothing when the folder is wrong.
        self.assertGreater(step0.index("setup-start"), step0.index("pwd -P"))

    def test_playbook_names_the_hook(self):
        with open(PLAYBOOK) as f:
            line = next(l for l in f if "not with pop-up buttons" in l)
        self.assertIn("apb-popups.py", line)


if __name__ == "__main__":
    unittest.main()
