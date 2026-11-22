"""Tests for the end-of-turn checks #121 added to apb-stop.py. Run from the repo root:
python3 -m unittest discover -s hooks -t hooks -p test_apb_stop_checks.py

Each test is built from the incident that added its case (#121's comments):
- one ask per turn: the ask line written twice in one reply (2026-11-21 ~7:15 PM and ~8:10 PM), three
  replies in a row each with the same ask (~8:14 PM, "apb orch 5"), and the same ask in two text blocks
  of one turn (2026-11-22 ~5:08 AM, "apb orch 11", after PR #155 merged);
- a "Nothing for you to do" line with nothing running (2026-11-21 ~4:58 PM, after the watcher handover);
- an ask line not in one of the forms, with a builder running: a bare "Nothing for you to do." (2026-11-22
  8:22 and 8:23 AM, "apb orch 13", and ~10:32 AM, "apb orch 14"; #159);
- the board and GitHub disagreeing (scripts/consistency-check.py; its own tests cover each case): the
  hook runs it when a turn hands something over or starts an agent, sends the turn back once with what
  to fix, and never blocks on a GitHub error.
Both directions each time: the incident is sent back, and the same turn done right goes through.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "apb-stop.py")
spec = importlib.util.spec_from_file_location("apb_stop_checks_under_test", SCRIPT)
stop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stop)

ccspec = importlib.util.spec_from_file_location("cc_for_hook", os.path.join(os.path.dirname(HERE), "scripts",
                                                                            "consistency-check.py"))
cc = importlib.util.module_from_spec(ccspec)
ccspec.loader.exec_module(cc)


def stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def apb_project(remote=None):
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "docs", "MAP.md"), "w") as f:
        f.write("# map\n")
    if remote:
        subprocess.run(["git", "init", "-q", d], check=True, timeout=30)
        subprocess.run(["git", "-C", d, "remote", "add", "origin", remote], check=True, timeout=30)
    return d


def write_events(project, events):
    os.makedirs(os.path.join(project, ".apb"), exist_ok=True)
    with open(os.path.join(project, ".apb", "events.jsonl"), "a", encoding="utf-8") as f:
        for e in events:
            e = dict(e)
            e.setdefault("time", stamp(time.time()))
            e.setdefault("session", "s1")
            f.write(json.dumps(e) + "\n")


def last_event(project):
    with open(os.path.join(project, ".apb", "events.jsonl"), encoding="utf-8") as f:
        return json.loads(f.read().splitlines()[-1])


def payload(message, **extra):
    p = {"session_id": "s1", "transcript_path": "/none.jsonl", "cwd": "/somewhere", "hook_event_name": "Stop",
         "stop_hook_active": False, "last_assistant_message": message, "background_tasks": []}
    p.update(extra)
    return p


def no_github(*args):
    raise subprocess.CalledProcessError(1, "gh")


def handle(message, project, **kw):
    extra = kw.pop("extra", {})
    return stop.handle(payload(message, **extra), project, time.time(), no_github, no_github, **kw)


# The human asked something; Claude looked it up and answered.
ANSWERED = [{"event": "UserPromptSubmit"}, {"event": "PostToolUse", "command": "gh pr view 44", "exit": 0}]
UPDATE = ("1 thing for you to do right now: update the plugin and open a new session, then tell it which card "
          "to build next.")


class OneAskPerTurn(unittest.TestCase):
    def test_the_same_ask_twice_in_one_reply_7_15_pm(self):
        # "APB orchestrator setup": the ask twice, then a summary after it.
        project = apb_project()
        write_events(project, ANSWERED)
        message = ("Listed the watchers.\n\n1 thing for you to do right now: open a new Code session on the apb repo.\n\n"
                   "1 thing for you to do right now: open a new Code session on the apb repo.\n\n"
                   "The old session keeps running until you close it.")
        r = handle(message, project)
        self.assertTrue(r["send_back"])
        self.assertIn("one ask", r["note"].lower())
        self.assertTrue(last_event(project).get("one-ask"))

    def test_two_wordings_of_the_ask_in_one_reply_8_10_pm(self):
        project = apb_project()
        write_events(project, ANSWERED)
        message = ("Updated.\n\n1 thing for you to do: open a new session on the apb repo.\n\n"
                   "1 thing for you to do right now: open a new session on the apb repo so plugin 0.3.60 takes effect.")
        self.assertTrue(handle(message, project)["send_back"])

    def test_the_ask_with_text_after_it_is_sent_back(self):
        project = apb_project()
        write_events(project, ANSWERED)
        r = handle("1 thing for you to do right now: merge PR #44.\n\nThe reviewer found nothing else.", project)
        self.assertTrue(r["send_back"])
        self.assertIn("last line", r["note"])

    def test_one_ask_at_the_end_goes_through(self):
        project = apb_project()
        write_events(project, ANSWERED)
        r = handle("The reviewer found nothing.\n\n1 thing for you to do right now: merge PR #44.", project)
        self.assertFalse(r["send_back"], r.get("note"))

    def test_a_quoted_ask_is_no_ask(self):
        project = apb_project()
        write_events(project, ANSWERED)
        r = handle("You wrote:\n> 1 thing for you to do right now: merge PR #44.\n\n"
                   "1 thing for you to do right now: merge PR #44.", project)
        self.assertFalse(r["send_back"], r.get("note"))

    def test_the_same_ask_in_two_text_blocks_of_one_turn_5_08_am(self):
        # "apb orch 11" after PR #155 merged: the ask, a "Showed the current time" step, the ask again.
        project = apb_project()
        write_events(project, ANSWERED)
        transcript = os.path.join(project, "t.jsonl")
        second = ("1 thing for you to do right now: update the plugin and open a new session. This session runs "
                  "apb plugin 0.3.66; main is at 0.3.68.")
        with open(transcript, "w") as f:
            for e in [{"type": "user", "message": {"content": "an earlier prompt"}},
                      {"type": "assistant", "message": {"content": [{"type": "text", "text": "Old turn.\n\n" + UPDATE}]}},
                      {"type": "user", "message": {"content": "PR #155 merged"}},
                      {"type": "assistant", "message": {"content": [{"type": "text", "text": "Merged.\n\n" + UPDATE}]}},
                      {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash"}]}},
                      {"type": "user", "message": {"content": [{"type": "tool_result", "content": "5:08"}]}},
                      {"type": "assistant", "message": {"content": [{"type": "text", "text": second}]}}]:
                f.write(json.dumps(e) + "\n")
        r = handle(second, project, extra={"transcript_path": transcript})
        self.assertTrue(r["send_back"])
        self.assertIn("one ask", r["note"].lower())
        # The earlier turn's ask doesn't count: one block of this turn alone goes through.
        with open(transcript, "w") as f:
            for e in [{"type": "assistant", "message": {"content": [{"type": "text", "text": UPDATE}]}},
                      {"type": "user", "message": {"content": [{"type": "text", "text": "PR #155 merged"}]}},
                      {"type": "assistant", "message": {"content": [{"type": "text", "text": "Merged."}]}},
                      {"type": "assistant", "message": {"content": [{"type": "text", "text": second}]}}]:
                f.write(json.dumps(e) + "\n")
        project2 = apb_project()
        write_events(project2, ANSWERED)
        self.assertFalse(handle(second, project2, extra={"transcript_path": transcript})["send_back"])

    def test_the_same_ask_on_three_wakes_8_14_pm(self):
        # "apb orch 5": after one git pull, three replies in a row, each with its own ask about #130.
        project = apb_project()
        write_events(project, ANSWERED + [
            {"event": "Stop", "holder": "human", "todo": "run git pull, then tell me to start #130"},
            {"event": "UserPromptSubmit", "automatic": True},
            {"event": "PostToolUse", "command": "git log -1", "exit": 0}])
        r = handle("Pulled.\n\n1 thing for you to do right now: say go and I'll start #130.", project)
        self.assertTrue(r["send_back"])
        self.assertIn("still open", r["note"])
        self.assertTrue(last_event(project).get("repeated-ask"))

    def test_after_the_human_writes_the_ask_may_be_said_again(self):
        project = apb_project()
        write_events(project, ANSWERED + [
            {"event": "Stop", "holder": "human", "todo": "say go and I'll start #130"},
            {"event": "UserPromptSubmit"}, {"event": "PostToolUse", "command": "git log -1", "exit": 0}])
        self.assertFalse(handle("1 thing for you to do right now: say go and I'll start #130.", project)["send_back"])

    def test_a_new_ask_on_a_wake_goes_through(self):
        project = apb_project()
        write_events(project, ANSWERED + [
            {"event": "Stop", "holder": "human", "todo": "say go and I'll start #130"},
            {"event": "UserPromptSubmit", "automatic": True},
            {"event": "PostToolUse", "command": "gh pr view 131", "exit": 0}])
        self.assertFalse(handle("1 thing for you to do right now: merge PR #131.", project)["send_back"])

    def test_a_retry_goes_through(self):
        project = apb_project()
        write_events(project, ANSWERED)
        r = handle(UPDATE + "\n\n" + UPDATE, project, extra={"stop_hook_active": True})
        self.assertFalse(r["send_back"])


class NothingWithNothingRunning(unittest.TestCase):
    """2026-11-21 ~4:58 PM: "Nothing for you to do right now. Waiting on a new comment on #25." with no
    build or agent running; the next step was the human choosing a card."""

    LINE = "Nothing for you to do right now. Waiting on a new comment on #25, ETA 5:30 PM."

    def events(self):
        return [{"event": "UserPromptSubmit"},
                {"event": "PostToolUse", "command": "python3 apb-watch.py --list", "exit": 0}]

    def test_the_4_58_pm_sequence_is_sent_back_once(self):
        project = apb_project()
        write_events(project, self.events())
        r = handle("Handed the watcher over.\n\n" + self.LINE, project)
        self.assertTrue(r["send_back"])
        self.assertIn("Nothing is running", r["note"])
        self.assertTrue(last_event(project).get("nothing-running"))
        retry = handle("Handed the watcher over.\n\n" + self.LINE, project, extra={"stop_hook_active": True})
        self.assertFalse(retry["send_back"])

    def test_with_something_running_it_goes_through(self):
        project = apb_project()
        write_events(project, self.events())
        shell = [{"id": "t1", "type": "shell", "status": "running", "command": "python3 apb-watch.py"}]
        r = handle(self.LINE, project, extra={"background_tasks": shell})
        self.assertFalse(r["send_back"], r.get("note"))
        project2 = apb_project()
        write_events(project2, self.events() + [{"event": "SubagentStart", "agent_id": "b1", "agent_type": "apb:builder"}])
        r = stop.handle({k: v for k, v in payload(self.LINE).items() if k != "background_tasks"}, project2,
                        time.time(), no_github, no_github)
        self.assertFalse(r["send_back"], r.get("note"))

    def test_waiting_on_something_outside_the_session_goes_through(self):
        for line in ["Nothing for you to do right now. Waiting on GitHub checks for PR #40, ETA 7:10 PM.",
                     "Nothing for you to do right now. Waiting on the build session for #142, ETA 7:10 PM.",
                     "Nothing for you to do right now. Waiting on CI, ETA 7:10 PM."]:
            project = apb_project()
            write_events(project, self.events())
            self.assertFalse(handle(line, project)["send_back"], line)


class MalformedAsk(unittest.TestCase):
    """#159: 2026-11-22 8:22 and 8:23 AM in "apb orch 13" (the #156 builder running) and ~10:32 AM in
    "apb orch 14" (the #136 builder running): turns ended with a bare "Nothing for you to do." and went
    through, because the strict ask (#153) was checked only when nothing was running."""

    BUILDER = [{"id": "a1", "type": "local_agent", "status": "running", "description": "Build issue #156"}]

    def events(self):
        return [{"event": "UserPromptSubmit"}, {"event": "PostToolUse", "command": "gh pr view 157", "exit": 0},
                {"event": "SubagentStart", "agent_id": "b1", "agent_type": "apb:builder"}]

    def stop_with(self, message, project=None, **extra):
        project = project or apb_project()
        if not os.path.exists(os.path.join(project, ".apb")):
            write_events(project, self.events())
        extra.setdefault("background_tasks", self.BUILDER)
        return handle(message, project, extra=extra), project

    def test_the_bare_line_with_a_builder_running_is_sent_back_once(self):
        r, project = self.stop_with("The #156 builder is writing its tests.\n\nNothing for you to do.")
        self.assertTrue(r["send_back"])
        self.assertIn('"Nothing for you to do."', r["note"])  # quotes the line it got
        # ... and the forms it should have used.
        self.assertIn("Nothing for you to do right now. Waiting on <what>, ETA <time>.", r["note"])
        self.assertIn("1 thing for you to do right now: <the thing>.", r["note"])
        self.assertIn("N things for you to do right now:", r["note"])
        self.assertIn("All clear: <boards> empty", r["note"])
        self.assertNotIn("Your work just ran out", r["note"])  # it didn't: the builder runs
        self.assertTrue(last_event(project).get("malformed-ask"))
        self.assertNotIn("holder", last_event(project))
        retry = handle("Nothing for you to do.", project, extra={"stop_hook_active": True,
                                                                  "background_tasks": self.BUILDER})
        self.assertFalse(retry["send_back"])

    def test_with_the_builder_known_only_from_the_events_file(self):
        project = apb_project()
        write_events(project, self.events())
        p = {k: v for k, v in payload("Nothing for you to do.").items() if k != "background_tasks"}
        r = stop.handle(p, project, time.time(), no_github, no_github)
        self.assertTrue(r["send_back"])
        self.assertTrue(last_event(project).get("malformed-ask"))

    def test_the_line_in_its_form_goes_through(self):
        for line in ["Nothing for you to do right now. Waiting on the #156 builder, ETA 8:40 AM.",
                     "1 thing for you to do right now: merge PR #44.",
                     "2 things for you to do right now:\n1. merge PR #44.\n2. answer decision 3 on PR #45: "
                     "https://github.com/hum/proj/pull/45#discussion_r3"]:
            r, _ = self.stop_with("Status.\n\n" + line)
            self.assertFalse(r["send_back"], (line, r.get("note")))

    def test_every_other_ask_shaped_line_is_sent_back(self):
        # Both directions: each of these is an ask line (ASK_LINE matches) that ask_block doesn't take.
        for line in ["Nothing for you to do right now.",
                     "Nothing for you to do right now. Waiting on the builder.",
                     "nothing for you to do; the builder is on it",
                     "**Nothing for you to do.**",
                     "1 thing for you to do: merge PR #44.",
                     "1 things for you to do right now: merge PR #44.",
                     "3 things for you to do right now:\n1. merge PR #44.\n2. merge PR #45.",
                     "All clear: nothing open."]:
            self.assertTrue(stop.ASK_LINE.match(stop.clean(line.splitlines()[0])), line)
            self.assertIsNone(stop.ask_block(line), line)
            r, project = self.stop_with("Status.\n\n" + line)
            self.assertTrue(r["send_back"], line)
            self.assertTrue(last_event(project).get("malformed-ask"), line)

    def test_no_ask_line_at_all_still_goes_through_while_work_runs(self):
        # A builder is running and the turn ends mid-work with no ask: the hook asks for one only when
        # the work has run out (REASON), as before.
        r, _ = self.stop_with("Started the builder; reading its first commits.")
        self.assertFalse(r["send_back"], r.get("note"))

    def test_a_quoted_bare_line_is_no_ask(self):
        r, _ = self.stop_with('The advisor wrote:\n> Nothing for you to do.\n\n'
                              'Nothing for you to do right now. Waiting on the #156 builder, ETA 8:40 AM.')
        self.assertFalse(r["send_back"], r.get("note"))

    def test_with_nothing_running_the_work_ran_out_note_says_it_once(self):
        project = apb_project()
        write_events(project, [{"event": "UserPromptSubmit", "automatic": True},
                               {"event": "PostToolUse", "command": "gh pr view 157", "exit": 0}])
        r = handle("Nothing for you to do.", project)
        self.assertTrue(r["send_back"])
        self.assertIn("Say who holds the ball", r["note"])
        self.assertEqual(r["note"].count("1 thing for you to do right now: <the thing>."), 1)

    def test_prose_after_it_is_the_one_ask_note_alone(self):
        r, project = self.stop_with("Nothing for you to do.\n\nThe builder is on step 3.")
        self.assertTrue(r["send_back"])
        self.assertIn("One ask per turn", r["note"])
        self.assertFalse(last_event(project).get("malformed-ask"))


# ---------------------------------------------------------------- the consistency check

REMOTE = "https://github.com/hum/proj.git"


def snapshot(board=(), prs=(), worktrees=()):
    return {"repo": "hum/proj", "human": "hum", "claude_login": "claude-bot", "board": list(board),
            "prs": list(prs), "worktrees": list(worktrees), "assigned": []}


def card(n, status, state="OPEN", assignees=()):
    return {"number": n, "status": status, "state": state, "assignees": list(assignees)}


class Recorder:
    """Stands in for consistency-check.py's fetch: records the call, returns a snapshot or fails."""

    def __init__(self, snap=None, error=None):
        self.snap, self.error, self.calls = snap, error, []

    def __call__(self, repo, project):
        self.calls.append((repo, project))
        if self.error:
            raise self.error
        return self.snap


STARTED = [{"event": "UserPromptSubmit"}, {"event": "PostToolUse", "command": "gh issue view 117", "exit": 0},
           {"event": "SubagentStart", "agent_id": "b1", "agent_type": "apb:builder"}]


class ConsistencyInTheHook(unittest.TestCase):
    def test_starting_a_build_with_its_card_in_todo_is_refused_and_names_the_card(self):
        project = apb_project(REMOTE)
        write_events(project, STARTED)
        lookup = Recorder(snapshot(board=[card(117, "todo")], worktrees=["issue-117-popups"]))
        r = handle("Started the builder on #117.", project, state_lookup=lookup)
        self.assertEqual(lookup.calls, [("hum/proj", project)])
        self.assertTrue(r["send_back"])
        self.assertIn("#117", r["note"])
        self.assertIn("start-build 117", r["note"])
        self.assertEqual(last_event(project)["consistency"], ["build-in-todo"])

    def test_moving_the_card_clears_it(self):
        project = apb_project(REMOTE)
        write_events(project, STARTED)
        lookup = Recorder(snapshot(board=[card(117, "in progress")], worktrees=["issue-117-popups"]))
        r = handle("Started the builder on #117.", project, state_lookup=lookup)
        self.assertFalse(r["send_back"], r.get("note"))
        self.assertNotIn("consistency", last_event(project))

    def test_a_hand_off_is_checked_with_what_it_hands_over(self):
        project = apb_project(REMOTE)
        write_events(project, [{"event": "UserPromptSubmit"}, {"event": "PostToolUse", "command": "gh pr view 134", "exit": 0}])
        pr = {"number": 134, "draft": False, "head": "issue-132-x", "assignees": [], "closes": [132],
              "threads": [], "comments": [], "reviews": []}
        lookup = Recorder(snapshot(board=[card(132, "in review")], prs=[pr]))
        r = handle("Reviewed.\n\n1 thing for you to do right now: merge PR #134.", project, state_lookup=lookup)
        self.assertTrue(r["send_back"])
        self.assertIn("--add-assignee hum", r["note"])
        self.assertEqual(last_event(project)["consistency"], ["hand-off-not-assigned"])
        # Assigned: through.
        project2 = apb_project(REMOTE)
        write_events(project2, [{"event": "UserPromptSubmit"}, {"event": "PostToolUse", "command": "gh pr view 134", "exit": 0}])
        pr["assignees"] = ["hum"]
        lookup = Recorder(snapshot(board=[card(132, "in review", assignees=["hum"])], prs=[pr]))
        self.assertFalse(handle("1 thing for you to do right now: merge PR #134.", project2, state_lookup=lookup)["send_back"])

    def test_a_github_error_lets_the_turn_end_and_says_so(self):
        project = apb_project(REMOTE)
        write_events(project, STARTED)
        r = handle("Started the builder on #117.", project, state_lookup=Recorder(error=subprocess.TimeoutExpired("gh", 12)))
        self.assertFalse(r["send_back"])
        self.assertIn("couldn't run", r["warning"])
        self.assertIn("consistency-error", last_event(project))

    def test_no_check_on_a_retry_or_a_plain_answer(self):
        project = apb_project(REMOTE)
        write_events(project, STARTED)
        lookup = Recorder(snapshot(board=[card(117, "todo")], worktrees=["issue-117-popups"]))
        handle("Started the builder on #117.", project, state_lookup=lookup, extra={"stop_hook_active": True})
        project2 = apb_project(REMOTE)
        write_events(project2, ANSWERED)
        handle("PR #44 has 3 files.", project2, state_lookup=lookup)
        self.assertEqual(lookup.calls, [])

    def test_no_repo_no_check(self):
        project = apb_project()
        write_events(project, STARTED)
        lookup = Recorder(snapshot())
        handle("Started the builder on #117.", project, state_lookup=lookup)
        self.assertEqual(lookup.calls, [])

    def test_hand_off_targets(self):
        message = ("1 thing for you to do right now: 2 decisions on PR #120: "
                   "https://github.com/hum/proj/pull/120#discussion_r7, https://github.com/hum/proj/pull/120#discussion_r8, "
                   "and answer the question on #41.")
        got = stop.hand_off_refs(message, "hum/proj")
        self.assertEqual(got["decisions"], [120])
        self.assertEqual(got["merge"], [])
        self.assertIn(120, got["prs"])
        self.assertIn(41, got["issues"])
        got = stop.hand_off_refs("1 thing for you to do right now: merge PR #134.", "hum/proj")
        self.assertEqual((got["merge"], got["prs"]), ([134], [134]))
        # A PR in another repo isn't this board's.
        got = stop.hand_off_refs("1 thing for you to do right now: merge https://github.com/other/x/pull/9.", "hum/proj")
        self.assertEqual((got["merge"], got["prs"]), ([], []))
        self.assertEqual(stop.hand_off_refs("Nothing for you to do right now. Waiting on CI, ETA 7:10 PM.", "hum/proj"),
                         {"prs": [], "merge": [], "issues": [], "decisions": []})


class HookRunForReal(unittest.TestCase):
    """The hook as Claude Code runs it: a GitHub error prints a notice for the human and sends nothing back."""

    def test_a_gh_failure_is_a_notice_not_a_send_back(self):
        project = apb_project(REMOTE)
        write_events(project, STARTED)
        bin_ = tempfile.mkdtemp()
        for tool in ("gh", "osascript"):
            with open(os.path.join(bin_, tool), "w") as f:
                f.write("#!/bin/sh\nexit 1\n")
            os.chmod(os.path.join(bin_, tool), 0o755)
        env = dict(os.environ, CLAUDE_PROJECT_DIR=project, PATH=bin_ + os.pathsep + os.environ["PATH"])
        r = subprocess.run([sys.executable, SCRIPT], input=json.dumps(payload("Started the builder on #117.")).encode(),
                           capture_output=True, env=env, timeout=60)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertNotIn("hookSpecificOutput", out)
        self.assertIn("couldn't run", out["systemMessage"])


class TimeBudget(unittest.TestCase):
    """Review fix 2 on #157: if Claude Code kills the hook at its timeout, there is no events line and no
    "couldn't run" message, so the worst case must fit inside hooks.json's timeout."""

    def test_the_worst_case_fits_the_stop_hooks_timeout(self):
        with open(os.path.join(HERE, "hooks.json")) as f:
            groups = json.load(f)["hooks"]["Stop"]
        timeout = next(h["timeout"] for g in groups for h in g["hooks"] if h["args"][0].endswith("/apb-stop.py"))
        worst = stop.PR_BUDGET + stop.CHECK_BUDGET + 2 * stop.GIT_LIMIT + stop.STARTUP
        self.assertLessEqual(worst, timeout)
        self.assertGreaterEqual(stop.STARTUP, 2)

    def test_the_check_passes_its_budget_on(self):
        # The hook gives the check CHECK_BUDGET for its gh calls and for each one, nothing more.
        project = apb_project(REMOTE)
        write_events(project, STARTED)
        seen = {}
        real = stop.consistency

        def fake_module():
            module = real()

            def fetch(repo, project, login, budget, time_limit):
                seen.update(budget=budget, time_limit=time_limit)
                return snapshot()
            module.fetch = fetch
            return module
        stop.consistency = fake_module
        try:
            handle("Started the builder on #117.", project)
        finally:
            stop.consistency = real
        self.assertEqual(seen, {"budget": stop.CHECK_BUDGET, "time_limit": stop.CHECK_BUDGET})


class Cases(unittest.TestCase):
    def test_the_hooks_own_cases_name_their_incidents(self):
        ids = [c["id"] for c in stop.CASES]
        self.assertEqual(sorted(ids), ["malformed-ask", "nothing-running", "one-ask", "repeated-ask"])
        for c in stop.CASES:
            self.assertRegex(c["incident"], r"20\d\d-\d\d-\d\d")


if __name__ == "__main__":
    unittest.main()
