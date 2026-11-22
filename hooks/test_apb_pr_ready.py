"""Tests for apb-pr-ready.py (#156): marking a PR ready moves its card to the top of In review, the
moment `gh pr ready` succeeds; opening a draft moves nothing. Run from the repo root:
python3 -m unittest hooks/test_apb_pr_ready.py

The hook reads gh's own success line, so the tests feed it the payloads Claude Code sends after a Bash
call. The end-to-end tests run the hook for real with a fake `gh` that keeps a board in a file (the
fake board of scripts/test_board_move.py), and check the board afterwards, not the calls made.
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
SCRIPT = os.path.join(HERE, "apb-pr-ready.py")

spec = importlib.util.spec_from_file_location("apb_pr_ready", SCRIPT)
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

READY = '✓ Pull request hum/proj#160 is marked as "ready for review"\n'
UNDO = '✓ Pull request hum/proj#160 is converted to "draft"\n'
ALREADY = '! Pull request hum/proj#160 is already "ready for review"\n'
CREATED = "https://github.com/hum/proj/pull/160\n"


def payload(command, stderr="", stdout="", cwd=None, tool="Bash", event="PostToolUse"):
    return {"hook_event_name": event, "tool_name": tool, "session_id": "s", "cwd": cwd or "/nowhere",
            "tool_input": {"command": command},
            "tool_response": {"stdout": stdout, "stderr": stderr, "interrupted": False}}


class Moves(unittest.TestCase):
    def test_gh_pr_ready_moves_to_in_review(self):
        self.assertEqual(hook.moves(payload("gh pr ready", stderr=READY)), [("pr-ready", "hum/proj", 160)])

    def test_with_a_number_and_repo_and_on_stdout(self):
        p = payload("gh pr ready 160 --repo hum/proj", stdout=READY)
        self.assertEqual(hook.moves(p), [("pr-ready", "hum/proj", 160)])

    def test_after_cd(self):
        p = payload("cd /x/y && gh pr ready", stderr=READY)
        self.assertEqual(hook.moves(p), [("pr-ready", "hum/proj", 160)])

    def test_undo_moves_back_to_in_progress(self):
        self.assertEqual(hook.moves(payload("gh pr ready --undo", stderr=UNDO)), [("pr-draft", "hum/proj", 160)])

    def test_opening_a_draft_moves_nothing(self):
        # #156: the draft opens at the start of the build; its card stays In progress.
        self.assertEqual(hook.moves(payload("gh pr create --draft --title t --body b", stdout=CREATED)), [])

    def test_already_ready_moves_nothing(self):
        self.assertEqual(hook.moves(payload("gh pr ready", stderr=ALREADY)), [])

    def test_a_failed_ready_moves_nothing(self):
        self.assertEqual(hook.moves(payload("gh pr ready", stderr="GraphQL: Resource not accessible\n")), [])

    def test_the_line_without_the_command_moves_nothing(self):
        # Printing gh's words (a test, a log) is not marking a PR ready.
        self.assertEqual(hook.moves(payload("cat notes.txt", stdout=READY)), [])
        self.assertEqual(hook.moves(payload("echo gh pr ready", stdout="gh pr ready\n")), [])

    def test_other_tools_and_events_move_nothing(self):
        self.assertEqual(hook.moves(payload("gh pr ready", stderr=READY, tool="Read")), [])
        self.assertEqual(hook.moves(payload("gh pr ready", stderr=READY, event="PreToolUse")), [])

    def test_odd_payloads_move_nothing(self):
        for p in ({}, {"tool_name": "Bash"}, {"hook_event_name": "PostToolUse", "tool_name": "Bash",
                                               "tool_input": "x", "tool_response": None}, []):
            self.assertEqual(hook.moves(p), [])

    def test_a_string_response(self):
        p = payload("gh pr ready")
        p["tool_response"] = READY
        self.assertEqual(hook.moves(p), [("pr-ready", "hum/proj", 160)])


def oracle_moves(command, text):
    """The moves, worked out separately: plain word splitting for the command, string slicing for gh's
    lines."""
    words = command.replace("&&", " ").replace(";", " ").replace("|", " ").split()
    if not any(words[i:i + 3] == ["gh", "pr", "ready"] for i in range(len(words))):
        return []
    found = []
    for line in text.splitlines():
        start = line.find("Pull request ")
        if start < 0:
            continue
        rest = line[start + len("Pull request "):]
        ref, _, what = rest.partition(" is ")
        repo, _, number = ref.partition("#")
        if what == 'marked as "ready for review"':
            step = "pr-ready"
        elif what == 'converted to "draft"':
            step = "pr-draft"
        else:
            continue
        move = (step, repo, int(number))
        if move not in found:
            found.append(move)
    return found


class RandomPayloads(unittest.TestCase):
    def test_against_the_oracle(self):
        rng = random.Random(156)
        commands = ["gh pr ready", "gh pr ready --undo", "gh pr ready 12 -R a/b", "cd /x && gh pr ready",
                    "gh pr create --draft --title t", "gh pr view 12", "echo done", "git push; gh pr ready 7",
                    "gh  pr  ready", "ghx pr ready", "gh pr ready-ish"]
        kinds = ['is marked as "ready for review"', 'is converted to "draft"', 'is already "ready for review"',
                 'is already "in draft"']
        for _ in range(500):
            lines = []
            for _ in range(rng.randint(0, 3)):
                roll = rng.random()
                if roll < 0.7:
                    lines.append("%s Pull request %s#%d %s" % (rng.choice(["✓", "!", ""]),
                                                               rng.choice(["hum/proj", "a/b", "o.x/r-y"]),
                                                               rng.randint(1, 999), rng.choice(kinds)))
                else:
                    lines.append(rng.choice(["GraphQL: Not Found", "https://github.com/a/b/pull/3", ""]))
            command = rng.choice(commands)
            text = "\n".join(lines)
            where = rng.choice(["stdout", "stderr"])
            p = payload(command, **{where: text})
            self.assertEqual(hook.moves(p), oracle_moves(command, text), (command, text))


FAKE_GH = r'''
import importlib.util, json, os, sys
here = os.path.dirname(os.path.abspath(__file__))
state_path = os.path.join(here, "board.json")
with open(state_path) as f:
    state = json.load(f)
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
if state.get("fail"):
    sys.exit("HTTP 502")
if state.get("hang"):
    import time
    time.sleep(60)
spec = importlib.util.spec_from_file_location("tbm", %(tbm)r)
tbm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tbm)
a = sys.argv[1:]
assert a[:2] == ["api", "graphql"], a
fields, query = {}, None
for flag, kv in zip(a[2::2], a[3::2]):
    k, v = kv.split("=", 1)
    if k == "query":
        query = v
    else:
        fields[k] = int(v) if flag == "-F" else v
b = tbm.FakeBoard(state["items"], {int(k): v for k, v in state["issues"].items()},
                  {int(k): v for k, v in state["prs"].items()})
print(json.dumps(b(query, **fields)))
state["items"] = b.items
with open(state_path, "w") as f:
    json.dump(state, f)
'''


class EndToEnd(unittest.TestCase):
    def setUp(self):
        self.dirs = []
        self.project = self.tmp()
        os.makedirs(os.path.join(self.project, "docs"))
        open(os.path.join(self.project, "docs", "MAP.md"), "w").close()
        bin_dir = self.tmp()
        self.gh = os.path.join(bin_dir, "gh")
        with open(self.gh, "w") as f:
            f.write("#!%s\n%s" % (sys.executable, FAKE_GH % {"tbm": os.path.join(ROOT, "scripts", "test_board_move.py")}))
        os.chmod(self.gh, 0o755)
        self.state_path = os.path.join(bin_dir, "board.json")
        self.calls = os.path.join(bin_dir, "calls.log")
        self.write({"items": [{"id": "I2", "issue": 2, "status": "In review"},
                              {"id": "I156", "issue": 156, "status": "In progress"},
                              {"id": "I9", "issue": 9, "status": "Todo"}],
                    "issues": {"2": "OPEN", "156": "OPEN", "9": "OPEN"},
                    "prs": {"160": {"state": "OPEN", "draft": True, "closes": [156]}}})

    def tmp(self):
        d = tempfile.mkdtemp()
        self.dirs.append(d)
        return d

    def tearDown(self):
        for d in self.dirs:
            shutil.rmtree(d, ignore_errors=True)

    def write(self, state):
        with open(self.state_path, "w") as f:
            json.dump(state, f)

    def state(self):
        with open(self.state_path) as f:
            return json.load(f)

    def set_draft(self, draft):
        s = self.state()
        s["prs"]["160"]["draft"] = draft
        self.write(s)

    def column(self, name):
        return [i["issue"] for i in self.state()["items"] if i["status"] == name]

    def gh_calls(self):
        if not os.path.exists(self.calls):
            return []
        with open(self.calls) as f:
            return [json.loads(l) for l in f if l.strip()]

    def run_hook(self, p, budget=20, project=None):
        env = dict(os.environ, CLAUDE_PROJECT_DIR=project or self.project)
        started = time.time()
        r = subprocess.run([sys.executable, SCRIPT, "--gh", self.gh, "--budget", str(budget)],
                           input=json.dumps(p), capture_output=True, text=True, env=env, timeout=60)
        self.took = time.time() - started
        self.assertEqual(r.returncode, 0, r.stderr)
        if not r.stdout.strip():
            return None
        out = json.loads(r.stdout)
        self.assertEqual(out["hookSpecificOutput"]["hookEventName"], "PostToolUse")
        return out["hookSpecificOutput"]["additionalContext"]

    def test_draft_open_then_ready_then_undo(self):
        # #156's "done when": the draft opening leaves the card In progress ...
        said = self.run_hook(payload("gh pr create --draft --title t", stdout=CREATED, cwd=self.project))
        self.assertIsNone(said)
        self.assertEqual(self.column("In progress"), [156])
        self.assertEqual(self.gh_calls(), [])
        # ... marking it ready moves the card to the top of In review ...
        self.set_draft(False)
        said = self.run_hook(payload("gh pr ready", stderr=READY, cwd=self.project))
        self.assertEqual(self.column("In review"), [156, 2])
        self.assertEqual(self.column("In progress"), [])
        self.assertIn("#156", said)
        self.assertIn("In review", said)
        # The note usually lands in the builder's session, and a builder never reviews its own PR.
        self.assertNotIn("apb-review", said)
        # ... and turning it back into a draft moves it back.
        self.set_draft(True)
        said = self.run_hook(payload("gh pr ready --undo", stderr=UNDO, cwd=self.project))
        self.assertEqual(self.column("In progress"), [156])
        self.assertEqual(self.column("In review"), [2])
        self.assertIn("In progress", said)
        self.assertNotIn("apb-review", said)
        self.assertEqual(self.column("Todo"), [9])

    def test_a_refusal_moves_nothing_and_says_why(self):
        # The line says ready, but GitHub says draft (it changed since): nothing moves.
        said = self.run_hook(payload("gh pr ready", stderr=READY, cwd=self.project))
        self.assertEqual(self.column("In progress"), [156])
        self.assertIn("draft", said)

    def test_a_gh_failure_never_blocks_and_names_the_command(self):
        s = self.state()
        s["fail"] = True
        self.write(s)
        said = self.run_hook(payload("gh pr ready", stderr=READY, cwd=self.project))
        self.assertIn("board-move.py pr-ready 160", said)
        self.assertEqual(self.column("In progress"), [156])

    def test_a_hanging_gh_stays_within_the_budget(self):
        s = self.state()
        s["hang"] = True
        self.write(s)
        said = self.run_hook(payload("gh pr ready", stderr=READY, cwd=self.project), budget=2)
        self.assertLess(self.took, 15)
        self.assertIn("board-move.py pr-ready 160", said)

    def test_outside_an_apb_folder_it_does_nothing(self):
        said = self.run_hook(payload("gh pr ready", stderr=READY, cwd="/"), project=self.tmp())
        self.assertIsNone(said)
        self.assertEqual(self.gh_calls(), [])

    def test_a_bad_payload_exits_0_quietly(self):
        r = subprocess.run([sys.executable, SCRIPT, "--gh", self.gh], input="not json", capture_output=True,
                           text=True, timeout=60, env=dict(os.environ, CLAUDE_PROJECT_DIR=self.project))
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "")


class Registered(unittest.TestCase):
    def test_hooks_json_runs_it_after_bash(self):
        with open(os.path.join(HERE, "hooks.json"), encoding="utf-8") as f:
            config = json.load(f)
        groups = [g for g in config["hooks"]["PostToolUse"]
                  if any(a.endswith("/apb-pr-ready.py") for h in g["hooks"] for a in h.get("args", []))]
        self.assertEqual(len(groups), 1)
        self.assertEqual(groups[0].get("matcher"), "Bash")
        h = groups[0]["hooks"][0]
        self.assertFalse(h.get("async"), "its note must reach the session that ran gh pr ready")
        self.assertGreaterEqual(h["timeout"], hook.BUDGET + 5)
        self.assertIn("gh pr ready", config["description"])


if __name__ == "__main__":
    unittest.main()
