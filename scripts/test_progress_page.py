"""Tests for progress-page.py. Run from the repo root: python3 scripts/test_progress_page.py

The pure parts (stages, board, agents, who holds the ball, who a PR waits on, the page) are checked
with hand-made inputs, and the agent list also with random event histories against a second,
simpler way of working out the answer.
"""
import importlib.util
import json
import os
import random
import re
import unittest

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("progress_page", os.path.join(HERE, "progress-page.py"))
pp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pp)

NOW = 1_800_000_000  # a fixed clock, in seconds since the epoch


def stamp(t):
    """An events-file time for t seconds since the epoch."""
    return pp.time.strftime("%Y-%m-%dT%H:%M:%S", pp.time.gmtime(t)) + ".000Z"


def ev(t, event, **more):
    line = {"time": stamp(t), "event": event, "session": more.pop("session", "s1")}
    line.update(more)
    line["t"] = int(t)
    return line


class Stages(unittest.TestCase):
    def test_reads_the_stages_line(self):
        text = "# x\n\n**Stages, in order:** PRD input → PRD (gate 1) → build → check the live product\n"
        self.assertEqual(pp.stages(text), ["PRD input", "PRD (gate 1)", "build", "check the live product"])

    def test_the_template_line(self):
        with open(os.path.join(HERE, "..", "templates", "MAP-template.md")) as f:
            found = pp.stages(f.read())
        self.assertIn("build", found)
        self.assertEqual(len(found), 7)

    def test_no_line_no_stages(self):
        self.assertEqual(pp.stages("# map\n\nNo stages here.\n"), [])


def project(items, options=("Todo", "In progress", "In review", "Done")):
    return {"title": "demo", "url": "https://github.com/users/o/projects/1",
            "field": {"options": [{"name": o} for o in options]},
            "items": {"nodes": [{"fieldValueByName": {"name": s} if s else None,
                                 "content": {"number": n, "title": "card %d" % n,
                                             "url": "https://github.com/o/r/issues/%d" % n}}
                                for n, s in items]}}


class Board(unittest.TestCase):
    def test_columns_in_board_order_cards_in_board_order(self):
        b = pp.board(project([(5, "Done"), (3, "Todo"), (9, "In progress"), (1, "Done"), (2, "Todo")]))
        self.assertEqual([c for c, _ in b["columns"]], ["Todo", "In progress", "In review", "Done"])
        cols = dict(b["columns"])
        self.assertEqual([c["number"] for c in cols["Todo"]], [3, 2])
        self.assertEqual([c["number"] for c in cols["Done"]], [5, 1])
        self.assertEqual(cols["In review"], [])

    def test_card_with_no_status_gets_its_own_column(self):
        b = pp.board(project([(1, None), (2, "Todo")]))
        self.assertEqual(b["columns"][-1][0], "No status")
        self.assertEqual([c["number"] for c in b["columns"][-1][1]], [1])

    def test_draft_item_without_content_is_skipped(self):
        p = project([(1, "Todo")])
        p["items"]["nodes"].append({"fieldValueByName": {"name": "Todo"}, "content": {}})
        self.assertEqual(sum(len(c) for _, c in pp.board(p)["columns"]), 1)

    def test_no_board(self):
        self.assertIsNone(pp.board(None))

    def test_done_count(self):
        b = pp.board(project([(1, "Done"), (2, "Todo"), (3, "Done"), (4, "In review")]))
        self.assertEqual(pp.done_count(b), (2, 4))
        self.assertEqual(pp.done_count(None), (0, 0))

    def test_random_boards_keep_every_card_once(self):
        rng = random.Random(7)
        names = ["Todo", "In progress", "In review", "Done", None]
        for _ in range(200):
            items = [(n, rng.choice(names)) for n in range(rng.randint(0, 30))]
            b = pp.board(project(items))
            got = [c["number"] for _, cards in b["columns"] for c in cards]
            self.assertEqual(sorted(got), [n for n, _ in items])
            done, total = pp.done_count(b)
            self.assertEqual((done, total), (sum(s == "Done" for _, s in items), len(items)))


