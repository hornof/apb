"""Tests for apb-stop.py, the Stop hook. Run from the repo root: python3 -m unittest discover hooks

The hook decides, when the orchestrator's turn ends, whether its work just ran out and, if so,
whether the message ends with an ask block: one of the playbook's four lines (#153). Example tests check each
rule; property tests build random session histories and compare the hook's decision with a separate
simulation that tracks the same state while it writes the events.
"""
import importlib.util
import json
import os
import random
import stat
import subprocess
import sys
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "apb-stop.py")
HOOKS_JSON = os.path.join(HERE, "hooks.json")

spec = importlib.util.spec_from_file_location("apb_stop", SCRIPT)
stop = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stop)

NOTHING = "Nothing for you to do right now. Waiting on GitHub checks for PR #40, ETA 7:10 PM."
ONE = "1 thing for you to do right now: merge PR #44."
MANY = "2 things for you to do right now:\n1. Merge PR #44.\n2. Answer the question on #41."
ALL_CLEAR = "All clear: both boards empty, no open PRs, as of 11:58 PM."


def apb_project():
    d = tempfile.mkdtemp()
    os.makedirs(os.path.join(d, "docs"))
    with open(os.path.join(d, "docs", "MAP.md"), "w") as f:
        f.write("# map\n")
    return d


def stamp(t):
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".000Z"


def write_events(project, events):
    os.makedirs(os.path.join(project, ".apb"), exist_ok=True)
    with open(os.path.join(project, ".apb", "events.jsonl"), "a", encoding="utf-8") as f:
        for e in events:
            e = dict(e)
            e.setdefault("time", stamp(time.time()))
            e.setdefault("session", "s1")
            f.write(json.dumps(e) + "\n")


def read_events(project):
    path = os.path.join(project, ".apb", "events.jsonl")
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f.read().splitlines()]


def payload(message, **extra):
    p = {"session_id": "s1", "transcript_path": "/none.jsonl", "cwd": "/somewhere",
         "hook_event_name": "Stop", "stop_hook_active": False, "last_assistant_message": message}
    p.update(extra)
    return p


def fake_osascript():
    """A folder holding an osascript that records its arguments, so tests never alert the Mac.
    Returns (folder, the file it records to)."""
    folder = tempfile.mkdtemp()
    calls = os.path.join(folder, "calls.txt")
    fake = os.path.join(folder, "osascript")
    with open(fake, "w") as f:
        f.write("#!/bin/sh\nfor a in \"$@\"; do printf '%%s\\n' \"$a\" >> '%s'; done\n"
                "echo --- >> '%s'\n" % (calls, calls))
    os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
    return folder, calls


def graphql_threads(threads):
    """The GraphQL answer for a PR's review threads. threads: {first comment's id: resolved}."""
    return {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
        {"isResolved": resolved, "comments": {"nodes": [{"databaseId": int(cid)}, {"databaseId": int(cid) + 1}]}}
        for cid, resolved in threads.items()]}}}}}


def fake_gh(folder, prs=None, threads=None):
    """Put in folder a gh that records its arguments and answers `gh pr view N ...` with prs[N] as
    JSON, or fails when prs has no N; and `gh api graphql -F number=N ...` with the review threads
    threads[N] ({first comment's id: resolved}), or fails when threads has no N. Returns the file it
    records to."""
    calls = os.path.join(folder, "gh-calls.txt")
    for number, pr in (prs or {}).items():
        with open(os.path.join(folder, "pr-%d.json" % number), "w") as f:
            json.dump(pr, f)
    for number, found in (threads or {}).items():
        with open(os.path.join(folder, "threads-%d.json" % number), "w") as f:
            json.dump(graphql_threads(found), f)
    fake = os.path.join(folder, "gh")
    with open(fake, "w") as f:
        f.write("#!/bin/sh\nprintf '%%s\\n' \"$*\" >> '%s'\n"
                "if [ \"$1\" = api ]; then f='%s'/threads-\"${4#number=}\".json; else f='%s'/pr-\"$3\".json; fi\n"
                "[ -f \"$f\" ] || exit 1\ncat \"$f\"\n" % (calls, folder, folder))
    os.chmod(fake, os.stat(fake).st_mode | stat.S_IEXEC)
    return calls


DEFAULT_BIN, _ = fake_osascript()
fake_gh(DEFAULT_BIN)  # no test reaches GitHub: by default, every gh call fails


def no_github(repo, number):
    """A lookup that fails, as gh does with no network: tests that call the hook in-process never reach GitHub."""
    raise subprocess.CalledProcessError(1, "gh")


def run(p, project, env_extra=None):
    data = p if isinstance(p, bytes) else json.dumps(p).encode()
    env = dict(os.environ)
    env.pop("CLAUDE_PROJECT_DIR", None)
    if project is not None:
        env["CLAUDE_PROJECT_DIR"] = project
    env["PATH"] = DEFAULT_BIN + os.pathsep + env["PATH"]
    env["APB_REMIND_AFTER_SECONDS"] = "1"
    env.update(env_extra or {})
    return subprocess.run([sys.executable, SCRIPT], input=data, capture_output=True, env=env,
                          cwd=tempfile.gettempdir(), timeout=10)


def sent_back(r):
    if not r.stdout.strip():
        return False
    out = json.loads(r.stdout)
    note = out.get("hookSpecificOutput", {})
    return note.get("hookEventName") == "Stop" and bool(note.get("additionalContext"))


# The events of one finished builder: the orchestrator started it, it ran a command, it stopped,
# and the orchestrator looked up its PR.
FINISHED_AGENT = [{"event": "UserPromptSubmit"},
                  {"event": "SubagentStart", "agent_id": "b1", "agent_type": "apb:builder"},
                  {"event": "PostToolUse", "agent_id": "b1", "command": "npm test", "exit": 0},
                  {"event": "SubagentStop", "agent_id": "b1", "agent_type": "apb:builder"},
                  {"event": "PostToolUse", "command": "gh pr view 44", "exit": 0}]


class FinalAsk(unittest.TestCase):
    """A message that hands something to the human ends with its ask block (#153): one of the four
    lines, the numbered list of "N things", and only the link lines or fenced blocks that belong to it."""

    def test_the_three_forms(self):
        self.assertEqual(stop.final_ask(NOTHING), ("claude", None))
        self.assertEqual(stop.final_ask(ONE), ("human", "merge PR #44"))
        self.assertEqual(stop.final_ask(MANY), ("human", "2 things"))

    def test_all_clear(self):
        self.assertEqual(stop.final_ask(ALL_CLEAR), ("human", "all clear"))
        self.assertEqual(stop.final_ask("**All clear: the apb and project boards empty, no open PRs, as of "
                                        "9:05 AM**"), ("human", "all clear"))
        self.assertEqual(stop.final_ask("Done.\n" + ALL_CLEAR), ("human", "all clear"))  # the last line
        for message in ["All clear.", "All clear: nothing open.",
                        "All clear: both boards empty, no open PRs.",  # no time
                        "All clear: both boards empty, as of 9:05 AM.",  # says nothing about PRs
                        "all clear: both boards empty, no open PRs, as of 9:05 AM.",
                        "ALL CLEAR: both boards empty, no open PRs, as of 9:05 AM.",
                        ALL_CLEAR + "\nDone.",  # not the last line
                        ALL_CLEAR + "\nhttps://github.com/hornof/apb/pulls",  # nothing under All clear
                        ALL_CLEAR + "\n```\nx\n```"]:
            self.assertIsNone(stop.final_ask(message), message)

    def test_nothing_takes_an_eta_as_a_clock_time(self):
        for eta in ["7:10 PM", "7:10 PM.", "7:10pm", "7:10 p.m.", "19:10", "9:05 AM"]:
            message = "Nothing for you to do right now. Waiting on the builder, ETA %s" % eta
            self.assertEqual(stop.final_ask(message), ("claude", None), message)

    def test_what_happened_and_the_status_lines_go_above(self):
        self.assertEqual(stop.final_ask("PR #44 (Card #41): in review.\n\n" + ONE), ("human", "merge PR #44"))
        self.assertEqual(stop.final_ask("PR #40: checks running.\n" + NOTHING)[0], "claude")
        self.assertEqual(stop.final_ask("Here is the summary.\n" + ONE), ("human", "merge PR #44"))
        self.assertEqual(stop.final_ask("The builder finished.\n1. A.\n2. B.\n\n" + MANY), ("human", "2 things"))

    def test_nothing_may_follow_the_ask(self):
        for message in [ONE + "\n\nPR #44 (Card #41): in review.", NOTHING + "\nPR #40: checks running.",
                        MANY + "\n\nThe builder finished.", MANY + "\n3. C.", ONE + "\n> quoted",
                        ONE + "\nSee https://github.com/hornof/apb/pull/44.",  # words around the link
                        ONE + "\nPreview: https://x.example/ is up.",  # words after the labelled link
                        ONE + "\nThe preview for the new page: https://x.example/",  # a label of more than four words
                        ONE + "\n```\nnever closed", ONE + "\n" + ONE + "\nDone."]:
            self.assertIsNone(stop.final_ask(message), message)

    def test_links_and_fenced_blocks_belong_to_the_ask(self):
        link = "https://github.com/hornof/apb/pull/44"
        for tail in [link, "<%s>" % link, "[PR #44](%s)" % link, "- [PR #44](%s), %s." % (link, link),
                     "```\ngh pr merge 44\n```", "%s\n\n```sh\nls\n```\n%s" % (link, link), "~~~\nx\n~~~",
                     "Preview: https://sketch.example.pages.dev/", "PR: [PR #44](%s)" % link,
                     "- Preview: <https://sketch.example.pages.dev/>"]:
            self.assertEqual(stop.final_ask(ONE + "\n\n" + tail), ("human", "merge PR #44"), tail)
            self.assertEqual(stop.final_ask(MANY + "\n" + tail), ("human", "2 things"), tail)
            self.assertEqual(stop.final_ask(NOTHING + "\n" + tail), ("claude", None), tail)

    def test_bold_and_blank_lines_are_ignored(self):
        self.assertEqual(stop.final_ask("\n\n**" + ONE + "**\n\n")[0], "human")

    def test_any_count_with_a_matching_list(self):
        for n in [2, 3, 9, 10, 12]:
            message = "Done.\n%d things for you to do right now:\n" % n + "\n".join(
                "%d. Thing %d." % (i, i) for i in range(1, n + 1))
            self.assertEqual(stop.final_ask(message), ("human", "%d things" % n))

    def test_the_count_must_match_the_list(self):
        self.assertIsNone(stop.final_ask("3 things for you to do right now:\n1. A.\n2. B."))
        self.assertIsNone(stop.final_ask("2 things for you to do right now:\n1. A.\n2. B.\n3. C."))
        self.assertIsNone(stop.final_ask("2 things for you to do right now:"))
        self.assertIsNone(stop.final_ask("2 things for you to do right now:\n1. A.\n3. B."))
        self.assertIsNone(stop.final_ask("1. A.\n2. B."))

    def test_wrong_forms(self):
        for message in [None, "", "PR #37: nothing for you to decide, merge-ready.",
                        "Done. The builder finished.",
                        "Nothing for you to do right now.",  # no "Waiting on"
                        "Nothing for you to do right now. Waiting on CI.",  # no time
                        "1 things for you to do right now: merge PR #44.",
                        "1 things for you to do right now:\n1. Merge PR #44.",
                        "Nothing for you to do right now. Waiting on CI, ETA soon.",
                        "Nothing for you to do right now. Waiting on CI, about 5 minutes.",  # the old form
                        "Nothing for you to do right now. Waiting on CI, ETA 5 minutes.",  # not a clock time
                        "Nothing for you to do right now. Waiting on CI, ETA 7:10 PM. Then I start #5.",
                        "0 things for you to do right now:",
                        "1 thing for you to do right now:",
                        "NOTHING FOR YOU TO DO RIGHT NOW. Waiting on CI, ETA 7:10 PM.",
                        ONE + "\nHere is the summary.",  # the ask first, prose after: not the last block
                        "2 things for you to do right now: merge and answer."]:
            self.assertIsNone(stop.final_ask(message), message)


