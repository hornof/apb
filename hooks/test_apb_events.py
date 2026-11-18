"""Tests for apb-events.py and hooks.json. Run from the repo root: python3 -m unittest discover hooks

Each test runs the script the way Claude Code does: a JSON payload on stdin, the project folder in
CLAUDE_PROJECT_DIR. It then reads .apb/events.jsonl back as JSON and compares it with the payload.
"""
import calendar
import importlib.util
import io
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "apb-events.py")
HOOKS_JSON = os.path.join(HERE, "hooks.json")
EVENTS = ["UserPromptSubmit", "SubagentStart", "SubagentStop", "PostToolUse", "PostToolUseFailure",
          "TaskCreated", "TaskCompleted", "Stop", "Notification"]

def run(payload, project, cwd=None):
    """Run the hook. payload is a dict (sent as JSON) or raw bytes. Returns the finished process."""
    data = payload if isinstance(payload, bytes) else json.dumps(payload).encode()
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if project is not None:
        env["CLAUDE_PROJECT_DIR"] = project
    return subprocess.run([sys.executable, SCRIPT], input=data, capture_output=True,
                          env=env, cwd=cwd or tempfile.gettempdir(), timeout=10)


def apb_project():
    """A temporary folder that looks like an apb project (it has docs/MAP.md)."""
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "docs", "MAP.md"), "w") as f:
        f.write("# map\n")
    return d