class Agents(unittest.TestCase):
    def test_running_and_finished(self):
        events = [
            ev(NOW - 600, "SubagentStart", agent_id="a", agent_type="apb:builder"),
            ev(NOW - 500, "PostToolUse", agent_id="a", agent_type="apb:builder", command="npm test"),
            ev(NOW - 900, "SubagentStart", agent_id="b", agent_type="apb:reviewer"),
            ev(NOW - 300, "SubagentStop", agent_id="b", agent_type="apb:reviewer"),
        ]
        running, finished = pp.agent_runs(events, NOW)
        self.assertEqual([(r["id"], r["type"], r["start"], r["last"]) for r in running],
                         [("a", "apb:builder", NOW - 600, NOW - 500)])
        self.assertEqual(running[0]["what"], "npm test")
        self.assertEqual([(f["id"], f["type"], f["start"], f["stop"]) for f in finished],
                         [("b", "apb:reviewer", NOW - 900, NOW - 300)])

    def test_type_kept_when_later_lines_lack_it(self):
        events = [ev(NOW - 60, "SubagentStart", agent_id="a", agent_type="apb:builder"),
                  ev(NOW - 30, "SubagentStop", agent_id="a", agent_type="")]
        _, finished = pp.agent_runs(events, NOW)
        self.assertEqual(finished[0]["type"], "apb:builder")

    def test_untyped_agent_is_called_agent(self):
        running, _ = pp.agent_runs([ev(NOW - 60, "SubagentStart", agent_id="a", agent_type="")], NOW)
        self.assertEqual(running[0]["type"], "agent")

    def test_silent_for_hours_is_not_running(self):
        running, _ = pp.agent_runs([ev(NOW - pp.ABANDONED - 1, "SubagentStart", agent_id="a")], NOW)
        self.assertEqual(running, [])

    def test_main_session_lines_are_not_agents(self):
        running, finished = pp.agent_runs([ev(NOW - 10, "PostToolUse", command="ls")], NOW)
        self.assertEqual((running, finished), ([], []))

    def test_a_lone_stop_line_is_not_listed(self):
        # Claude Code's own short helpers write only a stop line, with no type.
        running, finished = pp.agent_runs([ev(NOW - 10, "SubagentStop", agent_id="h", agent_type="")], NOW)
        self.assertEqual((running, finished), ([], []))

    def test_finished_newest_first(self):
        events = [ev(NOW - 100, "SubagentStart", agent_id="old"), ev(NOW - 90, "SubagentStop", agent_id="old"),
                  ev(NOW - 50, "SubagentStart", agent_id="new"), ev(NOW - 40, "SubagentStop", agent_id="new")]
        _, finished = pp.agent_runs(events, NOW)
        self.assertEqual([f["id"] for f in finished], ["new", "old"])

    def test_random_histories_against_a_second_way(self):
        rng = random.Random(20)
        for _ in range(300):
            events, plan = [], {}
            for i in range(rng.randint(0, 6)):
                a = "ag%d" % i
                t = NOW - rng.randint(0, 6 * 3600)
                pieces = []  # (start, stop or None): the orchestrator can resume an agent after it stops
                for p in range(rng.randint(1, 3)):
                    lines = [ev(t, "SubagentStart", agent_id=a, agent_type="apb:builder" if p == 0 else "")]
                    piece_start = t
                    for _ in range(rng.randint(0, 3)):
                        t += rng.randint(1, 1200)
                        lines.append(ev(t, "PostToolUse", agent_id=a, command="c%d" % t))
                    stop = None
                    if rng.random() < 0.6:
                        t += rng.randint(1, 600)
                        stop = t
                        lines.append(ev(t, "SubagentStop", agent_id=a))
                    events += lines
                    pieces.append((piece_start, stop, lines[-1]["t"]))
                    if stop is None:
                        break
                    t += rng.randint(1, 3600)
                plan[a] = pieces
            if rng.random() < 0.3:
                events.append(ev(NOW - 5, "SubagentStop", agent_id="helper", agent_type=""))
            events.sort(key=lambda e: e["t"])
            running, finished = pp.agent_runs(events, NOW)
            # Second way, from the plan: one row per agent. It runs if its last piece has no stop and its
            # last line is recent; it finished if its last piece stopped, and took the sum of its pieces.
            want_running = sorted(a for a, ps in plan.items() if ps[-1][1] is None and NOW - ps[-1][2] <= pp.ABANDONED)
            want_finished = {a: (ps[0][0], ps[-1][1], sum(s - b for b, s, _ in ps))
                             for a, ps in plan.items() if ps[-1][1] is not None}
            self.assertEqual(sorted(r["id"] for r in running), want_running)
            for r in running:
                self.assertEqual(r["start"], plan[r["id"]][0][0])
                self.assertEqual(r["piece"], plan[r["id"]][-1][0])
                self.assertEqual(r["type"], "apb:builder")
            self.assertEqual({f["id"]: (f["start"], f["stop"], f["took"]) for f in finished}, want_finished)

    def test_a_resumed_agent_is_one_row(self):
        events = [ev(NOW - 900, "SubagentStart", agent_id="a", agent_type="apb:builder"),
                  ev(NOW - 800, "SubagentStop", agent_id="a"),
                  ev(NOW - 500, "SubagentStart", agent_id="a", agent_type="apb:builder"),
                  ev(NOW - 300, "SubagentStop", agent_id="a")]
        running, finished = pp.agent_runs(events, NOW)
        self.assertEqual(running, [])
        self.assertEqual([(f["start"], f["stop"], f["took"]) for f in finished], [(NOW - 900, NOW - 300, 300)])
        running, _ = pp.agent_runs(events + [ev(NOW - 100, "SubagentStart", agent_id="a")], NOW)
        self.assertEqual([(r["start"], r["piece"]) for r in running], [(NOW - 900, NOW - 100)])