class FinalAskProperties(unittest.TestCase):
    """Random messages, built from parts whose answer is known by construction (the oracle): any prose
    above an ask block leaves the answer as the block alone gives it, and any prose after it, or prose
    alone, is no hand-off."""

    WORDS = ["the", "builder", "PR", "#44", "merged", "1.", "2)", "ETA", "7:10", "PM", "thing", "decide",
             "things", "for", "you", "to", "do", "right", "now", "Nothing", "All", "clear:", "**", ">", "-",
             "see", "https://github.com/o/r/pull/4", "`x`", "(a)", "waiting", "Card", "In", "review"]

    def prose_line(self, rng):
        """A line that is no ask line, no numbered item, no link line and no fence: it starts with a word
        no ask line starts with."""
        return rng.choice(["Done:", "PR #44:", "Status", "The", "Card #41:", "Then"]) + " " + " ".join(
            rng.choice(self.WORDS) for _ in range(rng.randint(0, 8)))

    def prose(self, rng):
        """Some lines of prose, or a numbered list with prose, or a fenced block in prose: never a block."""
        lines = [self.prose_line(rng) for _ in range(rng.randint(1, 4))]
        if rng.random() < 0.3:
            lines.insert(rng.randrange(len(lines) + 1), "\n".join("%d. %s" % (i, self.prose_line(rng))
                                                                   for i in range(1, rng.randint(2, 4))))
        if rng.random() < 0.2:
            lines.insert(0, "```\n%s\n```" % self.prose_line(rng))
        return "\n".join(lines)

    def block(self, rng):
        """(an ask block, the (holder, todo) it says), built so the answer is known."""
        kind = rng.randrange(4)
        bold = rng.random() < 0.2
        wrap = (lambda s: "**%s**" % s) if bold else (lambda s: s)
        tail = rng.choice(["", "", "\nhttps://github.com/o/r/pull/7", "\n\n[PR #7](https://github.com/o/r/pull/7)",
                           "\n```\ngh pr merge 7\n```"])
        if kind == 0:
            return wrap("Nothing for you to do right now. Waiting on CI, ETA 7:%02d PM." % rng.randrange(60)) \
                + tail, ("claude", None)
        if kind == 1:
            thing = "merge PR #%d" % rng.randrange(1, 999)
            return wrap("1 thing for you to do right now: %s." % thing) + tail, ("human", thing)
        if kind == 2:
            return wrap(ALL_CLEAR), ("human", "all clear")
        n = rng.randint(2, 6)
        return (wrap("%d things for you to do right now:" % n) + rng.choice(["\n", "\n\n"])
                + "\n".join("%d. merge PR #%d." % (i, i) for i in range(1, n + 1)) + tail), ("human", "%d things" % n)

    def test_prose_above_the_block_changes_nothing(self):
        rng = random.Random(153)
        for _ in range(400):
            block, want = self.block(rng)
            above = self.prose(rng)
            self.assertEqual(stop.final_ask(block), want, block)
            self.assertEqual(stop.final_ask(above + rng.choice(["\n", "\n\n"]) + block), want, (above, block))

    def test_prose_after_the_block_is_no_hand_off(self):
        rng = random.Random(1530)
        for _ in range(400):
            block, _want = self.block(rng)
            after = self.prose(rng)
            message = block + rng.choice(["\n", "\n\n"]) + after
            self.assertIsNone(stop.final_ask(message), message)
            self.assertIsNone(stop.final_ask(after), after)

    def test_the_hook_logs_the_same_holder_and_todo_with_prose_above(self):
        rng = random.Random(15300)
        for _ in range(30):
            block, want = self.block(rng)
            project = apb_project()
            write_events(project, FINISHED_AGENT)
            r = stop.handle(payload(self.prose(rng) + "\n\n" + block), project, time.time(), no_github, no_github)
            self.assertFalse(r["send_back"], block)
            last = read_events(project)[-1]
            self.assertEqual((last.get("holder"), last.get("todo")), want, block)

    def test_the_hook_sends_back_prose_after_the_block(self):
        rng = random.Random(153000)
        for _ in range(30):
            block, _want = self.block(rng)
            project = apb_project()
            write_events(project, FINISHED_AGENT)
            r = stop.handle(payload(block + "\n\n" + self.prose(rng)), project, time.time(), no_github, no_github)
            self.assertTrue(r["send_back"], block)
            self.assertTrue(r["note"].startswith("Say who holds the ball"), r["note"])
            self.assertNotIn("holder", read_events(project)[-1])


class HandOffLog(unittest.TestCase):
    """What the Stop line logs from the ask block, for scripts/card-times.py (#153)."""

    def setUp(self):
        self.project = apb_project()
        write_events(self.project, FINISHED_AGENT)

    def stop_line(self, message):
        r = stop.handle(payload(message), self.project, time.time(), no_github, no_github)
        return r, read_events(self.project)[-1]

    def test_a_one_pr_hand_off_at_the_end(self):
        r, last = self.stop_line("The reviewer found nothing.\nPR #44 (Card #41): in review.\n\n" + ONE)
        self.assertFalse(r["send_back"])
        self.assertEqual((last["holder"], last["todo"]), ("human", "merge PR #44"))
        self.assertNotIn("items", last)

    def test_an_n_things_hand_off_logs_each_item(self):
        message = ("Both reviews are done.\n\n2 things for you to do right now:\n"
                   "1. merge [PR #58 (Card #54)](https://github.com/hornof/apb/pull/58)\n"
                   "2. merge [PR #61 (Card #60)](https://github.com/hornof/apb/pull/61)")
        r, last = self.stop_line(message)
        self.assertFalse(r["send_back"])
        self.assertEqual((last["holder"], last["todo"]), ("human", "2 things"))
        self.assertEqual(last["items"], ["1. merge [PR #58 (Card #54)](https://github.com/hornof/apb/pull/58)",
                                         "2. merge [PR #61 (Card #60)](https://github.com/hornof/apb/pull/61)"])

    def test_link_lines_under_the_ask_are_logged(self):
        r, last = self.stop_line("1 thing for you to do right now: merge it.\nhttps://github.com/hornof/apb/pull/58")
        self.assertFalse(r["send_back"])
        self.assertEqual(last["links"], ["https://github.com/hornof/apb/pull/58"])

    def test_an_answer_with_no_hand_off_logs_no_holder(self):
        project = apb_project()
        write_events(project, [{"event": "UserPromptSubmit"}])  # the human asked; the work didn't run out
        r = stop.handle(payload("The PR changes two files."), project, time.time(), no_github, no_github)
        self.assertFalse(r["send_back"])
        last = read_events(project)[-1]
        self.assertEqual(last["event"], "Stop")
        for key in ("holder", "todo", "items", "links"):
            self.assertNotIn(key, last)

    def test_the_ask_first_with_prose_after_is_sent_back(self):
        r, last = self.stop_line(ONE + "\n\nPR #44 (Card #41): in review. The reviewer found nothing.")
        self.assertTrue(r["send_back"])
        self.assertTrue(r["note"].startswith("Say who holds the ball. Your work just ran out, so end the message"))
        self.assertTrue(last["sent_back"])
        self.assertNotIn("holder", last)


class StatedFacts(unittest.TestCase):
    """What counts as a GitHub fact in a message: a sentence that gives a PR, issue or card #N (or a
    link to one, or a count of them) a state: open, merged, closed, done, in review, on the board or in
    a column. Not in quotes, and not in a plan ("once PR #80 is merged")."""

    def test_facts(self):
        for message in ["PR #44 is merged.", "PRs #44 and #45 merged.", "pull request 12 is open.",
                        "https://github.com/hornof/apb/pull/52 is merged.", "Issue #70 is closed.",
                        "#70 is closed.", "apb#75 is open.", "Card #41 is in the In review column.",
                        "Card #9 is on the board.", "Card #3 is now in In review, and the reviewer is on it.",
                        "[PR #66](https://github.com/hornof/apb/pull/66) is merged.", "You merged PR #66.",
                        "PR #78 is still open.", "The open PR #44 waits for review.",
                        "Done. PR #44 merged at 7:05 PM.", "**PR #44:** merged.",
                        # A state in bold or italics.
                        "PR #44 is **open**.", "Card #3 is in **In review**.", "PR #44 is _merged_.",
                        # A count of PRs, cards or issues with a state.
                        "13 PRs merged Monday.", "5 cards are done.", "3 of 5 cards are done.",
                        "4 open issues left.", "2 issues are closed.",
                        # A plan clause doesn't hide a fact outside it.
                        "When I checked, PR #44 was merged.", "PR #44 merged before the checks finished."]:
            self.assertTrue(stop.stated_facts(message), message)

    def test_not_facts(self):
        for message in [None, "", "Done. The builder finished.", "Answer the question on #41.",
                        "Waiting on the builder, ETA 7:10 PM.", "2 things for you to do right now:",
                        "It took 1.5 seconds.", "The address is 10.0.0.12.", "On 2026-11-18 at 10:30.",
                        "The keyboard and dashboard work.", "We need one approval.",
                        "45 tests pass.", "Onboarding is next.",
                        # No #N, or no state: the word "board" or "column" alone, a version, a count.
                        "Moved it on the board.", "Updating a status, label or board column: a change.",
                        "The plugin is at 0.3.18.", "3 of 5 checks passed.", "13 PRs today.",
                        "The builder opened 2 PRs.",
                        # A state after "after", "once", "when", "if", "until" or "before" is a plan.
                        "I'll check the live site after PR #44 is merged.", "Once PR #80 is merged, I'll rebase.",
                        "If PR #81 is closed, I'll reopen it.", "Wait until PR #44 is merged.",
                        "Nothing for you to do right now. I'll say so when PR #45's review is done.",
                        "Waiting on GitHub checks for PR #40, ETA 7:10 PM.", "The builder opened PR #57.",
                        "Merge PR #44.", "After you merge PR #42, I'll start.", "Closes #79.",
                        "The builder is still on card #64.",
                        "Say \"go\" if you want apb#75 and apb#70 on the board.",
                        # A quoted fact is not stated: in quotation marks, backticks or a quoted line.
                        "The extra messages, like \"I checked GitHub: PR #78 is still open\", come from the check.",
                        "It printed `PR #78 is merged` twice.", "> PR #44 is merged."]:
            self.assertEqual(stop.stated_facts(message), [], message)

    def test_names_the_numbers_in_a_fact(self):
        self.assertEqual(stop.stated_facts("PRs #44 and #45 merged."), ["PRs #44", "#45"])
        self.assertEqual(stop.stated_facts("[PR #66](https://github.com/hornof/apb/pull/66) is merged."),
                         ["PR #66", "/pull/66"])
        self.assertEqual(stop.stated_facts("13 PRs merged Monday."), ["13 PRs"])
        self.assertEqual(stop.stated_facts("When I checked, PR #44 was merged."), ["PR #44"])

    def test_each_fact_is_named_once(self):
        facts = stop.stated_facts("PR #44, PR #44 and PR #44 are merged.")
        self.assertEqual(facts, ["PR #44"])


class CheckedThisTurn(unittest.TestCase):
    """Whether the orchestrator ran gh or git in this turn, from the events file."""
    now = time.time()

    def events(self, *groups):
        return [dict(e, session=e.get("session", "s1"), time=stamp(self.now)) for g in groups for e in g]

    def checked(self, *groups):
        return stop.checked_this_turn(self.events(*groups), "s1")

    def test_gh_or_git_in_this_turn(self):
        for command in ["gh pr view 44", "git -C /x log -1", "timeout 60 gh pr list", "  git status",
                        "gh api repos/o/r/pulls"]:
            self.assertTrue(self.checked([{"event": "UserPromptSubmit"},
                                          {"event": "PostToolUse", "command": command, "exit": 0}]), command)

    def test_the_map_and_progress_page_scripts_count(self):
        for command in ["python3 scripts/map-artifact.py /tmp/m.html https://claude.ai/artifact/x",
                        "python3 /Users/x/apb/scripts/progress-page.py /tmp/p.html",
                        "timeout 120 python3 scripts/progress-page.py out.html",
                        "scripts/map-artifact.py out.html"]:
            self.assertTrue(self.checked([{"event": "UserPromptSubmit"},
                                          {"event": "PostToolUse", "command": command, "exit": 0}]), command)

    def test_a_failed_script_run_does_not_count(self):
        for command in ["python3 scripts/map-artifact.py out.html", "python3 scripts/progress-page.py out.html"]:
            self.assertFalse(self.checked([{"event": "UserPromptSubmit"},
                                           {"event": "PostToolUseFailure", "command": command, "exit": 1}]),
                             command)

    def test_other_commands_do_not_count(self):
        for command in ["ls", "python3 hooks/github.py", "echo gh", "cat .gitignore", "legit x", None,
                        "cat scripts/map-artifact.py", "python3 scripts/test_progress_page.py",
                        "python3 scripts/apb-watch.py"]:
            self.assertFalse(self.checked([{"event": "UserPromptSubmit"},
                                           {"event": "PostToolUse", "command": command, "exit": 0}]), command)

    def test_a_lookup_in_an_earlier_turn_does_not_count(self):
        self.assertFalse(self.checked([{"event": "UserPromptSubmit"},
                                       {"event": "PostToolUse", "command": "gh pr view 44", "exit": 0},
                                       {"event": "Stop"}, {"event": "UserPromptSubmit"}]))

    def test_a_lookup_before_a_send_back_still_counts(self):
        self.assertTrue(self.checked([{"event": "UserPromptSubmit"},
                                      {"event": "PostToolUse", "command": "gh pr view 44", "exit": 0},
                                      {"event": "Stop", "sent_back": True}]))

    def test_an_agents_lookup_does_not_count(self):
        self.assertFalse(self.checked([{"event": "UserPromptSubmit"},
                                       {"event": "PostToolUse", "agent_id": "b1", "command": "gh pr view 44",
                                        "exit": 0}]))

    def test_another_sessions_lookup_does_not_count(self):
        self.assertFalse(self.checked([{"event": "UserPromptSubmit"},
                                       {"event": "PostToolUse", "session": "s2", "command": "gh pr view 44",
                                        "exit": 0}]))

    def test_a_failed_lookup_does_not_count(self):
        self.assertFalse(self.checked([{"event": "UserPromptSubmit"},
                                       {"event": "PostToolUseFailure", "command": "gh pr view 44", "exit": 1}]))