def lines(project):
    path = os.path.join(project, ".apb", "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f.read().splitlines()]


def base(event, **extra):
    p = {"session_id": "s1", "transcript_path": "/x.jsonl", "cwd": "/somewhere",
         "hook_event_name": event}
    p.update(extra)
    return p


class Writes(unittest.TestCase):
    def setUp(self):
        self.project = apb_project()

    def one(self, payload):
        r = run(payload, self.project)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, b"")
        got = lines(self.project)
        self.assertEqual(len(got), 1)
        return got[0]

    def test_subagent_start_has_agent_id_and_type(self):
        line = self.one(base("SubagentStart", agent_id="agent-1", agent_type="apb:builder"))
        self.assertEqual(line["event"], "SubagentStart")
        self.assertEqual(line["agent_id"], "agent-1")
        self.assertEqual(line["agent_type"], "apb:builder")
        self.assertEqual(line["session"], "s1")

    def test_subagent_stop_leaves_out_the_final_message(self):
        line = self.one(base("SubagentStop", agent_id="a2", agent_type="apb:reviewer",
                             last_assistant_message="x" * 5000, agent_transcript_path="/t"))
        self.assertEqual(line["agent_type"], "apb:reviewer")
        self.assertNotIn("x" * 100, json.dumps(line))

    def test_bash_success_has_command_and_exit_0(self):
        line = self.one(base("PostToolUse", tool_name="Bash", agent_id="a3",
                             tool_input={"command": "npm test", "description": "d"},
                             tool_response={"stdout": "lots of output " * 500, "stderr": "",
                                            "interrupted": False}, duration_ms=4187))
        self.assertEqual(line["command"], "npm test")
        self.assertEqual(line["exit"], 0)
        self.assertEqual(line["duration_ms"], 4187)
        self.assertNotIn("lots of output", json.dumps(line))

    def test_bash_failure_has_the_exit_code(self):
        line = self.one(base("PostToolUseFailure", tool_name="Bash",
                             tool_input={"command": "npm test"},
                             error="Exit code 3\nError: Cannot find module", is_interrupt=False))
        self.assertEqual(line["command"], "npm test")
        self.assertEqual(line["exit"], 3)
        self.assertNotIn("Cannot find module", json.dumps(line))

    def test_interrupted_bash_is_marked(self):
        line = self.one(base("PostToolUseFailure", tool_name="Bash", tool_input={"command": "sleep 99"},
                             error="Interrupted by user", is_interrupt=True))
        self.assertIsNone(line["exit"])
        self.assertTrue(line["interrupted"])

    def test_background_bash_has_no_exit_status_yet(self):
        line = self.one(base("PostToolUse", tool_name="Bash",
                             tool_input={"command": "npm run dev", "run_in_background": True},
                             tool_response={"stdout": "", "stderr": "", "interrupted": False}))
        self.assertIsNone(line["exit"])
        self.assertTrue(line["background"])

    def test_long_command_is_cut(self):
        line = self.one(base("PostToolUse", tool_name="Bash", tool_input={"command": "echo " + "y" * 5000},
                             tool_response={}))
        self.assertLessEqual(len(line["command"]), 300)
        self.assertTrue(line["command"].startswith("echo yyy"))

    def test_other_tools_are_not_logged(self):
        r = run(base("PostToolUse", tool_name="Edit", tool_input={"file_path": "/a"}), self.project)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(lines(self.project), [])

    def test_task_events_have_id_and_subject(self):
        for event in ["TaskCreated", "TaskCompleted"]:
            run(base(event, task_id="t-1", task_subject="Write the tests",
                     task_description="long " * 400), self.project)
        got = lines(self.project)
        self.assertEqual([g["event"] for g in got], ["TaskCreated", "TaskCompleted"])
        for g in got:
            self.assertEqual(g["task_id"], "t-1")
            self.assertEqual(g["task_subject"], "Write the tests")
            self.assertNotIn("task_description", g)

    def test_notification_has_its_type(self):
        line = self.one(base("Notification", message="Claude needs your permission",
                             notification_type="permission_prompt"))
        self.assertEqual(line["notification_type"], "permission_prompt")

    def test_stop_is_logged(self):
        line = self.one(base("Stop", stop_hook_active=False, last_assistant_message="done"))
        self.assertEqual(line["event"], "Stop")
        self.assertNotIn("agent_id", line)

    def test_prompt_is_logged_without_its_text(self):
        line = self.one(base("UserPromptSubmit", prompt="my secret plan " * 50))
        self.assertEqual(line["event"], "UserPromptSubmit")
        self.assertNotIn("secret", json.dumps(line))

    def test_a_routine_reply_is_marked_routine(self):
        for text, routine in [("go", True), ("Resolved.", True), ("b", True), ("9: a", True), ("merged", True),
                              ("why is the build red?", False), ("go ahead and rename it", False)]:
            run(base("UserPromptSubmit", prompt=text), self.project)
        got = lines(self.project)
        self.assertEqual([g["routine"] for g in got], [True, True, True, True, True, False, False])
        self.assertTrue(all("automatic" not in g for g in got))
        self.assertNotIn("rename", json.dumps(got))

    def test_a_prompt_claude_code_sent_itself_is_marked_automatic(self):
        line = self.one(base("UserPromptSubmit",
                             prompt="<task-notification>\n<task-id>x</task-id>\nagent done</task-notification>"))
        self.assertTrue(line["automatic"])
        self.assertNotIn("routine", line)
        self.assertNotIn("agent done", json.dumps(line))

    def test_a_prompt_with_no_text_has_neither_mark(self):
        line = self.one(base("UserPromptSubmit"))
        self.assertNotIn("routine", line)
        self.assertNotIn("automatic", line)

    def test_time_is_now_in_utc(self):
        before = time.time()
        line = self.one(base("Stop"))
        after = time.time()
        t = calendar.timegm(time.strptime(line["time"][:19], "%Y-%m-%dT%H:%M:%S"))
        self.assertTrue(line["time"].endswith("Z"))
        self.assertGreaterEqual(t, int(before) - 1)
        self.assertLessEqual(t, after + 1)

    def test_lines_are_appended_in_order(self):
        for i in range(5):
            run(base("TaskCreated", task_id=str(i), task_subject="s"), self.project)
        self.assertEqual([g["task_id"] for g in lines(self.project)], ["0", "1", "2", "3", "4"])

    def test_the_folder_ignores_itself_in_git(self):
        run(base("Stop"), self.project)
        with open(os.path.join(self.project, ".apb", ".gitignore")) as f:
            self.assertEqual(f.read().strip(), "*")

    def test_hooks_running_at_once_on_a_fresh_project_lose_nothing(self):
        for attempt in range(5):
            project = apb_project()
            env = dict(os.environ, CLAUDE_PROJECT_DIR=project)
            procs = [subprocess.Popen([sys.executable, SCRIPT], stdin=subprocess.PIPE, env=env)
                     for _ in range(8)]
            for i, proc in enumerate(procs):
                proc.stdin.write(json.dumps(base("TaskCreated", task_id=str(i), task_subject="s")).encode())
                proc.stdin.close()
            for proc in procs:
                self.assertEqual(proc.wait(timeout=10), 0)
            self.assertEqual(sorted(g["task_id"] for g in lines(project)), [str(i) for i in range(8)])
            with open(os.path.join(project, ".apb", ".gitignore")) as f:
                self.assertEqual(f.read().strip(), "*")

    def test_the_loser_of_a_race_to_make_the_folder_still_writes(self):
        # Another hook makes .apb/ between this hook's check and its own attempt to make it.
        spec = importlib.util.spec_from_file_location("apb_events", SCRIPT)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        old_env, old_stdin, old_mkdir = os.environ.get("CLAUDE_PROJECT_DIR"), sys.stdin, os.mkdir

        def other_hook_wins(path, *args, **kwargs):
            old_mkdir(path)  # the other hook makes the folder first
            raise FileExistsError(path)
        os.environ["CLAUDE_PROJECT_DIR"] = self.project
        sys.stdin = io.TextIOWrapper(io.BytesIO(json.dumps(base("Stop")).encode()))
        os.mkdir = other_hook_wins
        try:
            mod.main()
        finally:
            os.mkdir, sys.stdin = old_mkdir, old_stdin
            if old_env is None:
                os.environ.pop("CLAUDE_PROJECT_DIR")
            else:
                os.environ["CLAUDE_PROJECT_DIR"] = old_env
        self.assertEqual(len(lines(self.project)), 1)

    def test_project_comes_from_cwd_when_the_variable_is_missing(self):
        r = run(base("Stop", cwd=self.project), None)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(len(lines(self.project)), 1)