class Ball(unittest.TestCase):
    def test_human_holds_it(self):
        events = [ev(NOW - 100, "UserPromptSubmit"),
                  ev(NOW - 50, "Stop", holder="human", todo="merge PR #49")]
        self.assertEqual(pp.ball(events), {"state": "human", "todo": "merge PR #49", "since": NOW - 50})

    def test_claude_holds_it(self):
        events = [ev(NOW - 50, "Stop", holder="claude")]
        self.assertEqual(pp.ball(events)["state"], "claude")

    def test_a_new_prompt_means_claude_is_working(self):
        events = [ev(NOW - 50, "Stop", holder="human", todo="say go"), ev(NOW - 20, "UserPromptSubmit")]
        self.assertEqual(pp.ball(events), {"state": "working", "todo": None, "since": NOW - 20})

    def test_an_answer_with_no_first_line_keeps_the_holder(self):
        events = [ev(NOW - 90, "Stop", holder="human", todo="merge"), ev(NOW - 60, "UserPromptSubmit"),
                  ev(NOW - 30, "Stop")]
        self.assertEqual(pp.ball(events), {"state": "human", "todo": "merge", "since": NOW - 90})

    def test_go_then_an_agent_means_claude_is_working(self):
        # The human said "go", Claude started a builder and ended its turn without a first line.
        events = [ev(NOW - 90, "Stop", holder="human", todo="say go"), ev(NOW - 60, "UserPromptSubmit"),
                  ev(NOW - 50, "SubagentStart", agent_id="a"), ev(NOW - 40, "Stop")]
        running, _ = pp.agent_runs(events, NOW)
        self.assertEqual(pp.ball(events, running), {"state": "working", "todo": None, "since": NOW - 60})

    def test_an_agent_from_before_the_answer_does_not_count(self):
        events = [ev(NOW - 99, "SubagentStart", agent_id="a"), ev(NOW - 90, "Stop", holder="human", todo="merge"),
                  ev(NOW - 60, "UserPromptSubmit"), ev(NOW - 40, "Stop")]
        running, _ = pp.agent_runs(events, NOW)
        self.assertEqual(pp.ball(events, running)["state"], "human")

    def test_agent_lines_do_not_open_a_turn(self):
        events = [ev(NOW - 50, "Stop", holder="claude"), ev(NOW - 20, "PostToolUse", agent_id="a", command="x")]
        self.assertEqual(pp.ball(events)["state"], "claude")

    def test_latest_session_counts(self):
        events = [ev(NOW - 90, "Stop", holder="human", todo="old", session="old"),
                  ev(NOW - 30, "Stop", holder="claude", session="new")]
        self.assertEqual(pp.ball(events)["state"], "claude")

    def test_nothing_known(self):
        self.assertEqual(pp.ball([])["state"], None)


def pr(checks="SUCCESS", draft=False, bodies=(), threads=()):
    return {"number": 7, "title": "t", "url": "u", "isDraft": draft,
            "commits": {"nodes": [{"commit": {"statusCheckRollup": {"state": checks} if checks else None}}]},
            "comments": {"nodes": [{"body": b} for b in bodies]}, "reviews": {"nodes": []},
            "reviewThreads": {"nodes": [{"isResolved": r, "comments": {"nodes": [{"body": "x\n— Claude (reviewer)"}]}}
                                        for r in threads]}}


REVIEWED = ["Review\n\n— Claude (reviewer)\n"]