class CheckFirst(unittest.TestCase):
    """The hook run the way Claude Code runs it: a GitHub fact needs a gh or git call in the same turn."""

    def setUp(self):
        self.project = apb_project()

    def test_sends_back_an_unchecked_fact(self):
        write_events(self.project, [{"event": "UserPromptSubmit"}])
        r = run(payload("PR #44 is merged and card #43 is closed."), self.project)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertNotIn("decision", out)  # a quiet note, not a block
        note = out["hookSpecificOutput"]
        self.assertEqual(note["hookEventName"], "Stop")
        self.assertIn("You stated a GitHub fact without checking it this turn.", note["additionalContext"])
        self.assertIn("PR #44", note["additionalContext"])
        self.assertIn("card #43", note["additionalContext"])
        last = read_events(self.project)[-1]
        self.assertEqual((last["event"], last["sent_back"]), ("Stop", True))
        self.assertEqual(last["unchecked"], ["PR #44", "card #43"])

    def test_names_at_most_five_facts(self):
        write_events(self.project, [{"event": "UserPromptSubmit"}])
        facts = ["PR #1", "PR #2", "PR #3", "PR #4", "PR #5", "PR #6"]
        r = run(payload(", ".join(facts) + " are open."), self.project)
        context = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.endswith(": " + ", ".join(facts[:5]) + "."), context)
        self.assertNotIn("PR #6", context)
        self.assertEqual(read_events(self.project)[-1]["unchecked"], facts[:5])

    def test_lets_a_checked_fact_through(self):
        write_events(self.project, [{"event": "UserPromptSubmit"},
                                    {"event": "PostToolUse", "command": "gh pr view 44", "exit": 0}])
        self.assertFalse(sent_back(run(payload("PR #44 is merged."), self.project)))
        self.assertNotIn("unchecked", read_events(self.project)[-1])

    def test_lets_a_message_with_no_fact_through(self):
        write_events(self.project, [{"event": "UserPromptSubmit"}])
        self.assertFalse(sent_back(run(payload("The page now has a dark mode."), self.project)))

    def test_never_sends_back_twice_in_a_row(self):
        write_events(self.project, [{"event": "UserPromptSubmit"}])
        self.assertTrue(sent_back(run(payload("PR #44 is merged."), self.project)))
        self.assertFalse(sent_back(run(payload("PR #44 is merged.", stop_hook_active=True), self.project)))

    def test_a_lookup_after_the_send_back_lets_the_next_stop_through(self):
        write_events(self.project, [{"event": "UserPromptSubmit"}])
        self.assertTrue(sent_back(run(payload("PR #44 is merged."), self.project)))
        write_events(self.project, [{"event": "PostToolUse", "command": "gh pr view 44", "exit": 0}])
        self.assertFalse(sent_back(run(payload("PR #44 is merged."), self.project)))

    def test_both_notes_in_one_send_back(self):
        # The work ran out, the ask is missing, and a fact is unchecked: one send-back says both.
        write_events(self.project, FINISHED_AGENT[:-1])
        r = run(payload("PR #44 is merged."), self.project)
        context = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith("Say who holds the ball"))
        self.assertIn("You stated a GitHub fact without checking it this turn.", context)

    def test_a_valid_ask_with_an_unchecked_fact_is_sent_back_and_starts_no_reminder(self):
        write_events(self.project, FINISHED_AGENT[:-1])
        r = run(payload("PR #43 is merged.\n" + ONE), self.project)
        self.assertTrue(sent_back(r))
        context = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertFalse(context.startswith("Say who holds the ball"))
        last = read_events(self.project)[-1]
        self.assertNotIn("holder", last)


class AsksDecision(unittest.TestCase):
    """Whether a message asks the human for a decision. Mentioning a decision, quoting one or talking
    about the check doesn't."""

    def test_decisions(self):
        for message in ["1 thing for you to do right now: decide the layout.",
                        "1 thing for you to do right now: 1 decision on the PR.",
                        "1 thing for you to do right now: make a Decision.",
                        "1 thing for you to do right now: choose a host.",
                        "1 thing for you to do right now: 1 decision on PR #72 (no rush).",
                        "**1 thing for you to do right now: answer decision 3 on PR #69.**",
                        "Still 1 thing for you to do right now: rule on the review of PR #45, including decision 7.",
                        "Answer decision 3 on PR #69 when you can.", "Please answer [decision 8 on PR #71].",
                        "2 decisions for you on PR #71.", "Two decisions for you on the card.",
                        "Do you want to decide the layout now?", "Should we pick the grid or the list?",
                        "Which do you want, the grid or the list?", "Which one would you prefer?",
                        "**Your call** on the colors.", "It's your call.",
                        "The layout:\n(a) a grid\n(b) a list",
                        "The layout:\n- **(a)** a grid\n- **(b)** a list",
                        "The layout:\na) a grid\nb) a list",
                        # A "Nothing" line mid-message is not the ask: the question after it still asks.
                        "Status:\nNothing for you to do right now. Waiting on CI, ETA 3:00 PM.\nWhich do you want: (a) squash or (b) merge?"]:
            self.assertTrue(stop.asks_decision(message), message)

    def test_not_decisions(self):
        for message in [None, "", ONE, NOTHING, MANY, ALL_CLEAR, "Done. The builder finished.",
                        "Nothing for you to do right now. Waiting on the reviewer to decide, ETA 7:10 PM.",
                        "There will likely be one decision for you. Which do you want?\n"
                        "Nothing for you to do right now. Waiting on the reviewer, ETA 7:10 PM.",
                        "1 thing for you to do right now: pick up the build after lunch.",
                        "1 thing for you to do right now: merge PR #72 (no decisions).",
                        "Indecisive tests, rerun.", "No decisions on PR #72; merge it.", "0 decisions on PR #72.",
                        "Zero decisions left, merge PR #72.", "No decisions for you.",
                        # Mentioning a decision, quoting one, or talking about the check.
                        "Decision 8 was presented the same way.",
                        "(the automatic check again, triggered by the word 'decision')",
                        "It fires if a line contains a word like \"decide\".",
                        "In your question \"what do I need to decide on? Are we consistent?\" I saw two parts.",
                        "> Which do you want?",
                        "Choose a host.", "Pick a name for the site.", "Decide later.",
                        "When you want to pick it up again, say so.",
                        "The builder picked a layout.\n1. Decide the colors.",
                        "Recommended: (b). Resolving the thread counts as (b).",
                        "1. **The decision hand-off for PR #78.** It named only option (b)."]:
            self.assertFalse(stop.asks_decision(message), message)


class HasLink(unittest.TestCase):
    def test_links(self):
        for message in ["Decide here: https://github.com/hornof/apb/pull/44",
                        "On the card (github.com/hornof/apb/issues/26).",
                        "Decide.\n\n- https://github.com/o/r-2/issues/7#issuecomment-1"]:
            self.assertTrue(stop.has_link(message), message)

    def test_not_links(self):
        for message in [None, "", "Decide on PR #44.", "Decide on #26.",
                        "See https://github.com/hornof/apb/pulls", "See https://github.com/hornof/apb",
                        "See https://github.com/hornof/apb/blob/main/README.md",
                        "Decide.\n> https://github.com/hornof/apb/pull/44",  # a quoted line
                        "Decide.\n  >  As the card says: github.com/hornof/apb/issues/26"]:
            self.assertFalse(stop.has_link(message), message)


class DecisionWithoutLink(unittest.TestCase):
    """A decision asked in the message, or in a numbered item under "N things", needs its GitHub link."""
    PR71 = "https://github.com/hornof/apb/pull/71"

    def test_sent_back(self):
        for message in [
                "1 thing for you to do right now: decide the layout on PR #44.",
                # The reviewer's example: a decision in an item, with no link at all.
                "2 things for you to do right now:\n1. 2 decisions on PR #71.\n2. merge PR #72.",
                # The link is in the message, but not on the item that asks the decision.
                "2 things for you to do right now:\n1. 2 decisions on PR #71.\n2. merge %s." % self.PR71,
                "2 things for you to do right now:\n1. 2 decisions on PR #71.\n2. merge PR #72.\n\n" + self.PR71,
                "2 things for you to do right now:\n\n1. merge PR #72.\n2. **Your call** on the colors.",
                "4 things for you to do right now:\n1. A.\n2. B.\n3. Pick a host.",  # a count that doesn't match
                "2 things for you to do right now:\n1. merge PR #72.\n2. 1 decision on PR #71 (no rush).",
                "Still 1 thing for you to do right now: rule on the review of PR #45, including decision 7.",
                "The builder is done. Which do you want, the grid or the list?",
                "The layout:\n(a) a grid\n(b) a list\n\n> https://github.com/hornof/apb/pull/44",
                "Status:\nNothing for you to do right now. Waiting on CI, ETA 3:00 PM.\nWhich do you want: (a) squash or (b) merge?"]:
            self.assertTrue(stop.decision_without_link(message), message)

    def test_passes(self):
        for message in [
                None, "", ONE, NOTHING, MANY,
                "1 thing for you to do right now: decide the layout on %s." % self.PR71,
                "2 things for you to do right now:\n1. 2 decisions on PR #71: %s\n2. merge PR #72." % self.PR71,
                "2 things for you to do right now:\n1. merge PR #72 (no decisions).\n2. merge PR #73.",
                "2 things for you to do right now:\n1. Zero decisions on PR #72; merge it.\n2. 0 decisions on #73.",
                # Below the list, a decision word is no longer an item.
                "2 things for you to do right now:\n1. merge PR #72.\n2. merge PR #73.\n\nDecide later on the rest.",
                "Progress: the builder picked a layout.\n1. Decide the colors.",  # no "N things" line
                "1 thing for you to do right now: answer [decision 3 on PR #69](%s#discussion_r1)." % self.PR71,
                "Which do you want? Both options are on %s." % self.PR71,
                "Decision 8 was presented the same way.", "I'll leave PR #78. When you want to pick it up, say so."]:
            self.assertFalse(stop.decision_without_link(message), message)


with open(os.path.join(HERE, "test_apb_stop_cases.json"), encoding="utf-8") as f:
    # Messages the hook sent back on one evening, written when the ask came first (before #153), each
    # trimmed to its first line and the lines that set the check off. "sent_back_for" is what the old checks said; "still_caught" is what a person reading
    # the message says: only a real unchecked fact or a decision handed off without its link.
    REPLAYED = json.load(f)


class Replay(unittest.TestCase):
    """The evening's send-backs, replayed: the false ones pass, the real ones are still caught."""

    def test_the_evening_has_both_kinds(self):
        self.assertGreaterEqual(len(REPLAYED), 40)
        caught = [c for c in REPLAYED if c["still_caught"]]
        self.assertEqual(sorted(k for c in caught for k in c["still_caught"]),
                         ["decision-without-link", "github-fact"])
        for c in REPLAYED:
            self.assertTrue(set(c["still_caught"]) <= set(c["sent_back_for"]), c["time"])

    def test_each_check_says_what_a_reader_says(self):
        for c in REPLAYED:
            self.assertEqual(bool(stop.stated_facts(c["message"])), "github-fact" in c["still_caught"],
                             (c["time"], c["message"], stop.stated_facts(c["message"])))
            self.assertEqual(stop.decision_without_link(c["message"]),
                             "decision-without-link" in c["still_caught"], (c["time"], c["message"]))

    def test_the_hook_sends_back_only_the_real_ones(self):
        for c in REPLAYED:
            project = apb_project()
            # A turn the human started, with no lookup in it: only the two checks can send it back.
            write_events(project, [{"event": "UserPromptSubmit"}])
            r = stop.handle(payload(c["message"]), project, time.time(), no_github, no_github)
            self.assertEqual(r["send_back"], bool(c["still_caught"]), (c["time"], c["message"]))
            last = read_events(project)[-1]
            self.assertEqual("unchecked" in last, "github-fact" in c["still_caught"], c["time"])
            self.assertEqual("decision-without-link" in last, "decision-without-link" in c["still_caught"],
                             c["time"])