class Properties(unittest.TestCase):
    """Random payloads; each line, parsed back, must match its payload field by field."""

    def test_random_payloads_round_trip(self):
        rng = random.Random(22)
        project = apb_project()
        sent = []
        alphabet = "abcAZ09 _-'\"\\/$`|&;\n\té漢😀"
        for _ in range(60):
            event = rng.choice(EVENTS)
            word = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 40)))
            p = base(event, session_id=word)
            if rng.random() < 0.6:
                p["agent_id"], p["agent_type"] = "a" + word, "t" + word
            if event.startswith("PostToolUse"):
                p["tool_name"] = "Bash"
                p["tool_input"] = {"command": word * rng.randint(1, 20)}
                code = rng.randint(1, 255)
                if event == "PostToolUseFailure":
                    p["error"] = "Exit code %d\n%s" % (code, word)
                else:
                    p["tool_response"] = {"stdout": word, "stderr": "", "interrupted": False}
                p["_code"] = 0 if event == "PostToolUse" else code
            if event.startswith("Task"):
                p["task_id"], p["task_subject"] = word, word[::-1]
            if event == "Notification":
                p["notification_type"] = word
            sent.append(p)
            r = run({k: v for k, v in p.items() if k != "_code"}, project)
            self.assertEqual((r.returncode, r.stdout), (0, b""))
        got = lines(project)
        self.assertEqual(len(got), len(sent))
        for p, g in zip(sent, got):
            self.assertEqual(g["event"], p["hook_event_name"])
            self.assertEqual(g["session"], p["session_id"])
            self.assertEqual(g.get("agent_id"), p.get("agent_id"))
            self.assertEqual(g.get("agent_type"), p.get("agent_type"))
            if "tool_input" in p:
                self.assertEqual(g["command"], p["tool_input"]["command"][:300])
                self.assertEqual(g["exit"], p["_code"])
            if "task_id" in p:
                self.assertEqual((g["task_id"], g["task_subject"]), (p["task_id"], p["task_subject"]))
            if "notification_type" in p:
                self.assertEqual(g["notification_type"], p["notification_type"])
            self.assertLess(len(json.dumps(g, ensure_ascii=False).encode()), 2000)