class WaitingOn(unittest.TestCase):
    def test_each_case(self):
        self.assertEqual(pp.waiting_on(pr(draft=True))[0], "builder")
        self.assertEqual(pp.waiting_on(pr(checks="PENDING"))[0], "CI")
        self.assertEqual(pp.waiting_on(pr(checks="EXPECTED"))[0], "CI")
        self.assertEqual(pp.waiting_on(pr(checks="FAILURE", bodies=REVIEWED))[0], "builder")
        self.assertEqual(pp.waiting_on(pr(checks="SUCCESS"))[0], "reviewer")
        self.assertEqual(pp.waiting_on(pr(checks=None))[0], "reviewer")
        self.assertEqual(pp.waiting_on(pr(threads=[True, False, False])), ("you", "review posted, 2 decisions open"))
        self.assertEqual(pp.waiting_on(pr(threads=[False])), ("you", "review posted, 1 decision open"))

    def test_fix_items_only_never_say_ready_to_merge(self):
        # Fix and Note items go in one post with no threads, so the page can't tell if the fixes are made.
        fix_post = ["## Fix items (1–3)\n\n1. Rename x.\n\n— Claude (reviewer)\n"]
        self.assertEqual(pp.waiting_on(pr(bodies=fix_post)), ("you", "review posted"))
        self.assertEqual(pp.waiting_on(pr(bodies=fix_post, threads=[True])), ("you", "review posted"))
        for threads in ([], [True], [False], [True, False]):
            self.assertNotIn("merge", pp.waiting_on(pr(bodies=REVIEWED, threads=threads))[1])

    def test_review_found_in_a_review_body(self):
        p = pr()
        p["reviews"]["nodes"].append({"body": "All good.\n\n— Claude (reviewer)"})
        self.assertEqual(pp.waiting_on(p)[0], "you")

    def test_builder_reply_is_not_a_review(self):
        self.assertEqual(pp.waiting_on(pr(bodies=["Fixed.\n\n— Claude (builder)"]))[0], "reviewer")

    def test_checks_state(self):
        self.assertEqual(pp.checks(pr(checks=None)), "none")
        self.assertEqual(pp.checks(pr(checks="SUCCESS")), "passed")
        p = pr()
        p["commits"]["nodes"] = []
        self.assertEqual(pp.checks(p), "none")


def data(**more):
    d = {"repo": "o/r", "now": NOW, "ball": {"state": "human", "todo": "merge PR #7", "since": NOW - 120},
         "stages": ["PRD", "build", "check the live product"], "build_count": (3, 10),
         "board": pp.board(project([(1, "Todo"), (2, "In progress"), (3, "Done")])),
         "prs": [{"number": 7, "title": "A PR", "url": "https://github.com/o/r/pull/7", "checks": "passed",
                  "who": "you", "why": "review posted"}],
         "running": [{"id": "a1", "type": "apb:builder", "start": NOW - 1800, "piece": NOW - 1800, "last": NOW - 60,
                      "what": "npm test"}],
         "finished": [{"id": "b1", "type": "apb:reviewer", "start": NOW - 4000, "stop": NOW - 3000, "took": 1000}],
         "events": ".apb/events.jsonl"}
    d.update(more)
    return d