class DecisionLink(unittest.TestCase):
    """The hook run the way Claude Code runs it: a decision asked in a hand-off needs a GitHub link."""
    NOTE = ("A decision was asked without a GitHub link. Put it on the PR or the card, then hand off "
            "with the link.")

    def setUp(self):
        self.project = apb_project()
        # The orchestrator looked something up, so the GitHub-fact check stays quiet.
        write_events(self.project, [{"event": "UserPromptSubmit"},
                                    {"event": "PostToolUse", "command": "gh pr view 44", "exit": 0}])

    def test_a_decision_with_a_link_passes(self):
        r = run(payload("1 thing for you to do right now: decide the layout on "
                        "https://github.com/hornof/apb/pull/44."), self.project)
        self.assertFalse(sent_back(r))
        last = read_events(self.project)[-1]
        self.assertNotIn("decision-without-link", last)
        self.assertEqual(last["holder"], "human")

    def test_a_decision_without_a_link_is_sent_back(self):
        r = run(payload("1 thing for you to do right now: decide the layout on PR #44."), self.project)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertNotIn("decision", out)  # a quiet note, not a block
        note = out["hookSpecificOutput"]
        self.assertEqual(note["hookEventName"], "Stop")
        self.assertEqual(note["additionalContext"], self.NOTE)
        last = read_events(self.project)[-1]
        self.assertEqual((last["event"], last["sent_back"], last["decision-without-link"]), ("Stop", True, True))
        self.assertNotIn("holder", last)

    def test_no_decision_and_no_link_passes(self):
        self.assertFalse(sent_back(run(payload(ONE), self.project)))
        self.assertNotIn("decision-without-link", read_events(self.project)[-1])

    def test_a_link_in_a_quoted_line_does_not_count(self):
        r = run(payload("1 thing for you to do right now: decide the layout.\n\n"
                        "> https://github.com/hornof/apb/pull/44"), self.project)
        self.assertTrue(sent_back(r))
        self.assertTrue(read_events(self.project)[-1]["decision-without-link"])

    def test_a_decision_in_a_numbered_item_without_a_link_is_sent_back(self):
        # The reviewer's example on PR #72.
        r = run(payload("2 things for you to do right now:\n1. 2 decisions on PR #71.\n2. merge PR #72."),
                self.project)
        self.assertEqual(json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"], self.NOTE)
        self.assertTrue(read_events(self.project)[-1]["decision-without-link"])

    def test_a_decision_in_a_numbered_item_with_its_link_passes(self):
        r = run(payload("2 things for you to do right now:\n1. 2 decisions on PR #71: "
                        "https://github.com/hornof/apb/pull/71\n2. merge PR #72."), self.project)
        self.assertFalse(sent_back(r))
        self.assertEqual(read_events(self.project)[-1]["holder"], "human")

    def test_never_sends_back_twice_in_a_row(self):
        message = "1 thing for you to do right now: decide the layout."
        self.assertTrue(sent_back(run(payload(message), self.project)))
        self.assertFalse(sent_back(run(payload(message, stop_hook_active=True), self.project)))

    def test_joins_the_other_notes_in_one_send_back(self):
        project = apb_project()
        write_events(project, FINISHED_AGENT[:-1])  # the work ran out, and no lookup this turn
        r = run(payload("Your call on PR #44, which is open."), project)
        context = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(context.startswith("Say who holds the ball"))
        self.assertIn("You stated a GitHub fact without checking it this turn.", context)
        self.assertTrue(context.endswith(self.NOTE))
        last = read_events(project)[-1]
        self.assertTrue(last["decision-without-link"])
        self.assertEqual(last["unchecked"], ["PR #44"])


APB = "hornof/apb"
APB_PR = "https://github.com/hornof/apb/pull/%d"


def pr(paths, body, state="OPEN"):
    return {"state": state, "body": body, "files": [{"path": p} for p in paths]}


class ApbRepo(unittest.TestCase):
    def test_comes_from_the_plugin_manifest(self):
        self.assertEqual(stop.apb_repo(), APB)


class ChangesProjects(unittest.TestCase):
    """A PR that touches these paths changes how a project runs."""

    def test_paths_that_change_projects(self):
        for path in ["templates/ci.yml", "templates/CLAUDE-template.md", "hooks/apb-stop.py",
                     "hooks/hooks.json", "skills/apb-build/SKILL.md", "docs/playbook.md",
                     "agents/builder.md", "agents/reviewer.md"]:
            self.assertTrue(stop.changes_projects(path), path)

    def test_paths_that_do_not(self):
        for path in ["README.md", "docs/MAP.md", "docs/playbook.md.bak", "scripts/apb-watch.py",
                     "skills/apb-build/notes.md", "skills/SKILL.md", "skills/a/b/SKILL.md",
                     "x/templates/a.md", "x/agents/builder.md", "templates", "hooks", "agents", "agents/",
                     "", None, 7]:
            self.assertFalse(stop.changes_projects(path), path)


class HasProjectsLine(unittest.TestCase):
    def test_lines_that_count(self):
        for body in ["Closes #73\n\nProjects: none",
                     "Projects: none, the hook's note only",
                     "Projects: None.",
                     "**Projects:** none",
                     "- Projects: https://github.com/hornof/sketch-gallery/pull/43",
                     "Projects: hornof/sketch-gallery#43",
                     "Projects: [sketch-gallery#43](https://github.com/hornof/sketch-gallery/pull/43), "
                     "https://github.com/o/r/pull/2",
                     "Intro\r\nProjects: none\r\n",
                     "`Projects: none`",  # as the playbook shows it
                     "## Projects: none",
                     "### Projects: `hornof/sketch-gallery#43`"]:
            self.assertTrue(stop.has_projects_line(body), body)

    def test_lines_that_do_not(self):
        for body in ["", None, 7, "Closes #73", "Projects:", "Projects: TBD", "Projects: sketch-gallery",
                     "Projects: nonexistent", "Projects: https://github.com/hornof/sketch-gallery/issues/43",
                     "> Projects: none", "Not in this PR: the sketch-gallery CLAUDE.md line",
                     "The Projects: none line comes later", "Projects: #43", "> `Projects: none`",
                     "## Projects", "`Projects:`"]:
            self.assertFalse(stop.has_projects_line(body), body)


class ApbPrs(unittest.TestCase):
    def test_finds_links_to_the_apb_repos_prs_once_each_in_order(self):
        message = ("2 things for you to do right now:\n1. merge %s\n2. merge %s and see %s again"
                   % (APB_PR % 74, APB_PR % 75, APB_PR % 74))
        self.assertEqual(stop.apb_prs(message, APB), [74, 75])

    def test_ignores_other_repos_issues_and_quoted_lines(self):
        message = ("1 thing for you to do right now: merge https://github.com/hornof/sketch-gallery/pull/43.\n"
                   "Card: https://github.com/hornof/apb/issues/73\n> %s\n"
                   "https://github.com/hornof/apb-other/pull/9 https://github.com/xhornof/apb/pull/8 "
                   "https://notgithub.com/hornof/apb/pull/7"
                   % (APB_PR % 74))
        self.assertEqual(stop.apb_prs(message, APB), [])
        self.assertEqual(stop.apb_prs(None, APB), [])
        self.assertEqual(stop.apb_prs(APB_PR % 74, None), [])


class ProjectsLineMissing(unittest.TestCase):
    """Which apb PRs a hand-off links that change projects and have no Projects line."""

    def lookup(self, prs):
        calls = []

        def look(repo, number):
            calls.append((repo, number))
            if number not in prs:
                raise subprocess.CalledProcessError(1, "gh")
            return prs[number]
        return look, calls

    def test_an_open_pr_that_changes_projects_without_the_line(self):
        look, calls = self.lookup({74: pr(["hooks/apb-stop.py"], "Closes #73")})
        self.assertEqual(stop.projects_line_missing("merge %s" % (APB_PR % 74), look), [74])
        self.assertEqual(calls, [(APB, 74)])

    def test_passes(self):
        cases = {
            "has the line": pr(["hooks/apb-stop.py"], "Closes #73\nProjects: none"),
            "changes no project": pr(["scripts/apb-watch.py", "README.md"], "Closes #73"),
            "merged": pr(["hooks/apb-stop.py"], "Closes #73", state="MERGED"),
            "closed": pr(["hooks/apb-stop.py"], "Closes #73", state="CLOSED"),
            "odd answer": ["not", "a", "pr"],
        }
        for name, answer in cases.items():
            look, _ = self.lookup({74: answer})
            self.assertEqual(stop.projects_line_missing("merge %s" % (APB_PR % 74), look), [], name)

    def test_a_failed_lookup_lets_the_stop_through(self):
        look, calls = self.lookup({})
        self.assertEqual(stop.projects_line_missing("merge %s" % (APB_PR % 74), look), [])
        self.assertEqual(len(calls), 1)

    def test_a_lookup_past_its_time_limit_lets_the_stop_through(self):
        def slow(repo, number):
            raise subprocess.TimeoutExpired("gh", stop.PR_TIME_LIMIT)
        self.assertEqual(stop.projects_line_missing("merge %s" % (APB_PR % 74), slow), [])

    def test_a_used_up_time_budget_looks_up_no_more_prs(self):
        # The first lookup takes the whole budget, so the second PR is never looked up.
        clock = [1000.0]
        prs = {1: pr(["hooks/a.py"], ""), 2: pr(["hooks/a.py"], "")}
        look, calls = self.lookup(prs)

        def slow_look(repo, number):
            clock[0] += stop.PR_BUDGET
            return look(repo, number)
        real_time = stop.time.time
        stop.time.time = lambda: clock[0]
        try:
            got = stop.projects_line_missing("%s %s" % (APB_PR % 1, APB_PR % 2), slow_look)
        finally:
            stop.time.time = real_time
        self.assertEqual(got, [1])
        self.assertEqual(calls, [(APB, 1)])

    def test_the_gh_call_gets_what_is_left_of_the_budget(self):
        clock = [1000.0]
        limits = []

        def fake_view(repo, number, time_limit=stop.PR_TIME_LIMIT):
            limits.append(time_limit)
            clock[0] += 3
            return pr(["hooks/a.py"], "")
        real_time, real_view = stop.time.time, stop.view_pr
        stop.time.time, stop.view_pr = (lambda: clock[0]), fake_view
        try:
            got = stop.projects_line_missing(" ".join(APB_PR % n for n in (1, 2, 3)))
        finally:
            stop.time.time, stop.view_pr = real_time, real_view
        self.assertEqual(got, [1, 2])
        self.assertEqual(limits, [min(stop.PR_TIME_LIMIT, stop.PR_BUDGET), min(stop.PR_TIME_LIMIT, stop.PR_BUDGET - 3)])

    def test_no_apb_link_means_no_lookup(self):
        look, calls = self.lookup({})
        stop.projects_line_missing("merge https://github.com/hornof/sketch-gallery/pull/43 and PR #74", look)
        self.assertEqual(calls, [])

    def test_looks_up_at_most_max_prs(self):
        prs = {n: pr(["templates/a.md"], "") for n in range(1, 10)}
        look, calls = self.lookup(prs)
        message = " ".join(APB_PR % n for n in range(1, 10))
        self.assertEqual(stop.projects_line_missing(message, look), list(range(1, stop.MAX_PRS + 1)))
        self.assertEqual(len(calls), stop.MAX_PRS)


class ProjectsLine(unittest.TestCase):
    """The hook run the way Claude Code runs it, with a fake gh: an apb PR handed to the human that
    changes how projects run needs a "Projects:" line in its description."""
    NOTE_START = "An apb PR in this hand-off changes how projects run"

    def setUp(self):
        self.project = apb_project()
        # The orchestrator looked something up, so the GitHub-fact check stays quiet.
        write_events(self.project, [{"event": "UserPromptSubmit"},
                                    {"event": "PostToolUse", "command": "gh pr view 74", "exit": 0}])
        self.bin = tempfile.mkdtemp()

    def run_with(self, message, prs, **extra):
        calls = fake_gh(self.bin, prs)
        env = {"PATH": self.bin + os.pathsep + DEFAULT_BIN + os.pathsep + os.environ["PATH"]}
        r = run(payload(message, **extra), self.project, env)
        with_calls = []
        if os.path.exists(calls):
            with open(calls) as f:
                with_calls = f.read().splitlines()
        return r, with_calls

    def test_a_hand_off_of_a_pr_without_the_line_is_sent_back(self):
        r, calls = self.run_with("1 thing for you to do right now: merge %s." % (APB_PR % 74),
                                 {74: pr(["hooks/apb-stop.py", "README.md"], "Closes #73")})
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertNotIn("decision", out)  # a quiet note, not a block
        note = out["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(note.startswith(self.NOTE_START), note)
        self.assertIn(APB_PR % 74, note)
        self.assertIn("Projects: none", note)
        # The note reads the same list as the playbook rule (#145): projects marked in use there, no other.
        self.assertIn("every project marked in use in the apb repo's docs/projects.md, and to no other", note)
        self.assertNotIn("every project in use", note)
        self.assertEqual(calls, ["pr view 74 --repo hornof/apb --json state,body,files"])
        last = read_events(self.project)[-1]
        self.assertEqual((last["sent_back"], last["projects-line-missing"]), (True, [74]))
        self.assertNotIn("holder", last)

    def test_a_pr_with_the_line_passes(self):
        r, _ = self.run_with("1 thing for you to do right now: merge %s." % (APB_PR % 74),
                             {74: pr(["skills/apb-build/SKILL.md"],
                                     "Closes #73\n\nProjects: https://github.com/hornof/sketch-gallery/pull/44")})
        self.assertFalse(sent_back(r))
        last = read_events(self.project)[-1]
        self.assertNotIn("projects-line-missing", last)
        self.assertEqual(last["holder"], "human")

    def test_only_a_hand_off_to_the_human_is_checked(self):
        r, calls = self.run_with("Nothing for you to do right now. Waiting on the reviewer for %s, ETA "
                                 "7:10 PM." % (APB_PR % 74), {74: pr(["hooks/a.py"], "")})
        self.assertFalse(sent_back(r))
        self.assertEqual(calls, [])

    def test_never_sends_back_twice_in_a_row(self):
        message = "1 thing for you to do right now: merge %s." % (APB_PR % 74)
        prs = {74: pr(["docs/playbook.md"], "")}
        self.assertTrue(sent_back(self.run_with(message, prs)[0]))
        r, calls = self.run_with(message, prs, stop_hook_active=True)
        self.assertFalse(sent_back(r))
        self.assertEqual(len(calls), 1)  # the retry looked nothing up

    def test_a_failing_gh_lets_the_stop_through(self):
        r, calls = self.run_with("1 thing for you to do right now: merge %s." % (APB_PR % 74), {})
        self.assertFalse(sent_back(r))
        self.assertEqual(len(calls), 1)
        self.assertEqual(read_events(self.project)[-1]["holder"], "human")

    def test_joins_the_decision_note_in_one_send_back(self):
        r, _ = self.run_with("2 things for you to do right now:\n1. merge %s\n2. decide the layout on PR #9."
                             % (APB_PR % 74), {74: pr(["templates/ci.yml"], "")})
        note = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertIn(self.NOTE_START, note)
        self.assertIn("A decision was asked without a GitHub link.", note)
        last = read_events(self.project)[-1]
        self.assertTrue(last["decision-without-link"])
        self.assertEqual(last["projects-line-missing"], [74])


class ProjectsLineProperties(unittest.TestCase):
    """Random PRs and descriptions, against an oracle written separately from the hook's patterns."""
    SEGMENTS = ["templates", "hooks", "skills", "docs", "scripts", "agents", "apb-build", "SKILL.md",
                "playbook.md", "MAP.md", "a.py", "ci.yml", "x"]

    @staticmethod
    def oracle_changes(path):
        parts = path.split("/")
        if parts[0] in ("templates", "hooks", "agents"):
            return len(parts) > 1 and all(parts[1:])
        if parts == ["docs", "playbook.md"]:
            return True
        return len(parts) == 3 and parts[0] == "skills" and parts[1] != "" and parts[2] == "SKILL.md"

    # (line, is a Projects line that counts)
    LINES = [("Closes #73", False), ("", False), ("Projects: none", True), ("**Projects:** none.", True),
             ("Projects: https://github.com/o/r/pull/3", True), ("Projects: o/r#3", True),
             ("Projects: later", False), ("> Projects: none", False), ("Projects:", False),
             ("Not in this PR: the project's CLAUDE.md line.", False), ("Projects: o/r/issues/3", False),
             ("`Projects: none`", True), ("## Projects: o/r#3", True), ("## Projects", False)]

    def test_paths(self):
        rng = random.Random(73)
        for _ in range(3000):
            path = "/".join(rng.choice(self.SEGMENTS + [""]) for _ in range(rng.randint(1, 4)))
            self.assertEqual(stop.changes_projects(path), self.oracle_changes(path), path)

    def test_prs_and_descriptions(self):
        rng = random.Random(74)
        for _ in range(500):
            paths = ["/".join(rng.choice(self.SEGMENTS) for _ in range(rng.randint(1, 3)))
                     for _ in range(rng.randint(0, 4))]
            lines = [rng.choice(self.LINES) for _ in range(rng.randint(0, 4))]
            body = rng.choice(["\n", "\r\n"]).join(l for l, _ in lines)
            state = rng.choice(["OPEN", "OPEN", "MERGED", "CLOSED"])
            want = (state == "OPEN" and any(self.oracle_changes(p) for p in paths)
                    and not any(counts for _, counts in lines))
            got = stop.projects_line_missing("merge " + APB_PR % 5, lambda repo, n: pr(paths, body, state))
            self.assertEqual(got, [5] if want else [], (paths, body, state))


THREAD = "https://github.com/hornof/apb/pull/82#discussion_r4627001605"
# The line sent at 2:08 AM, after the human had resolved the thread.
STALE = ("1 thing for you to do right now: answer [decision 3 on PR #82 (apb#70)](%s). The reviewer "
         "recommends (a), correct the past card times on a new card. Resolving the thread with no reply "
         "counts as (a)." % THREAD)


class Targets(unittest.TestCase):
    """What a hand-off asks the human to act on."""

    def test_targets(self):
        cases = {
            STALE: [("thread", APB, 82, 4627001605)],
            "1 thing for you to do right now: merge [PR #84 (apb#83)](%s)." % (APB_PR % 84): [("pr", APB, 84)],
            "1 thing for you to do right now: merge PR #44.": [("pr", None, 44)],
            "1 thing for you to do right now: Merge PR #44 and PR #45.": [("pr", None, 44), ("pr", None, 45)],
            "1 thing for you to do right now: answer [decision 8](https://github.com/hornof/apb/pull/46/files"
            "#discussion_r11).": [("thread", APB, 46, 11)],
            "2 things for you to do right now:\n1. answer [decision 3](%s)\n2. merge [PR #82](%s)"
            % (THREAD, APB_PR % 82): [("thread", APB, 82, 4627001605), ("pr", APB, 82)],
            "**2 things for you to do right now:**\n\n1. merge PR #4.\n2. merge PR #4 again.": [("pr", None, 4)],
            # The thread is a target on any hand-off line. A thread link names no PR to merge, so the
            # merge's PR is the one the line names.
            "1 thing for you to do right now: merge PR #82 after you answer [decision 3](%s)." % THREAD:
                [("thread", APB, 82, 4627001605), ("pr", None, 82)],
            "1 thing for you to do right now: answer [decision 3](https://github.com/hornof/apb/pull/82"
            "#discussion_r111) so I can merge PR #84.": [("thread", APB, 82, 111), ("pr", None, 84)],
            "1 thing for you to do right now: answer decision 3 (https://github.com/hornof/apb/pull/82"
            "#discussion_r111), then merge [PR #84](%s)." % (APB_PR % 84): [("thread", APB, 82, 111), ("pr", APB, 84)],
        }
        for message, want in cases.items():
            self.assertEqual(stop.targets(message), want, message)

    def test_not_targets(self):
        for message in [
                NOTHING, ALL_CLEAR, None, "", "Progress: merge PR #44.",
                "Nothing for you to do right now. Waiting on CI for %s, ETA 7:10 PM." % THREAD,
                "1 thing for you to do right now: say \"merge PR #44\" when ready.",  # quoted words
                "1 thing for you to do right now: read the review of PR #44.",  # no merge, no thread link
                "1 thing for you to do right now: check that PR #44 merged cleanly.",  # "merged" isn't "merge"
                "1 thing for you to do right now: read the card.\n%s\nmerge PR #44" % THREAD,  # not on the line
                "2 things for you to do right now:\n1. read the card.\n2. sign in.\n\nmerge PR #4; see %s" % THREAD,
                "3 things for you to do right now:\n1. merge PR #4.\n2. sign in."]:  # the count is wrong
            self.assertEqual(stop.targets(message), [], message)


def fake_github(prs=None, threads=None, **kwargs):
    """A GitHub whose lookups answer from prs {(repo, n): pr} and threads {(repo, n): {id: resolved}},
    failing for anything else. Returns (github, the calls it got)."""
    calls = []

    def look(kind, table):
        def answer(repo, number):
            calls.append((kind, repo, number))
            if (repo, number) not in table:
                raise subprocess.CalledProcessError(1, "gh")
            return table[(repo, number)]
        return answer
    return stop.GitHub(None, look("pr", prs or {}), look("threads", threads or {}), **kwargs), calls


class AlreadyDone(unittest.TestCase):
    def test_a_resolved_thread(self):
        github, calls = fake_github(threads={(APB, 82): {4627001605: True, 4627001606: True}})
        self.assertEqual(stop.already_done(STALE, github), ["the decision thread %s is resolved" % THREAD])
        self.assertEqual(calls, [("threads", APB, 82)])

    def test_a_link_to_a_reply_finds_its_thread(self):
        github, _ = fake_github(threads={(APB, 82): {4627001604: True, 4627001605: True}})
        self.assertEqual(len(stop.already_done(STALE, github)), 1)

    def test_an_unresolved_or_unknown_thread_passes(self):
        for found in [{4627001605: False}, {}, {99: True}, ["odd"]]:
            github, _ = fake_github(threads={(APB, 82): found})
            self.assertEqual(stop.already_done(STALE, github), [], found)

    def test_a_merged_or_closed_pr(self):
        message = "1 thing for you to do right now: merge [PR #84](%s)." % (APB_PR % 84)
        for state, want in [("MERGED", "PR #84 is merged (%s)" % (APB_PR % 84)),
                            ("CLOSED", "PR #84 is closed (%s)" % (APB_PR % 84))]:
            github, _ = fake_github(prs={(APB, 84): pr([], "", state)})
            self.assertEqual(stop.already_done(message, github), [want])
        github, _ = fake_github(prs={(APB, 84): pr([], "")})
        self.assertEqual(stop.already_done(message, github), [])

    def test_a_pr_named_with_no_link_is_looked_up_in_the_projects_repo(self):
        github, calls = fake_github(prs={(None, 44): pr([], "", "MERGED")})
        self.assertEqual(stop.already_done(ONE, github), ["PR #44 is merged"])
        self.assertEqual(calls, [("pr", None, 44)])

    def test_a_failed_lookup_lets_the_stop_through(self):
        github, calls = fake_github()
        self.assertEqual(stop.already_done(STALE + "\n", github), [])
        self.assertEqual(stop.already_done(ONE, github), [])
        self.assertEqual(len(calls), 2)

    def test_each_pr_and_each_prs_threads_looked_up_once(self):
        message = ("3 things for you to do right now:\n1. answer [decision 3](%s)\n2. answer [decision 4]("
                   "https://github.com/hornof/apb/pull/82#discussion_r7)\n3. merge [PR #82](%s)"
                   % (THREAD, APB_PR % 82))
        github, calls = fake_github(prs={(APB, 82): pr([], "", "MERGED")},
                                    threads={(APB, 82): {4627001605: True, 7: False}})
        self.assertEqual(len(stop.already_done(message, github)), 2)
        self.assertEqual(calls, [("threads", APB, 82), ("pr", APB, 82)])

    def test_looks_up_at_most_max_done(self):
        message = "1 thing for you to do right now: merge %s." % " ".join("PR #%d" % n for n in range(1, 9))
        github, calls = fake_github(prs={(None, n): pr([], "", "MERGED") for n in range(1, 9)})
        self.assertEqual(len(stop.already_done(message, github)), stop.MAX_DONE)
        self.assertEqual(len(calls), stop.MAX_DONE)

    def test_the_gh_calls_share_one_budget_with_the_projects_check(self):
        clock = [1000.0]
        limits = []

        def fake_view(repo, number, time_limit=stop.PR_TIME_LIMIT, cwd=None):
            limits.append(("pr", number, time_limit))
            clock[0] += 3
            return pr(["hooks/a.py"], "")

        def fake_threads(repo, number, time_limit=stop.PR_TIME_LIMIT):
            limits.append(("threads", number, time_limit))
            clock[0] += 2
            return {}
        message = ("2 things for you to do right now:\n1. answer [decision 3](https://github.com/o/r/pull/82"
                   "#discussion_r1)\n2. merge [PR #5](%s)" % (APB_PR % 5))
        saved = stop.time.time, stop.view_pr, stop.view_threads
        stop.time.time, stop.view_pr, stop.view_threads = (lambda: clock[0]), fake_view, fake_threads
        try:
            github = stop.GitHub()
            self.assertEqual(stop.already_done(message, github), [])
            self.assertEqual(stop.projects_line_missing(message + " " + APB_PR % 6, github=github), [5, 6])
        finally:
            stop.time.time, stop.view_pr, stop.view_threads = saved
        # 6 seconds in all: threads (4 allowed, takes 2), PR #5 (4 allowed, takes 3), then 1 second
        # left, and PR #5's answer is reused, so PR #6 gets the last second.
        self.assertEqual(limits, [("threads", 82, 4), ("pr", 5, 4), ("pr", 6, 1)])

    def test_the_real_gh_calls(self):
        # The gh calls made from a folder with a fake gh: the arguments, and how the answers are read.
        folder = tempfile.mkdtemp()
        calls = fake_gh(folder, {44: pr([], "", "MERGED")}, {82: {4627001605: True}})
        path = os.environ["PATH"]
        os.environ["PATH"] = folder + os.pathsep + path
        try:
            self.assertEqual(stop.view_pr(None, 44, 4, cwd=folder)["state"], "MERGED")
            self.assertEqual(stop.view_threads(APB, 82, 4), {4627001605: True, 4627001606: True})
            with self.assertRaises(subprocess.CalledProcessError):
                stop.view_threads(APB, 83, 4)
        finally:
            os.environ["PATH"] = path
        with open(calls) as f:
            got = f.read().splitlines()
        self.assertEqual(got[0], "pr view 44 --json state,body,files")
        self.assertTrue(got[1].startswith("api graphql -F number=82 -f owner=hornof -f name=apb -f query="), got[1])
        self.assertIn("reviewThreads", got[1])


class AlreadyDoneProperties(unittest.TestCase):
    """Random hand-offs of threads and merges in random states, against an oracle that knows by
    construction what each line asks for."""

    def test_random_hand_offs(self):
        rng = random.Random(85)
        for _ in range(400):
            prs = {n: rng.choice(["OPEN", "MERGED", "CLOSED", None]) for n in range(1, 6)}  # None: lookup fails
            threads = {cid: rng.choice([True, False, None]) for cid in range(100, 106)}  # None: no such thread
            lines, want, seen = [], [], []
            for _ in range(rng.randint(1, 4)):
                kind = rng.choice(["thread", "merge link", "merge bare", "other", "quoted"])
                if kind == "thread":
                    cid = rng.choice(sorted(threads))
                    lines.append("answer [decision 3](https://github.com/o/r/pull/9#discussion_r%d)" % cid)
                    key, done = ("thread", cid), threads[cid] is True
                    text = "the decision thread https://github.com/o/r/pull/9#discussion_r%d is resolved" % cid
                elif kind in ("merge link", "merge bare"):
                    n = rng.choice(sorted(prs))
                    link = kind == "merge link"
                    lines.append("merge [PR #%d](https://github.com/o/r/pull/%d)" % (n, n) if link else
                                 "merge PR #%d" % n)
                    key, done = ("pr", link, n), prs[n] in ("MERGED", "CLOSED")
                    text = "PR #%d is %s" % (n, (prs[n] or "").lower()) + (
                        " (https://github.com/o/r/pull/%d)" % n if link else "")
                elif kind == "other":
                    lines.append("sign in to Cloudflare")
                    continue
                else:
                    lines.append("say \"merge PR #%d\"" % rng.choice(sorted(prs)))
                    continue
                if key not in seen:
                    seen.append(key)
                    if done and len(seen) <= stop.MAX_DONE:
                        want.append(text)
            if len(lines) == 1:
                message = "1 thing for you to do right now: %s." % lines[0]
            else:
                message = "%d things for you to do right now:\n" % len(lines) + "\n".join(
                    "%d. %s." % (i, l) for i, l in enumerate(lines, 1))

            def pr_answer(repo, n):
                if repo not in ("o/r", None) or prs[n] is None:
                    raise subprocess.CalledProcessError(1, "gh")
                return pr([], "", prs[n])

            def thread_answer(repo, n):
                return {cid: r for cid, r in threads.items() if r is not None}
            github = stop.GitHub(None, pr_answer, thread_answer)
            self.assertEqual(stop.already_done(message, github), want, message)


class AlreadyDoneHook(unittest.TestCase):
    """The hook run the way Claude Code runs it, with a fake gh: a hand-off never asks for something
    already done."""
    NOTE_START = "A hand-off asks the human for something that is already done:"

    def setUp(self):
        self.project = apb_project()
        # The orchestrator looked something up, so the GitHub-fact check stays quiet.
        write_events(self.project, [{"event": "UserPromptSubmit"},
                                    {"event": "PostToolUse", "command": "gh pr view 82", "exit": 0}])
        self.bin = tempfile.mkdtemp()

    def run_with(self, message, prs=None, threads=None, **extra):
        calls = fake_gh(self.bin, prs, threads)
        env = {"PATH": self.bin + os.pathsep + DEFAULT_BIN + os.pathsep + os.environ["PATH"]}
        r = run(payload(message, **extra), self.project, env)
        got = []
        if os.path.exists(calls):
            with open(calls) as f:
                got = f.read().splitlines()
        return r, got

    # The stale line's lookups: its thread, then, for the Projects-line check, its apb PR.
    LOOKUPS = ["api graphql -F number=82", "pr view 82 --repo hornof/apb --json state,body,files"]

    @staticmethod
    def looked_up(calls):
        return [c.split(" -f ")[0] for c in calls]

    def test_the_stale_line_is_sent_back(self):
        r, calls = self.run_with(STALE, threads={82: {4627001605: True}})
        out = json.loads(r.stdout)
        self.assertNotIn("decision", out)  # a quiet note, not a block
        note = out["hookSpecificOutput"]["additionalContext"]
        self.assertTrue(note.startswith(self.NOTE_START), note)
        self.assertIn(THREAD, note)
        self.assertEqual(self.looked_up(calls), self.LOOKUPS)
        last = read_events(self.project)[-1]
        self.assertEqual((last["sent_back"], last["already-done"]),
                         (True, ["the decision thread %s is resolved" % THREAD]))
        self.assertNotIn("holder", last)

    def test_an_unresolved_thread_passes(self):
        r, _ = self.run_with(STALE, threads={82: {4627001605: False}})
        self.assertFalse(sent_back(r))
        last = read_events(self.project)[-1]
        self.assertNotIn("already-done", last)
        self.assertEqual(last["holder"], "human")

    def test_merging_a_merged_pr_is_sent_back(self):
        r, calls = self.run_with("2 things for you to do right now:\n1. sign in.\n2. merge PR #44.",
                                 prs={44: pr([], "", "MERGED")})
        note = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(note, stop.DONE % "PR #44 is merged")
        self.assertEqual(calls, ["pr view 44 --json state,body,files"])
        self.assertEqual(read_events(self.project)[-1]["already-done"], ["PR #44 is merged"])

    def test_merging_an_open_pr_passes(self):
        r, _ = self.run_with(ONE, prs={44: pr([], "")})
        self.assertFalse(sent_back(r))

    def test_never_sends_back_twice_in_a_row(self):
        self.assertTrue(sent_back(self.run_with(STALE, threads={82: {4627001605: True}})[0]))
        r, calls = self.run_with(STALE, threads={82: {4627001605: True}}, stop_hook_active=True)
        self.assertFalse(sent_back(r))
        self.assertEqual(self.looked_up(calls), self.LOOKUPS)  # the retry looked nothing up

    def test_only_a_hand_off_is_checked(self):
        r, calls = self.run_with("Nothing for you to do right now. Waiting on the reviewer, ETA 7:10 PM.\n"
                                 "Then: merge PR #44, see %s" % THREAD,
                                 prs={44: pr([], "", "MERGED")}, threads={82: {4627001605: True}})
        self.assertFalse(sent_back(r))
        self.assertEqual(calls, [])

    def test_a_failing_gh_lets_the_stop_through(self):
        r, calls = self.run_with(STALE)
        self.assertFalse(sent_back(r))
        self.assertEqual(self.looked_up(calls), self.LOOKUPS)
        self.assertEqual(read_events(self.project)[-1]["holder"], "human")

    def test_shares_the_pr_lookup_and_the_send_back_with_the_projects_check(self):
        message = "1 thing for you to do right now: merge [PR #74](%s)." % (APB_PR % 74)
        r, calls = self.run_with(message, prs={74: pr(["hooks/apb-stop.py"], "Closes #73", "CLOSED")})
        note = json.loads(r.stdout)["hookSpecificOutput"]["additionalContext"]
        self.assertEqual(note, stop.DONE % ("PR #74 is closed (%s)" % (APB_PR % 74)))  # a closed PR needs no line
        self.assertEqual(calls, ["pr view 74 --repo hornof/apb --json state,body,files"])


class WorkRanOut(unittest.TestCase):
    now = time.time()

    def events(self, *groups):
        out = []
        for g in groups:
            out.extend(dict(e, session=e.get("session", "s1"), time=stamp(self.now)) for e in g)
        return out

    def test_last_agent_finished_in_this_turn(self):
        self.assertTrue(stop.work_ran_out(self.events(FINISHED_AGENT), "s1", self.now))

    def test_an_agent_still_running(self):
        evs = self.events(FINISHED_AGENT, [{"event": "SubagentStart", "agent_id": "r1"}])
        self.assertFalse(stop.work_ran_out(evs, "s1", self.now))

    def test_an_agent_running_in_another_session_counts(self):
        evs = self.events(FINISHED_AGENT, [{"event": "SubagentStart", "agent_id": "r1", "session": "s2"}])
        self.assertFalse(stop.work_ran_out(evs, "s1", self.now))

    def test_an_agent_silent_for_hours_is_taken_to_be_gone(self):
        old = dict(event="SubagentStart", agent_id="x", session="s1", time=stamp(self.now - 4 * 3600))
        self.assertTrue(stop.work_ran_out([old] + self.events(FINISHED_AGENT), "s1", self.now))

    def test_an_answer_after_the_stop_is_not_a_stop(self):
        evs = self.events(FINISHED_AGENT, [{"event": "Stop"}, {"event": "UserPromptSubmit"}])
        self.assertFalse(stop.work_ran_out(evs, "s1", self.now))

    def test_a_stop_sent_back_does_not_end_the_turn(self):
        evs = self.events(FINISHED_AGENT[1:], [{"event": "Stop", "sent_back": True}])
        self.assertTrue(stop.work_ran_out(evs, "s1", self.now))

    def test_another_sessions_prompt_does_not_start_this_turn(self):
        evs = self.events([{"event": "UserPromptSubmit"}, {"event": "Stop"},
                           {"event": "UserPromptSubmit", "session": "s2"}])
        self.assertTrue(stop.work_ran_out(evs, "s1", self.now))

    def test_background_tasks_from_claude_code_decide_what_is_running(self):
        evs = self.events(FINISHED_AGENT)
        shell = [{"id": "t1", "type": "shell", "status": "running", "command": "python3 apb-watch.py"}]
        self.assertFalse(stop.work_ran_out(evs, "s1", self.now, shell))
        self.assertTrue(stop.work_ran_out(evs, "s1", self.now, []))
        # A watcher started in this turn, already gone by the time the turn ends: nothing runs.
        evs2 = self.events(FINISHED_AGENT, [{"event": "PostToolUse", "command": "w", "background": True}])
        self.assertTrue(stop.work_ran_out(evs2, "s1", self.now, []))
        # An agent the events file still thinks is running, but Claude Code says is gone.
        evs3 = self.events(FINISHED_AGENT, [{"event": "SubagentStart", "agent_id": "r1"}])
        self.assertTrue(stop.work_ran_out(evs3, "s1", self.now, []))

    def test_another_sessions_stop_does_not_end_this_turn(self):
        evs = self.events(FINISHED_AGENT, [{"event": "Stop", "session": "s2"}])
        self.assertTrue(stop.work_ran_out(evs, "s1", self.now))

    def test_a_watcher_finishing_wakes_a_turn_with_no_prompt(self):
        evs = self.events([{"event": "UserPromptSubmit"},
                           {"event": "PostToolUse", "command": "python3 apb-watch.py", "background": True},
                           {"event": "Stop"}])
        self.assertTrue(stop.work_ran_out(evs, "s1", self.now))

    def test_a_watcher_started_in_this_turn_is_still_running(self):
        evs = self.events(FINISHED_AGENT,
                          [{"event": "PostToolUse", "command": "python3 apb-watch.py", "background": True}])
        self.assertFalse(stop.work_ran_out(evs, "s1", self.now))

    def test_a_wake_logged_as_an_automatic_prompt_is_no_prompt(self):
        # Claude Code wakes the orchestrator with a prompt of its own (a watcher's report, a task
        # notification, an agent's message); apb-events.py logs it with "automatic": true. The turn it
        # starts was not started by the human, so when nothing runs, the work has run out (#134 review).
        wake = {"event": "UserPromptSubmit", "automatic": True}
        evs = self.events([{"event": "UserPromptSubmit"}, {"event": "Stop"}, wake])
        self.assertTrue(stop.work_ran_out(evs, "s1", self.now))
        self.assertTrue(stop.work_ran_out(evs, "s1", self.now, []))
        # Both directions: the human's prompt in the same place is an answer, not a stop ...
        human = self.events([{"event": "UserPromptSubmit"}, {"event": "Stop"},
                             {"event": "UserPromptSubmit", "routine": True}])
        self.assertFalse(stop.work_ran_out(human, "s1", self.now, []))
        # ... and a human prompt in the turn wins over a wake in it.
        both = self.events([{"event": "UserPromptSubmit"}, {"event": "Stop"}, wake, {"event": "UserPromptSubmit"}])
        self.assertFalse(stop.work_ran_out(both, "s1", self.now, []))
        # Something still running: not run out, wake or not.
        self.assertFalse(stop.work_ran_out(evs, "s1", self.now, [{"id": "t", "type": "shell"}]))
        # An automatic prompt alone also shows that prompts are logged.
        self.assertTrue(stop.work_ran_out(self.events([wake, {"event": "Stop"}, wake]), "s1", self.now))

    def test_a_wake_logged_as_an_automatic_prompt_may_end_quietly(self):
        # The quiet wake-up exception still holds when the wake is logged as an automatic prompt.
        evs = self.events([{"event": "UserPromptSubmit"}, {"event": "Stop", "holder": "claude"},
                           {"event": "UserPromptSubmit", "automatic": True}])
        self.assertTrue(stop.quiet_wake_up(evs, "s1", "Noted."))
        self.assertFalse(stop.quiet_wake_up(evs, "s1", "Nothing for you to do."))
        human = self.events([{"event": "UserPromptSubmit"}, {"event": "Stop", "holder": "claude"},
                             {"event": "UserPromptSubmit"}])
        self.assertFalse(stop.quiet_wake_up(human, "s1", "Noted."))

    def test_a_plain_question_and_answer(self):
        evs = self.events([{"event": "UserPromptSubmit"}, {"event": "Stop"}, {"event": "UserPromptSubmit"},
                           {"event": "PostToolUse", "command": "git status", "exit": 0}])
        self.assertFalse(stop.work_ran_out(evs, "s1", self.now))

    def test_without_any_prompt_lines_a_turn_with_no_agent_is_not_a_stop(self):
        # Prompts not logged (an older plugin wrote the file): don't guess.
        self.assertFalse(stop.work_ran_out(self.events([{"event": "Stop"}]), "s1", self.now))


class Hook(unittest.TestCase):
    """The hook run the way Claude Code runs it."""

    def setUp(self):
        self.project = apb_project()

    def test_sends_the_message_back_when_the_work_ran_out_and_the_line_is_missing(self):
        write_events(self.project, FINISHED_AGENT)
        r = run(payload("PR #44 is ready. The reviewer found nothing."), self.project)
        self.assertEqual(r.returncode, 0)
        out = json.loads(r.stdout)
        self.assertNotIn("decision", out)  # a quiet note, not a block
        note = out["hookSpecificOutput"]
        self.assertEqual(note["hookEventName"], "Stop")
        self.assertTrue(note["additionalContext"].startswith("Say who holds the ball"))
        for form in ["Nothing for you to do right now.", "1 thing for you to do right now:",
                     "things for you to do right now:", "All clear: <boards> empty, no open PRs, as of <time>."]:
            self.assertIn(form, note["additionalContext"])
        last = read_events(self.project)[-1]
        self.assertEqual((last["event"], last["sent_back"]), ("Stop", True))

    def test_allows_each_of_the_four_forms(self):
        for message in [NOTHING, ONE, MANY, ALL_CLEAR]:
            project = apb_project()
            write_events(project, FINISHED_AGENT)
            r = run(payload(message), project)
            self.assertFalse(sent_back(r), message)
            last = read_events(project)[-1]
            self.assertEqual(last["event"], "Stop")
            self.assertNotIn("sent_back", last)

    def test_the_stop_line_records_who_holds_the_ball(self):
        write_events(self.project, FINISHED_AGENT)
        run(payload(ONE), self.project)
        last = read_events(self.project)[-1]
        self.assertEqual((last["holder"], last["todo"]), ("human", "merge PR #44"))

    def test_all_clear_is_logged_as_the_humans_turn(self):
        write_events(self.project, FINISHED_AGENT)
        self.assertFalse(sent_back(run(payload(ALL_CLEAR), self.project)))
        last = read_events(self.project)[-1]
        self.assertEqual((last["event"], last["holder"], last["todo"]), ("Stop", "human", "all clear"))
        self.assertNotIn("sent_back", last)

    def test_never_sends_back_twice_in_a_row(self):
        write_events(self.project, FINISHED_AGENT)
        self.assertTrue(sent_back(run(payload("Done."), self.project)))
        self.assertFalse(sent_back(run(payload("Done.", stop_hook_active=True), self.project)))

    def test_does_not_send_back_an_answer_between_stops(self):
        write_events(self.project, FINISHED_AGENT)
        run(payload(NOTHING), self.project)
        write_events(self.project, [{"event": "UserPromptSubmit"}])
        self.assertFalse(sent_back(run(payload("The PR changes two files."), self.project)))

    def test_does_not_send_back_while_an_agent_runs(self):
        write_events(self.project, [{"event": "UserPromptSubmit"},
                                    {"event": "SubagentStart", "agent_id": "b1", "agent_type": "apb:builder"}])
        self.assertFalse(sent_back(run(payload("Started the builder."), self.project)))

    def test_does_not_send_back_while_claude_code_lists_background_work(self):
        write_events(self.project, FINISHED_AGENT)
        tasks = [{"id": "t1", "type": "shell", "status": "running", "command": "python3 apb-watch.py"}]
        self.assertFalse(sent_back(run(payload("Watching PR #44.", background_tasks=tasks), self.project)))
        self.assertTrue(sent_back(run(payload("Done.", background_tasks=[]), self.project)))

    def woken(self, earlier_line=True, helper=False):
        """A session where, after the human's prompt, the orchestrator stopped (with an ask line, or
        without one), and then a watcher woke it: a turn with no prompt and no agent. With helper, the
        turn starts with the SubagentStop that Claude Code's own helper logs after each stop."""
        write_events(self.project, [{"event": "UserPromptSubmit"}])
        run(payload(NOTHING if earlier_line else "Looking.", background_tasks=[]), self.project)
        if helper:
            write_events(self.project, [{"event": "SubagentStop", "agent_id": "x9", "agent_type": ""}])
        write_events(self.project, [{"event": "PostToolUse", "command": "gh pr view 44", "exit": 0}])

    def test_a_wake_up_that_changes_nothing_may_end_with_no_message(self):
        for message, helper in [("", False), ("  \n", False), ("Noted.", False), ("**Ok**", False),
                                ("", True), ("Noted.", True)]:
            self.setUp()
            self.woken(helper=helper)
            r = run(payload(message, background_tasks=[]), self.project)
            self.assertFalse(sent_back(r), repr(message))
            last = read_events(self.project)[-1]
            self.assertEqual(last["event"], "Stop")
            self.assertNotIn("sent_back", last)
            self.assertNotIn("holder", last)  # the ball stays where the last line put it

    def test_a_wake_up_with_more_than_one_word_still_needs_the_line(self):
        self.woken()
        r = run(payload("That alert was my own reply on the card.", background_tasks=[]), self.project)
        self.assertTrue(sent_back(r))

    def test_a_quiet_ending_needs_an_earlier_ask(self):
        self.woken(earlier_line=False)
        self.assertTrue(sent_back(run(payload("", background_tasks=[]), self.project)))

    def test_an_agents_report_is_not_a_quiet_wake_up(self):
        write_events(self.project, [{"event": "UserPromptSubmit"}])
        run(payload(NOTHING, background_tasks=[]), self.project)
        write_events(self.project, FINISHED_AGENT[1:])
        self.assertTrue(sent_back(run(payload("Done.", background_tasks=[]), self.project)))

    def test_reads_the_message_from_the_transcript_when_it_is_not_given(self):
        write_events(self.project, FINISHED_AGENT)
        transcript = os.path.join(self.project, "t.jsonl")
        with open(transcript, "w") as f:
            f.write(json.dumps({"type": "user", "message": {"content": "go"}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "Working on it."}]}}) + "\n")
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": NOTHING}]}}) + "\n")
        p = payload(None, transcript_path=transcript)
        del p["last_assistant_message"]
        self.assertFalse(sent_back(run(p, self.project)))
        p_bad = dict(p)
        with open(transcript, "a") as f:
            f.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "text", "text": "All done."}]}}) + "\n")
        self.assertTrue(sent_back(run(p_bad, self.project)))

    def test_non_apb_folder_gets_nothing(self):
        d = tempfile.mkdtemp()
        r = run(payload("Done."), d)
        self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""))
        self.assertEqual(os.listdir(d), [])

    def test_bad_input_never_sends_back(self):
        for bad in [b"", b"not json", b"[1]", b"null", b"{", b"\xff\xfe",
                    b'{"hook_event_name": "Stop"}', b'{"hook_event_name": "Stop", "session_id": 5}',
                    b'{"hook_event_name": "Stop", "last_assistant_message": 7}']:
            r = run(bad, self.project)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, b"", b""), bad)

    def test_a_broken_events_file_never_sends_back(self):
        os.makedirs(os.path.join(self.project, ".apb"))
        with open(os.path.join(self.project, ".apb", "events.jsonl"), "wb") as f:
            f.write(b"\xff not json\n{\"event\": 5}\n[]\n")
        r = run(payload("Done."), self.project)
        self.assertEqual(r.returncode, 0)
        self.assertFalse(sent_back(r))

    def test_it_is_fast_on_a_long_events_file(self):
        write_events(self.project, FINISHED_AGENT * 5000)
        start = time.time()
        run(payload(NOTHING), self.project)
        self.assertLess(time.time() - start, 3.0)