class StaysQuiet(unittest.TestCase):
    """Whatever happens, the hook exits 0 and prints nothing."""

    def quiet(self, r):
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, b"")
        self.assertEqual(r.stderr, b"")

    def test_non_apb_folder_gets_nothing(self):
        d = tempfile.mkdtemp()
        self.quiet(run(base("Stop"), d))
        self.assertEqual(os.listdir(d), [])

    def test_missing_folder_gets_nothing(self):
        d = os.path.join(tempfile.mkdtemp(), "gone")
        self.quiet(run(base("Stop"), d))
        self.assertFalse(os.path.exists(d))

    def test_bad_input(self):
        project = apb_project()
        for bad in [b"", b"not json", b"[1, 2]", b"null", b"42", b'"text"', b"{", b"\xff\xfe\x00",
                    b'{"hook_event_name": 7}', b'{"hook_event_name": "PostToolUse", "tool_name": "Bash"}',
                    b'{"hook_event_name": "PostToolUse", "tool_name": "Bash", "tool_input": "x"}',
                    b'{"hook_event_name": "PostToolUseFailure", "tool_name": "Bash", "error": 5}']:
            self.quiet(run(bad, project))
        for line in lines(project):
            self.assertIsInstance(line["event"], str)

    def test_unwritable_log_folder(self):
        project = apb_project()
        with open(os.path.join(project, ".apb"), "w") as f:
            f.write("a file where the folder should be")
        self.quiet(run(base("Stop"), project))

    def test_it_is_fast(self):
        project = apb_project()
        start = time.time()
        for _ in range(5):
            run(base("Stop"), project)
        self.assertLess((time.time() - start) / 5, 1.0)


class HooksFile(unittest.TestCase):
    def setUp(self):
        with open(HOOKS_JSON) as f:
            self.hooks = json.load(f)["hooks"]

    # The only hooks Claude Code waits for, named one by one: (event, matcher, script). The Stop hook can
    # send a message back; the brief check refuses an agent launch (PreToolUse) and records a departure
    # before the orchestrator reads the report (SubagentStop). Every other hook is the events logger.
    WAITED_FOR = [("Stop", None, "apb-stop.py"), ("PreToolUse", "Agent|Task", "apb-brief.py"),
                  ("SubagentStop", None, "apb-brief.py")]

    def entries(self):
        """(event, matcher, script file name, hook) for every hook in hooks.json."""
        out = []
        for event, groups in self.hooks.items():
            for group in groups:
                for hook in group["hooks"]:
                    path = hook["args"][0].replace("${CLAUDE_PLUGIN_ROOT}", os.path.dirname(HERE))
                    self.assertTrue(os.path.isfile(path), path)
                    out.append((event, group.get("matcher"), os.path.basename(path), hook))
        return out

    def test_every_event_is_registered_once(self):
        logged = [event for event, _, script, _ in self.entries() if script == "apb-events.py"]
        self.assertEqual(sorted(logged), sorted(e for e in EVENTS if e != "Stop"))
        self.assertEqual(sorted(self.hooks), sorted(EVENTS + ["PreToolUse"]))

    def test_bash_only_for_tool_events(self):
        for event in ["PostToolUse", "PostToolUseFailure"]:
            self.assertEqual(self.hooks[event][0]["matcher"], "Bash")

    def test_every_hook_runs_the_script_in_the_background(self):
        waited = []
        for event, matcher, script, hook in self.entries():
            self.assertEqual(hook["type"], "command")
            self.assertEqual(hook["command"], "python3")
            if script == "apb-events.py":
                self.assertTrue(hook.get("async"), event)
            else:
                self.assertFalse(hook.get("async", False), event)
                self.assertLessEqual(hook["timeout"], 30, event)
                waited.append((event, matcher, script))
        self.assertEqual(sorted(waited, key=str), sorted(self.WAITED_FOR, key=str))


if __name__ == "__main__":
    unittest.main()