class Page(unittest.TestCase):
    def test_everything_shows(self):
        page = pp.render(data())
        self.assertIn("<title>r progress</title>", page)
        self.assertIn("Your turn: merge PR #7", page)
        self.assertIn("build (3 of 10 cards done)", page)
        # Two counts, worded apart: build cards in the stages, every board item under the stages.
        self.assertIn("1 of 3 board items in Done", page)
        self.assertEqual(page.count("cards done"), 1)
        for text in ["apb:builder", "<td>30 min</td>", "npm test", "1 min ago", "apb:reviewer", "took 16 min",
                     'href="https://github.com/o/r/pull/7"', "review posted", "card 1", "card 2", "card 3"]:
            self.assertIn(text, page)

    def test_running_column_header(self):
        page = pp.render(data())
        self.assertIn("<th>Running for</th>", page)
        self.assertNotIn("<th>Started</th><th>Last event</th>", page)

    def test_finished_time_is_the_sum_of_its_pieces(self):
        # Started 4000 s ago, stopped 3000 s ago, but resumed in between: the row shows the work time.
        page = pp.render(data(finished=[{"id": "b1", "type": "apb:reviewer", "start": NOW - 4000,
                                         "stop": NOW - 3000, "took": 300}]))
        self.assertIn("took 5 min", page)

    def test_stages_message_names_the_map_file(self):
        page = pp.render(data(stages=[], map="other/MAP.md"))
        self.assertIn('No "Stages, in order" line in other/MAP.md.', page)
        page = pp.render(data(stages=[], map="gone/MAP.md", map_found=False))
        self.assertIn("gone/MAP.md not found", page)
        self.assertNotIn("Stages, in order", page)

    def test_every_card_once(self):
        page = pp.render(data())
        for n in (1, 2, 3):
            self.assertEqual(page.count('href="https://github.com/o/r/issues/%d"' % n), 1)

    def test_text_from_github_and_events_is_escaped(self):
        bad = "<script>alert(1)</script>"
        page = pp.render(data(ball={"state": "human", "todo": bad, "since": NOW},
                              prs=[{"number": 1, "title": bad, "url": "u", "checks": "none", "who": "CI", "why": bad}],
                              running=[{"id": bad, "type": bad, "start": NOW, "last": NOW, "what": bad}]))
        self.assertNotIn(bad, page)
        self.assertIn("&lt;script&gt;", page)

    def test_empty_states(self):
        page = pp.render(data(ball={"state": None, "todo": None, "since": None}, stages=[], board=None, prs=[],
                              running=[], finished=[], build_count=(0, 0)))
        self.assertIn("No agents running", page)
        self.assertIn("No open PRs", page)
        self.assertIn("No board", page)
        self.assertNotIn("cards done", page)

    def test_no_build_count_when_no_build_cards(self):
        page = pp.render(data(build_count=(0, 0)))
        self.assertIn(">build<", page)
        self.assertNotIn("build (", page)

    def test_ball_states(self):
        self.assertIn("Nothing for you to do right now",
                      pp.render(data(ball={"state": "claude", "todo": None, "since": NOW})))
        self.assertIn("Claude is working",
                      pp.render(data(ball={"state": "working", "todo": None, "since": NOW})))

    def test_page_works_in_dark_mode_and_on_a_phone(self):
        page = pp.render(data())
        self.assertIn("prefers-color-scheme: dark", page)
        self.assertIn(':root[data-theme="dark"]', page)
        self.assertIn("max-width", page)


class BuildCountRule(unittest.TestCase):
    """The count goes where the map artifact puts it: after the first word "build" in the stages."""

    def same_as_the_map(self, names, done=3, total=10):
        line = "<p><strong>Stages, in order:</strong> %s</p>" % " → ".join(names)
        want = re.sub(r"<[^>]+>", "", pp.mp.add_build_count(line, done, total)).replace("Stages, in order: ", "")
        got = re.findall(r"<li>(.*?)</li>", pp.stages_html(names, (done, total)))
        self.assertEqual(" → ".join(got), want)

    def test_build_and_test_gets_the_count(self):
        self.assertIn("<li>build (3 of 10 cards done) and test</li>", pp.stages_html(["PRD", "build and test"], (3, 10)))
        self.same_as_the_map(["PRD", "build and test", "ship"])

    def test_same_rule_as_the_map(self):
        for names in (["build"], ["PRD", "Build", "check"], ["rebuild docs", "build"], ["build", "build again"],
                      ["PRD (gate 1)", "split the TDD into cards", "build", "review and merge (gate 3)"]):
            self.same_as_the_map(names)

    def test_no_build_stage_no_count(self):
        self.assertNotIn("cards done", pp.stages_html(["PRD", "ship"], (3, 10)))


class Durations(unittest.TestCase):
    def test_short_and_long(self):
        self.assertEqual(pp.span(30), "under 1 min")
        self.assertEqual(pp.span(61), "1 min")
        self.assertEqual(pp.span(3 * 3600 + 5 * 60), "3 h 5 min")


class TimeLimits(unittest.TestCase):
    """Every command the script starts has a time limit, so a stuck gh can't stall it."""

    def test_every_gh_call_has_a_time_limit(self):
        calls = []
        real = pp.subprocess.run

        class Done:
            returncode = 0

            def __init__(self, out):
                self.stdout = out

        def fake(args, **kwargs):
            calls.append(kwargs)
            if args[:3] == ["gh", "api", "graphql"]:
                return Done(json.dumps({"data": {"repository": {"projectsV2": {"nodes": []},
                                                                "pullRequests": {"nodes": []}}}}))
            return Done("[]")

        pp.subprocess.run = fake
        self.addCleanup(setattr, pp.subprocess, "run", real)
        pp.github("o/r")
        self.assertTrue(calls)
        for kwargs in calls:
            self.assertGreater(kwargs.get("timeout") or 0, 0)


class NoCopiedCode(unittest.TestCase):
    def test_reuses_the_map_script(self):
        # The page's look and the build count come from map-artifact.py, so the two pages can't drift.
        with open(os.path.join(HERE, "progress-page.py")) as f:
            source = f.read()
        self.assertIn("map-artifact.py", source)
        self.assertIsNone(re.search(r"def build_count", source))


if __name__ == "__main__":
    unittest.main()
