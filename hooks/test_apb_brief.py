"""Tests for apb-brief.py and the departure check in apb-stop.py. Run from the repo root:
python3 -m unittest hooks.test_apb_brief   (or: python3 hooks/test_apb_brief.py)

The brief check is tested with hand-made briefs, and with random task-only briefs that must pass and
must be refused once any format instruction is put anywhere in them: the generator knows which ones
it put in. The departure check is tested the same way on agent reports. The hooks run the way
Claude Code runs them, with the event's JSON on stdin.
"""
import importlib.util
import json
import os
import random
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "apb-brief.py")
STOP = os.path.join(HERE, "apb-stop.py")
HOOKS_JSON = os.path.join(HERE, "hooks.json")


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


brief = load("apb_brief", SCRIPT)
stop = load("apb_stop_for_brief", STOP)

# The brief the orchestrator gave a builder: the task only.
GOOD_BUILDER = """Build issue #54 on hornof/apb: `gh issue view 54 --repo hornof/apb --comments`. Follow your own
agent file (agents/builder.md) for everything about how to work and how to write the PR. Put the issue's
"How we'll know it worked" line in the PR description.

- Worktree: /Users/x/src/apb-wt/issue-54, branch issue-54-brief-check, freshly made from origin/main.
- Scratch folder: /tmp/scratchpad/issue-54/.
- Bump `.claude-plugin/plugin.json` to 0.3.20. The PR closes #54.
- Command rule (the human sees every permission prompt): one plain command per shell call. No `$(...)`,
  loops, `&&`/`;` chains, heredocs or pipes into grep. Give every command a time limit.
- Never merge. Report back the PR link."""

GOOD_REVIEWER = """Review PR #57 on hornof/sketch-gallery. It holds code and docs. Your scratch folder is
/tmp/scratchpad/pr-57/; keep every file you write outside the repo there. Command rule: one plain
command per shell call, with a time limit."""

# A brief like the one that started this: it told the reviewer how to post.
BAD_REVIEWER = GOOD_REVIEWER + "\nPost the whole review as a single comment, and leave out items you rate low."

# Format instructions as an orchestrator might write them. Each must be refused on its own.
FORMAT = [
    "Post the review as a single comment.",
    "Put everything in a single comment.",
    "post as one comment",
    "Post it as a reply to the PR.",
    "Post them as review threads.",
    "Use this format for the review:",
    "Format the PR description like this:",
    "The review format is: Fix, then Notes.",
    "Use the following format.",
    "Write the report in this format.",
    "Leave out items that are only style.",
    "leave out the nits",
    "Leave out notes.",
    "One thread for everything.",
    "Use one thread per file.",
    "No index this time.",
    "Skip the index.",
    "Post without an index.",
    "No threads, please.",
    "Put the decisions in the main comment instead of threads.",
    "Format your report as a table.",
    "Keep the comment formatted as a checklist.",
]

# Briefs that talk about formats of things the agent builds, not of its own output. None is refused.
TASK_ONLY = [
    "Fix the date format on the gallery page.",
    "The upload form must accept PNG and JPEG formats.",
    "Format the price with two decimals in the cart.",
    "Add a comment to the code explaining the retry.",
    "The app shows one thread per conversation in the inbox.",
    "Index the sketches by date so the search is fast.",
    "Leave out the old CSS file from the bundle.",
    "Post the form data as JSON to /api/upload.",
    "The issue asks for a single page, no tabs.",
    "Follow agents/reviewer.md for how to post the review.",
    "Run prettier to format the code before you push.",
]

DEPARTED = [
    "I departed from agents/reviewer.md: the brief asked for one comment.",
    "Note: this review departed from the usual format.",
    "As you asked, instead of threads I posted one comment.",
    "As you asked, instead of a PR per card, I opened one PR.",
    "**Departed from** my file on one point: no index.",
    "I DEPARTED FROM the file because the brief said so.",
    "as you asked,\ninstead of the index I wrote a summary.",
]