class Reminder(unittest.TestCase):
    """Rule 4: the human has held the ball for a while with no action: one reminder."""

    def setUp(self):
        self.project = apb_project()
        self.bin, self.calls = fake_osascript()
        self.env = {"APB_REMIND_AFTER_SECONDS": "1", "PATH": self.bin + os.pathsep + os.environ["PATH"]}

    def alerts(self, wait):
        deadline = time.time() + wait
        while time.time() < deadline:
            time.sleep(0.2)
        if not os.path.exists(self.calls):
            return []
        with open(self.calls) as f:
            return [a for a in f.read().split("---\n") if a.strip()]

    def test_one_reminder_when_the_human_holds_the_ball(self):
        write_events(self.project, FINISHED_AGENT)
        run(payload(ONE), self.project, self.env)
        got = self.alerts(3.5)
        self.assertEqual(len(got), 1)
        self.assertIn("Reminder, your turn: merge PR #44", got[0])
        self.assertIn("Glass", got[0])

    def test_reminder_for_several_things(self):
        write_events(self.project, FINISHED_AGENT)
        run(payload(MANY), self.project, self.env)
        got = self.alerts(3.5)
        self.assertEqual(len(got), 1)
        self.assertIn("Reminder, your turn: 2 things", got[0])

    def test_no_reminder_when_claude_holds_the_ball(self):
        write_events(self.project, FINISHED_AGENT)
        run(payload(NOTHING), self.project, self.env)
        self.assertEqual(self.alerts(3), [])

    def test_no_reminder_after_all_clear(self):
        # All clear gets one alert, from the orchestrator; nothing more until the human speaks.
        write_events(self.project, FINISHED_AGENT)
        run(payload(ALL_CLEAR), self.project, self.env)
        self.assertEqual(self.alerts(3), [])

    def test_an_answer_after_all_clear_starts_no_reminder(self):
        write_events(self.project, FINISHED_AGENT)
        run(payload(ALL_CLEAR), self.project, self.env)
        write_events(self.project, [{"event": "UserPromptSubmit"}])
        run(payload("The last PR changed two files."), self.project, self.env)
        self.assertEqual(self.alerts(3), [])

    def test_no_reminder_after_the_human_acts(self):
        write_events(self.project, FINISHED_AGENT)
        run(payload(ONE), self.project, self.env)
        write_events(self.project, [{"event": "UserPromptSubmit"}])
        self.assertEqual(self.alerts(3), [])

    def test_an_answer_while_the_human_holds_the_ball_starts_a_new_wait(self):
        write_events(self.project, FINISHED_AGENT)
        run(payload(ONE), self.project, {"APB_REMIND_AFTER_SECONDS": "30"})  # cancelled by the next line
        write_events(self.project, [{"event": "UserPromptSubmit"}])
        run(payload("The PR changes two files."), self.project, self.env)
        got = self.alerts(3.5)
        self.assertEqual(len(got), 1)
        self.assertIn("merge PR #44", got[0])

    def test_a_woken_stop_with_no_line_keeps_the_original_time(self):
        write_events(self.project, FINISHED_AGENT)
        env = dict(self.env, APB_REMIND_AFTER_SECONDS="3")
        start = time.time()
        run(payload(ONE), self.project, env)
        while time.time() < start + 1:
            time.sleep(0.1)
        # A watcher check-in wakes Claude; no prompt, no ask, the watcher still runs.
        write_events(self.project, [{"event": "PostToolUse", "command": "gh pr checks 44", "exit": 0}])
        tasks = [{"id": "w", "type": "shell", "status": "running", "command": "python3 apb-watch.py"}]
        r = run(payload("PR #44: checks still running.", background_tasks=tasks), self.project, env)
        self.assertFalse(sent_back(r))
        # The original wait fires about 3 s after the first stop; a restarted one would fire at 4 s.
        got = self.alerts(start + 3.6 - time.time())
        self.assertEqual(len(got), 1)
        self.assertIn("merge PR #44", got[0])
        self.assertEqual(len(self.alerts(2)), 1)

    def test_a_new_ask_ends_the_wait(self):
        write_events(self.project, FINISHED_AGENT)
        env = dict(self.env, APB_REMIND_AFTER_SECONDS="2")
        run(payload(ONE), self.project, env)
        write_events(self.project, [{"event": "PostToolUse", "command": "gh pr checks 40", "exit": 0}])
        tasks = [{"id": "w", "type": "shell", "status": "running"}]
        run(payload(NOTHING, background_tasks=tasks), self.project, env)
        self.assertEqual(self.alerts(3), [])

    def test_agent_activity_does_not_cancel_it(self):
        write_events(self.project, FINISHED_AGENT)
        run(payload(ONE), self.project, self.env)
        write_events(self.project, [{"event": "SubagentStart", "agent_id": "r9"}])
        self.assertEqual(len(self.alerts(3.5)), 1)

    def test_the_text_cannot_break_out_of_the_alert(self):
        write_events(self.project, FINISHED_AGENT)
        thing = 'say "hi" & `rm -rf x` $(boom) \\ it'
        run(payload("1 thing for you to do right now: %s." % thing), self.project, self.env)
        got = self.alerts(3.5)
        self.assertEqual(len(got), 1)
        self.assertIn("Reminder, your turn: " + thing, got[0])