NOT_DEPARTED = [
    "PR #57 is open. All tests pass.",
    "I did not depart from agents/reviewer.md.",
    "Nothing in this review departed from agents/reviewer.md.",
    "The review never departed from the file.",
    "I haven't departed from my instructions.",
    "As you asked, I opened the PR.",
    "Instead of a second round, the builder fixed it.",
    "The brief asked for one comment; I followed agents/reviewer.md and said so in the index.",
    "",
]


def project():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "docs", "MAP.md"), "w") as f:
        f.write("# map\n")
    return d


def run(args, data, project_dir=None, cwd=None):
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if project_dir is not None:
        env["CLAUDE_PROJECT_DIR"] = project_dir
    data = data if isinstance(data, bytes) else json.dumps(data).encode()
    return subprocess.run([sys.executable] + args, input=data, capture_output=True, env=env,
                          cwd=cwd or tempfile.gettempdir(), timeout=10)


def dispatch(prompt, agent="apb:reviewer", tool="Agent"):
    return {"session_id": "s1", "cwd": "/somewhere", "hook_event_name": "PreToolUse", "tool_name": tool,
            "tool_input": {"description": "Review PR", "prompt": prompt, "subagent_type": agent}}


def agent_stop(message, agent="apb:reviewer", agent_id="r1"):
    return {"session_id": "s1", "cwd": "/somewhere", "hook_event_name": "SubagentStop",
            "agent_id": agent_id, "agent_type": agent, "last_assistant_message": message}