class ReminderLifetime(unittest.TestCase):
    """The reminder is the one process apb lets outlive the script that starts it. It is given a
    deadline and ends there, whatever it is doing (scripts/test_apb_watch.py, TimeLimits)."""

    def setUp(self):
        self.project = apb_project()
        self.bin, self.calls = fake_osascript()

    def remind(self, deadline, wait="30", bin_folder=None):
        env = dict(os.environ, APB_REMIND_AFTER_SECONDS=wait,
                   PATH=(bin_folder or self.bin) + os.pathsep + os.environ["PATH"])
        start = time.time()
        r = subprocess.run([sys.executable, SCRIPT, "remind", self.project, "s1", "merge PR #44", "0",
                            "%.3f" % deadline], env=env, capture_output=True, timeout=15)
        return r, time.time() - start

    def test_a_long_wait_ends_at_the_deadline(self):
        r, took = self.remind(time.time() + 1.5)
        self.assertGreater(took, 1.0)
        self.assertLess(took, 4.0)
        self.assertFalse(os.path.exists(self.calls))  # ended without an alert

    def test_a_deadline_already_past_ends_at_once(self):
        r, took = self.remind(time.time() - 5)
        self.assertLess(took, 2.0)
        self.assertFalse(os.path.exists(self.calls))

    def test_a_stuck_alert_ends_at_the_deadline(self):
        folder = tempfile.mkdtemp()
        stuck = os.path.join(folder, "osascript")
        with open(stuck, "w") as f:
            f.write("#!/bin/sh\nexec sleep 29.4817\n")  # an odd length, to find it in ps
        os.chmod(stuck, os.stat(stuck).st_mode | stat.S_IEXEC)
        # Pass or fail, the fake alert does not outlive the test.
        self.addCleanup(subprocess.run, ["pkill", "-f", "sleep 29.4817"], capture_output=True, timeout=10)
        r, took = self.remind(time.time() + 2.5, wait="0.5", bin_folder=folder)
        self.assertGreater(took, 2.0)
        self.assertLess(took, 5.0)
        ps = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10)
        self.assertEqual([l for l in ps.stdout.splitlines() if "sleep 29.4817" in l], [])  # it ended too

    def test_the_hook_gives_the_reminder_a_deadline_after_its_wait(self):
        started = []

        class FakePopen:
            def __init__(self, args, **kwargs):
                started.append((args, kwargs))

        old_popen, old_env = stop.subprocess.Popen, os.environ.get("APB_REMIND_AFTER_SECONDS")
        stop.subprocess.Popen = FakePopen
        os.environ["APB_REMIND_AFTER_SECONDS"] = "7"
        try:
            now = time.time()
            stop.start_reminder(self.project, "s1", "merge PR #44", 123)
        finally:
            stop.subprocess.Popen = old_popen
            if old_env is None:
                os.environ.pop("APB_REMIND_AFTER_SECONDS")
            else:
                os.environ["APB_REMIND_AFTER_SECONDS"] = old_env
        self.assertEqual(len(started), 1)
        args, kwargs = started[0]
        self.assertEqual(args[2:7], ["remind", self.project, "s1", "merge PR #44", "123"])
        deadline = float(args[7])
        self.assertGreater(deadline, now + 7 + 10)  # room for the alert's own 10 s limit
        self.assertLess(deadline, now + 7 + stop.LIFETIME_MARGIN + 1)
        self.assertTrue(kwargs.get("start_new_session"))