def denied(r):
    if not r.stdout.strip():
        return None
    out = json.loads(r.stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    return out["permissionDecisionReason"] if out["permissionDecision"] == "deny" else None


def events(project_dir):
    path = os.path.join(project_dir, ".apb", "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f.read().splitlines()]


class FormatInstructions(unittest.TestCase):
    def test_task_only_briefs_pass(self):
        for text in [GOOD_BUILDER, GOOD_REVIEWER] + TASK_ONLY:
            with self.subTest(text=text):
                self.assertEqual(brief.format_instructions(text), [])

    def test_each_format_instruction_is_found(self):
        for text in FORMAT:
            with self.subTest(text=text):
                self.assertTrue(brief.format_instructions(text))

    def test_the_brief_that_started_it(self):
        found = brief.format_instructions(BAD_REVIEWER)
        self.assertEqual(len(found), 2, found)
        self.assertTrue(any("single comment" in f for f in found))
        self.assertTrue(any("leave out items" in f.lower() for f in found))

    def test_markup_case_and_line_breaks_do_not_hide_it(self):
        for text in ["Post the review **as a single comment**.", "POST AS ONE COMMENT", "Leave\nout items.",
                     "No `index`.", "Use _this format_ please."]:
            with self.subTest(text=text):
                self.assertTrue(brief.format_instructions(text))

    def test_each_finding_is_named_once(self):
        self.assertEqual(len(brief.format_instructions("No index. No index. No index.")), 1)


class Departure(unittest.TestCase):
    def test_reports_that_say_they_departed(self):
        for text in DEPARTED:
            with self.subTest(text=text):
                self.assertTrue(brief.departure(text))

    def test_reports_that_do_not(self):
        for text in NOT_DEPARTED + [None, 5]:
            with self.subTest(text=text):
                self.assertIsNone(brief.departure(text))

    def test_the_quote_is_the_sentence_and_is_short(self):
        quote = brief.departure("PR #57 is open. I departed from agents/reviewer.md: one comment. Tests pass.")
        self.assertIn("departed from agents/reviewer.md", quote)
        self.assertNotIn("Tests pass", quote)
        self.assertLessEqual(len(brief.departure("I departed from " + "x" * 1000)), brief.MAX)


def random_brief(rng):
    """A task-only brief, as lines, in one of the ways an orchestrator writes one."""
    lines = [rng.choice(["Build issue #%d on o/r." % rng.randint(1, 99), "Review PR #%d on o/r." % rng.randint(1, 99),
                         "Build card #%d: the upload page." % rng.randint(1, 99)])]
    lines += rng.sample(TASK_ONLY, rng.randint(0, 3))
    lines += rng.sample(["- Worktree: /w/issue-%d." % rng.randint(1, 99), "- Scratch folder: /tmp/s/.",
                         "- Never merge. Report back the PR link.", "Command rule: one plain command per call.",
                         "It holds code and docs.", "Follow your own agent file for how to work."],
                        rng.randint(1, 4))
    rng.shuffle(lines[1:])
    return lines


def dress(text, rng):
    """The same words with markup or case an orchestrator might add."""
    style = rng.randrange(4)
    if style == 1:
        return "**" + text + "**"
    if style == 2:
        return text.upper()
    if style == 3:
        return "- " + text
    return text


class Properties(unittest.TestCase):
    def test_random_briefs(self):
        rng = random.Random(54)
        for i in range(400):
            lines = random_brief(rng)
            clean = "\n".join(lines)
            with self.subTest(i=i, brief=clean):
                self.assertEqual(brief.format_instructions(clean), [])
            put = rng.sample(FORMAT, rng.randint(1, 3))
            dirty = list(lines)
            for text in put:
                dirty.insert(rng.randint(0, len(dirty)), dress(text, rng))
            dirty = "\n".join(dirty)
            with self.subTest(i=i, brief=dirty):
                self.assertTrue(brief.format_instructions(dirty))

    def test_random_reports(self):
        rng = random.Random(540)
        filler = ["PR #%d is open." % n for n in range(1, 9)] + ["All tests pass.", "Couldn't test Safari."]
        for i in range(400):
            parts = rng.sample(filler, rng.randint(1, 4)) + rng.sample(NOT_DEPARTED[:-1], rng.randint(0, 2))
            rng.shuffle(parts)
            with self.subTest(i=i, report=parts):
                self.assertIsNone(brief.departure(" ".join(parts)))
            said = dress(rng.choice(DEPARTED), rng)
            parts.insert(rng.randint(0, len(parts)), said)
            with self.subTest(i=i, report=parts):
                self.assertTrue(brief.departure(" ".join(parts)))


class DispatchHook(unittest.TestCase):
    """PreToolUse on the Agent tool, run the way Claude Code runs it."""

    def test_refuses_a_brief_with_format_instructions(self):
        r = run([SCRIPT], dispatch(BAD_REVIEWER))
        self.assertEqual(r.returncode, 0)
        reason = denied(r)
        self.assertTrue(reason)
        self.assertIn("Send the task only", reason)
        self.assertIn("agents/reviewer.md", reason)
        self.assertIn("as a single comment", reason)

    def test_lets_a_task_only_brief_through(self):
        for agent, prompt in [("apb:builder", GOOD_BUILDER), ("apb:reviewer", GOOD_REVIEWER)]:
            r = run([SCRIPT], dispatch(prompt, agent))
            self.assertEqual((r.returncode, r.stdout), (0, b""), agent)

    def test_checks_builders_and_reviewers_by_any_prefix(self):
        for agent in ["apb:builder", "builder", "apb:reviewer", "reviewer"]:
            reason = denied(run([SCRIPT], dispatch("Build #3. Format the PR description like this: x", agent)))
            self.assertIn("agents/%s.md" % agent.split(":")[-1], reason)

    def test_the_task_tool_name_too(self):
        self.assertTrue(denied(run([SCRIPT], dispatch(BAD_REVIEWER, tool="Task"))))

    def test_other_agents_are_not_checked(self):
        for agent in ["Explore", "general-purpose", "claude-code-guide", None]:
            r = run([SCRIPT], dispatch("Post the findings as a single comment.", agent))
            self.assertEqual(r.stdout, b"", agent)

    def test_bad_input_lets_the_call_through(self):
        for bad in [b"", b"not json", b"[1]", b"null", b"\xff",
                    b'{"hook_event_name": "PreToolUse", "tool_name": "Agent"}',
                    b'{"hook_event_name": "PreToolUse", "tool_name": "Agent", "tool_input": {"prompt": 5}}']:
            r = run([SCRIPT], bad)
            self.assertEqual((r.returncode, r.stdout), (0, b""), bad)


class ReportHook(unittest.TestCase):
    """SubagentStop: a report that says it departed from its file is written to the events file."""

    def setUp(self):
        self.project = project()

    def test_a_departure_is_recorded(self):
        r = run([SCRIPT], agent_stop(DEPARTED[0]), self.project)
        self.assertEqual((r.returncode, r.stdout), (0, b""))
        line = events(self.project)[-1]
        self.assertEqual((line["event"], line["agent_type"], line["agent_id"]),
                         ("Departure", "apb:reviewer", "r1"))
        self.assertIn("departed from agents/reviewer.md", line["quote"])

    def test_a_clean_report_writes_nothing(self):
        run([SCRIPT], agent_stop("PR #57 is open."), self.project)
        self.assertEqual(events(self.project), [])

    def test_only_in_an_apb_project(self):
        d = tempfile.mkdtemp()
        run([SCRIPT], agent_stop(DEPARTED[0]), d)
        self.assertEqual(os.listdir(d), [])

    def test_any_agent_type_counts(self):
        run([SCRIPT], agent_stop(DEPARTED[0], agent="general-purpose"), self.project)
        self.assertEqual(events(self.project)[-1]["event"], "Departure")


class Resolve(unittest.TestCase):
    def setUp(self):
        self.project = project()

    def test_resolve_writes_a_line(self):
        run([SCRIPT], agent_stop(DEPARTED[0]), self.project)
        self.assertEqual(len(stop.departures(events(self.project))), 1)
        r = run([SCRIPT, "resolve", "Reviewer rerun with the task only."], b"", cwd=self.project)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"1", r.stdout)
        line = events(self.project)[-1]
        self.assertEqual((line["event"], line["note"]), ("DepartureResolved", "Reviewer rerun with the task only."))
        self.assertEqual(stop.departures(events(self.project)), [])

    def test_resolve_needs_a_note(self):
        r = run([SCRIPT, "resolve"], b"", cwd=self.project)
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(events(self.project), [])

    def test_resolve_outside_an_apb_project_fails(self):
        r = run([SCRIPT, "resolve", "done"], b"", cwd=tempfile.mkdtemp())
        self.assertNotEqual(r.returncode, 0)

    def test_a_departure_after_the_resolve_is_pending_again(self):
        run([SCRIPT], agent_stop(DEPARTED[0]), self.project)
        run([SCRIPT, "resolve", "fixed"], b"", cwd=self.project)
        run([SCRIPT], agent_stop(DEPARTED[2], agent="apb:builder", agent_id="b2"), self.project)
        pending = stop.departures(events(self.project))
        self.assertEqual([d["agent_id"] for d in pending], ["b2"])


HAND_OFF = "1 thing for you to do right now: merge PR #57."
WAITING = "Nothing for you to do right now. Waiting on the reviewer, about 10 minutes."


def stop_payload(message, **extra):
    p = {"session_id": "s1", "transcript_path": "/none.jsonl", "cwd": "/somewhere", "hook_event_name": "Stop",
         "stop_hook_active": False, "last_assistant_message": message, "background_tasks": []}
    p.update(extra)
    return p


class StopHook(unittest.TestCase):
    """apb-stop.py: a hand-off to the human waits while a departure is not resolved."""

    def setUp(self):
        self.project = project()
        lines = [{"event": "UserPromptSubmit"}, {"event": "SubagentStart", "agent_id": "r1"},
                 {"event": "SubagentStop", "agent_id": "r1"},
                 {"event": "PostToolUse", "command": "gh pr view 57", "exit": 0}]
        os.makedirs(os.path.join(self.project, ".apb"))
        with open(os.path.join(self.project, ".apb", "events.jsonl"), "w") as f:
            for e in lines:
                e.update(session="s1", time=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + ".000Z")
                f.write(json.dumps(e) + "\n")

    def handle(self, message, **extra):
        return stop.handle(stop_payload(message, **extra), self.project, time.time())

    def test_a_hand_off_is_sent_back_while_a_departure_is_pending(self):
        run([SCRIPT], agent_stop(DEPARTED[0]), self.project)
        r = self.handle(HAND_OFF)
        self.assertTrue(r["send_back"])
        self.assertIn("departed from its file", r["note"])
        self.assertIn("departed from agents/reviewer.md", r["note"])
        self.assertIn("apb-brief.py resolve", r["note"])
        self.assertTrue(events(self.project)[-1].get("departure"))

    def test_it_holds_on_a_retry_too(self):
        run([SCRIPT], agent_stop(DEPARTED[0]), self.project)
        self.assertTrue(self.handle(HAND_OFF)["send_back"])
        self.assertTrue(self.handle(HAND_OFF, stop_hook_active=True)["send_back"])

    def test_it_gives_up_after_three_send_backs_in_a_turn(self):
        run([SCRIPT], agent_stop(DEPARTED[0]), self.project)
        got = [self.handle(HAND_OFF, stop_hook_active=i > 0)["send_back"] for i in range(4)]
        self.assertEqual(got, [True, True, True, False])

    def test_no_hand_off_no_send_back(self):
        run([SCRIPT], agent_stop(DEPARTED[0]), self.project)
        self.assertFalse(self.handle(WAITING)["send_back"])

    def test_resolved_lets_the_hand_off_through(self):
        run([SCRIPT], agent_stop(DEPARTED[0]), self.project)
        self.assertTrue(self.handle(HAND_OFF)["send_back"])
        run([SCRIPT, "resolve", "Reran the reviewer with the task only."], b"", cwd=self.project)
        self.assertFalse(self.handle(HAND_OFF, stop_hook_active=True)["send_back"])

    def test_no_departure_lets_the_hand_off_through(self):
        run([SCRIPT], agent_stop("PR #57 is open."), self.project)
        self.assertFalse(self.handle(HAND_OFF)["send_back"])


class HooksFile(unittest.TestCase):
    def setUp(self):
        with open(HOOKS_JSON) as f:
            self.hooks = json.load(f)["hooks"]

    def script(self, hook):
        path = hook["args"][0].replace("${CLAUDE_PLUGIN_ROOT}", os.path.dirname(HERE))
        return os.path.realpath(path)

    def test_dispatch_runs_the_brief_check_and_waits_for_it(self):
        groups = [g for g in self.hooks["PreToolUse"] if "Agent" in g.get("matcher", "")]
        self.assertEqual(len(groups), 1)
        self.assertIn("Task", groups[0]["matcher"])
        hook = groups[0]["hooks"][0]
        self.assertEqual(self.script(hook), os.path.realpath(SCRIPT))
        self.assertFalse(hook.get("async", False))
        self.assertLessEqual(hook["timeout"], 30)

    def test_agent_stop_runs_the_report_check_and_waits_for_it(self):
        hooks = [h for g in self.hooks["SubagentStop"] for h in g["hooks"]
                 if self.script(h) == os.path.realpath(SCRIPT)]
        self.assertEqual(len(hooks), 1)
        self.assertFalse(hooks[0].get("async", False))
        self.assertLessEqual(hooks[0]["timeout"], 30)


if __name__ == "__main__":
    unittest.main()