def reminders_left():
    """Lines of `ps` for reminders this test file started (their project is a temporary folder)."""
    r = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=10)
    temp = os.path.realpath(tempfile.gettempdir())
    return [l for l in r.stdout.splitlines()
            if SCRIPT in l and " remind " in l and (temp in l or tempfile.gettempdir() in l)]


def tearDownModule():
    """No test leaves a reminder running: each ends by itself, at the latest at its deadline."""
    deadline = time.time() + 20
    while reminders_left() and time.time() < deadline:
        time.sleep(0.5)
    left = reminders_left()
    for line in left:
        try:
            os.kill(int(line.split()[0]), 9)
        except (OSError, ValueError):
            pass
    if left:
        raise AssertionError("reminders still running after the tests:\n" + "\n".join(left))


class Properties(unittest.TestCase):
    """Random session histories. A simulation tracks running agents, how each turn started and
    what happened in it; its answer must match the hook's, which only reads the events file."""

    # (thing, states a GitHub fact, names a decision, asks a decision on any line, holds a GitHub link).
    # A thing that names a decision asks it when it is the human's thing to do: after "1 thing for you
    # to do right now:" or as an item under "N things".
    THINGS = [("merge PR #4", False, False, False, False), ("answer #9", False, False, False, False),
              ("sign in to Cloudflare", False, False, False, False),
              ("decide the layout on #9", False, True, False, False),
              ("pick a host on https://github.com/o/r/issues/9", False, True, False, True),
              ("your call on https://github.com/o/r/pull/4", False, True, True, True),
              ("answer decision 3 on #9", False, True, True, False),
              ("merge PR #4, now that PR #3 is merged", True, False, False, False),
              ("merge PR #4 once PR #3 is merged", False, False, False, False),  # a plan, not a fact
              ("merge https://github.com/o/r/pull/4, which is open", True, False, False, True)]
    # (tail, states a GitHub fact, holds a GitHub link, asks a decision)
    TAILS = [("", False, False, False), ("3 of 5 cards are done.", True, False, False),
             ("3 of 5 checks passed.", False, False, False),
             ("Once PR #7 is merged, I'll rebase.", False, False, False),  # a plan, not a fact
             ("PR #7 is **merged**.", True, False, False),
             ("The plugin is at 0.3.18.", False, False, False), ("It took 1.5 seconds.", False, False, False),
             ("Card #9 is on the board.", True, False, False), ("Moved it on the board.", False, False, False),
             ("45 tests pass.", False, False, False), ("See https://github.com/o/r/pull/7.", False, True, False),
             ("https://github.com/o/r/pull/7 is merged.", True, True, False),
             ("Card: https://github.com/o/r/issues/7", False, True, False),
             ("Decision 8 was presented the same way.", False, False, False),
             ("Which do you want?", False, False, True), ("Noted.", False, False, False),
             ("The note said \"PR #7 is merged\".", False, False, False),  # a quoted fact isn't stated
             ("> https://github.com/o/r/pull/7 is merged.", False, False, False)]  # nor a quoted line or link

    LINK_TAILS = {"Card: https://github.com/o/r/issues/7"}

    def message(self, rng):
        """A random message and, known by construction: whether it ends with a valid ask block,
        whether it states a GitHub fact, and whether it asks a decision with no GitHub link."""
        text, valid, fact, asks, link, item_unlinked, nothing = self.body(rng)
        all_clear = text == ALL_CLEAR
        tail, tail_fact, tail_link, tail_asks = rng.choice(self.TAILS)
        if tail:
            # What happened goes above the ask (#153). Below it, the ask is no longer the last thing,
            # so the message has no valid ask block.
            if rng.random() < 0.75:
                text = tail + "\n\n" + text
            else:
                # A link line (with or without a short label) belongs to the ask, except under All
                # clear; any other tail ends the ask block.
                text = text + "\n\n" + tail
                valid = valid and tail in self.LINK_TAILS and not all_clear
                nothing = nothing and valid
        # A message whose ask block is "Nothing for you to do right now" asks nothing.
        asks = not nothing and (asks or tail_asks)
        return text, valid, fact or tail_fact, (asks and not (link or tail_link)) or item_unlinked

    def body(self, rng):
        """(text, a valid ask block, states a fact, asks a decision, holds a link, a numbered item under
        "N things" asks a decision with no link on its own line, starts with "Nothing for you")."""
        kind = rng.randrange(7)
        thing, fact, names, asks, link = rng.choice(self.THINGS)
        if kind == 0:
            return NOTHING, True, False, False, False, False, True
        if kind == 5:
            return ALL_CLEAR, True, False, False, False, False, False
        if kind == 1:
            return "1 thing for you to do right now: %s." % thing, True, fact, names, link, False, False
        n = rng.randint(2, 6)
        items = ["%d. %s." % (i, thing) for i in range(1, n + 1)]
        # In a list, the thing is an item: a decision there needs the link on the item's own line,
        # so a link in the tail doesn't help it.
        if kind == 2:
            return ("%d things for you to do right now:\n" % n + "\n".join(items), True, fact, asks, link,
                    names and not link, False)
        if kind == 3:
            return ("%d things for you to do right now:\n" % (n + 1) + "\n".join(items), False, fact, asks, link,
                    names and not link, False)
        if kind == 4:
            return "Progress: " + thing, False, fact, asks, link, False, False
        return "", False, False, False, False, False, False

    def test_random_histories(self):
        rng = random.Random(41)
        quiet_stops = 0  # quiet wake-ups the histories reached, so the exception is really tested
        quiet_after_helper = 0  # those whose turn starts with the helper's SubagentStop
        for history in range(40):
            project = apb_project()
            sim_running = set()
            next_id = [0]
            checks = 0
            prompts_logged = rng.random() < 0.9
            seen_prompt = False
            said_holder = False  # a stop that went through has said who holds the ball
            sends_tasks = rng.random() < 0.5
            for turn in range(rng.randint(3, 12)):
                events = []
                # Claude Code's own helper logs a SubagentStop with agent_type "" (and no SubagentStart)
                # just after a stop, so it lands at the start of the next turn. It is no agent's report.
                helper = turn > 0 and rng.random() < 0.4
                if helper:
                    next_id[0] += 1
                    events.append({"event": "SubagentStop", "agent_id": "h%d" % next_id[0], "agent_type": ""})
                # Background agents may finish before the turn; one finishing wakes the turn.
                woken = False
                for a in sorted(sim_running):
                    if rng.random() < 0.4:
                        events.append({"event": "SubagentStop", "agent_id": a})
                        sim_running.discard(a)
                        woken = True
                by_prompt = not woken and rng.random() < 0.8
                if by_prompt and prompts_logged:
                    events.append({"event": "UserPromptSubmit"})
                # A wake Claude Code sends itself is logged as an automatic prompt; it is no prompt
                # from the human, so the simulation treats it as a turn no prompt started.
                wake_prompt = not by_prompt and prompts_logged and rng.random() < 0.5
                if wake_prompt:
                    events.append({"event": "UserPromptSubmit", "automatic": True})
                stopped_now = woken
                background_bash = False
                checked = False
                for _ in range(rng.randint(0, 3)):
                    action = rng.randrange(7)
                    if action == 0:  # a foreground agent: starts and stops within the turn
                        next_id[0] += 1
                        a = "f%d" % next_id[0]
                        events += [{"event": "SubagentStart", "agent_id": a},
                                   {"event": "PostToolUse", "agent_id": a, "command": "x", "exit": 0},
                                   {"event": "SubagentStop", "agent_id": a}]
                        stopped_now = True
                    elif action == 1:  # a background agent: keeps running after the turn
                        next_id[0] += 1
                        a = "b%d" % next_id[0]
                        events.append({"event": "SubagentStart", "agent_id": a})
                        sim_running.add(a)
                    elif action == 2:
                        events.append({"event": "PostToolUse", "command": "watch", "background": True})
                        background_bash = True
                    elif action == 3:
                        events.append({"event": "PostToolUse", "command": "ls", "exit": 0})
                    elif action == 4:  # the orchestrator looks it up
                        events.append({"event": "PostToolUse", "exit": 0,
                                       "command": rng.choice(["gh pr view 4", "git -C /r log -1",
                                                              "python3 scripts/map-artifact.py m.html u",
                                                              "python3 scripts/progress-page.py p.html"])})
                        checked = True
                    elif action == 5:  # an agent's lookup is not the orchestrator's
                        next_id[0] += 1
                        a = "g%d" % next_id[0]
                        events += [{"event": "SubagentStart", "agent_id": a},
                                   {"event": "PostToolUse", "agent_id": a, "command": "gh pr list", "exit": 0},
                                   {"event": "SubagentStop", "agent_id": a}]
                        stopped_now = True
                    else:  # a lookup that failed checked nothing
                        events.append({"event": "PostToolUseFailure", "exit": 1,
                                       "command": rng.choice(["gh pr view 4",
                                                              "python3 scripts/progress-page.py p.html"])})
                write_events(project, events)
                if (by_prompt or wake_prompt) and prompts_logged:
                    seen_prompt = True  # any logged prompt shows that prompts are logged
                message, valid, fact, unlinked = self.message(rng)
                if not by_prompt and rng.random() < 0.5:  # a wake-up often ends quietly
                    message, valid, fact, unlinked = rng.choice(["", "Noted.", "  "]), False, False, False
                # A turn that no prompt started counts only once prompts are known to be logged.
                # The hook's work-ran-out check counts the helper's SubagentStop like any other.
                ran_out = (not sim_running and not background_bash
                           and (stopped_now or helper or (not by_prompt and seen_prompt)))
                hook_active = False
                extra = {}
                if sends_tasks:  # Claude Code lists what's in flight
                    extra["background_tasks"] = (
                        [{"id": a, "type": "subagent", "status": "running"} for a in sorted(sim_running)]
                        + ([{"id": "w", "type": "shell", "status": "running"}] if background_bash else []))
                # A watcher's wake-up (no prompt, no agent stopped) may end with at most one word, once
                # an earlier stop has said who holds the ball.
                woken_quietly = not stopped_now and not (by_prompt and prompts_logged) and said_holder
                while True:
                    quiet = woken_quietly and len(message.split()) <= 1
                    quiet_stops += quiet and ran_out and not valid and not hook_active
                    quiet_after_helper += quiet and helper and not valid and not hook_active
                    want_send_back = not hook_active and ((ran_out and not valid and not quiet)
                                                          or (fact and not checked) or unlinked)
                    r = stop.handle(payload(message, stop_hook_active=hook_active, **extra), project, time.time(),
                                    no_github, no_github)
                    checks += 1
                    self.assertEqual(r["send_back"], want_send_back, (history, turn, message, events))
                    if not r["send_back"]:
                        said_holder = said_holder or valid
                        break
                    hook_active = True  # Claude Code tries again; it may still have no line
                    message, valid, fact, unlinked = self.message(rng)
            self.assertGreater(checks, 0)
        self.assertGreater(quiet_stops, 0)
        self.assertGreater(quiet_after_helper, 0)


class HooksFile(unittest.TestCase):
    def setUp(self):
        with open(HOOKS_JSON) as f:
            self.hooks = json.load(f)["hooks"]

    def test_stop_runs_this_hook_and_waits_for_it(self):
        # The watcher starter also runs at a stop, in the background (test_apb_watch_start.py checks it).
        groups = [g for g in self.hooks["Stop"]
                  if not g["hooks"][0]["args"][0].endswith("/apb-watch-start.py")]
        self.assertEqual(len(groups), 1)
        hooks = groups[0]["hooks"]
        self.assertEqual(len(hooks), 1)
        hook = hooks[0]
        self.assertFalse(hook.get("async", False))
        self.assertLessEqual(hook["timeout"], 30)
        path = hook["args"][0].replace("${CLAUDE_PLUGIN_ROOT}", os.path.dirname(HERE))
        self.assertTrue(os.path.samefile(path, SCRIPT))


if __name__ == "__main__":
    unittest.main()
