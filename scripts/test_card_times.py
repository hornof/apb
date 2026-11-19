"""Tests for card-times.py. Run from the repo root: python3 -m unittest discover -s scripts -t scripts

Hand-made timelines check each of the three times and the message count, one rule at a time. Random
timelines are then checked against a second, simpler way of working the times out (oracle below).
GitHub is never called: the gh calls are replaced with canned answers.
"""
import calendar
import importlib.util
import json
import os
import random
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("card_times", os.path.join(HERE, "card-times.py"))
ct = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ct)

SIGN = "\n\n— Claude (reviewer)"
S = "orchestrator-session"


def at(minute, second=0):
    """An events-file time, minute minutes after 12:00 UTC."""
    t = calendar.timegm((2026, 11, 18, 12, 0, 0)) + minute * 60 + second
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(t)) + ".500Z"


def gh_at(minute):
    """A GitHub time, minute minutes after 12:00 UTC."""
    t = calendar.timegm((2026, 11, 18, 12, 0, 0)) + minute * 60
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def start(minute, agent, kind="apb:builder"):
    return {"time": at(minute), "event": "SubagentStart", "session": S, "agent_id": agent, "agent_type": kind}


def bash(minute, agent, command, kind="apb:builder"):
    return {"time": at(minute), "event": "PostToolUse", "session": S, "agent_id": agent, "agent_type": kind,
            "command": command, "exit": 0}


def stop(minute, holder=None, todo=None, session=S, **extra):
    line = {"time": at(minute), "event": "Stop", "session": session}
    if holder:
        line["holder"] = holder
    if todo:
        line["todo"] = todo
    line.update(extra)
    return line


def prompt(minute, routine=None, automatic=False, session=S):
    line = {"time": at(minute), "event": "UserPromptSubmit", "session": session}
    if automatic:
        line["automatic"] = True
    elif routine is not None:
        line["routine"] = routine
    return line


def pr(number=58, opened=10, merged=30, closed=None, branch="issue-54-brief-check", comments=()):
    return {"number": number, "createdAt": gh_at(opened), "mergedAt": gh_at(merged) if merged is not None else None,
            "closedAt": gh_at(closed if closed is not None else merged) if (closed is not None or merged is not None)
            else None, "headRefName": branch, "comments": list(comments)}


def issue(prs, number=54, state="CLOSED", comments=()):
    return {"number": number, "state": state, "comments": list(comments), "prs": prs}


def builder_54():
    return [start(2, "b1"), bash(3, "b1", "gh issue view 54 --repo hornof/apb"),
            bash(9, "b1", "git -C /wt/issue-54 push -u origin issue-54-brief-check")]


class Example(unittest.TestCase):
    """Card #54 as it happened: builder 22:23:07, PR 22:29:32, hand-off 22:34:09, merge 22:47:52."""

    def test_the_three_times(self):
        events = [
            {"time": "2026-11-18T22:19:35.437Z", "event": "Stop", "session": S, "holder": "human",
             "todo": "say \"go\" for the next card, #54"},
            {"time": "2026-11-18T22:23:07.684Z", "event": "SubagentStart", "session": S,
             "agent_id": "ab0", "agent_type": "apb:builder"},
            {"time": "2026-11-18T22:29:16.420Z", "event": "PostToolUse", "session": S, "agent_id": "ab0",
             "agent_type": "apb:builder", "command": "git -C /wt push -u origin issue-54-brief-check", "exit": 0},
            {"time": "2026-11-18T22:34:09.664Z", "event": "Stop", "session": S, "holder": "human",
             "todo": "answer [decision 9 on PR #58 (Card #54)](https://github.com/hornof/apb/pull/58)"},
            {"time": "2026-11-18T22:40:05.152Z", "event": "Stop", "session": S, "holder": "human",
             "todo": "merge [PR #58 (Card #54)](https://github.com/hornof/apb/pull/58)"},
        ]
        data = issue([{"number": 58, "createdAt": "2026-11-18T22:29:32Z", "mergedAt": "2026-11-18T22:47:52Z",
                       "closedAt": "2026-11-18T22:47:53Z", "headRefName": "issue-54-brief-check",
                       "comments": []}])
        r = ct.card_times(events, data)
        self.assertEqual(r["pr"], 58)
        self.assertAlmostEqual(r["build_min"], 6.4, places=1)
        self.assertAlmostEqual(r["review_min"], 4.6, places=1)
        self.assertAlmostEqual(r["human_min"], 13.7, places=1)
        self.assertEqual(r["last_action"], "merge")


class Build(unittest.TestCase):
    def test_the_builder_that_pushed_the_branch_is_the_one(self):
        events = [start(1, "other"), bash(2, "other", "git push -u origin issue-53-decisions")] + builder_54()
        r = ct.card_times(events, issue([pr()]))
        self.assertEqual(r["build_min"], 8.0)  # minute 2 to minute 10

    def test_a_resumed_builder_keeps_its_first_start(self):
        events = builder_54() + [start(11, "b1"), start(12, "b1")]
        self.assertEqual(ct.card_times(events, issue([pr()]))["build_min"], 8.0)

    def test_the_issue_number_is_used_when_no_builder_names_the_branch(self):
        events = [start(4, "b1"), bash(5, "b1", "gh issue view 54 --repo hornof/apb")]
        self.assertEqual(ct.card_times(events, issue([pr()]))["build_min"], 6.0)

    def test_the_branch_beats_the_issue_number(self):
        events = [start(1, "early"), bash(2, "early", "gh issue view 54"),
                  start(4, "b1"), bash(5, "b1", "git push origin issue-54-brief-check")]
        self.assertEqual(ct.card_times(events, issue([pr()]))["build_min"], 6.0)

    def test_issue_number_forms(self):
        for command in ["gh issue view 54", "git -C /wt/issue-54 status", "commit -m 'x (#54)'",
                        "gh api repos/o/r/issues/54/comments", "title (Card #54)"]:
            events = [start(4, "b1"), bash(5, "b1", command)]
            self.assertEqual(ct.card_times(events, issue([pr()]))["build_min"], 6.0, command)
        for command in ["gh issue view 540", "git -C /wt/issue-541 status", "#5", "port 5454"]:
            events = [start(4, "b1"), bash(5, "b1", command)]
            self.assertIsNone(ct.card_times(events, issue([pr()]))["build_min"], command)

    def test_a_longer_branch_name_is_not_the_branch(self):
        events = [start(4, "b1"), bash(5, "b1", "git push origin fix-times-2")]
        self.assertIsNone(ct.card_times(events, issue([pr(branch="fix-times")]))["build_min"])
        events = [start(4, "b1"), bash(5, "b1", "git push origin fix-times")]
        self.assertEqual(ct.card_times(events, issue([pr(branch="fix-times")]))["build_min"], 6.0)

    def test_only_builders_count(self):
        events = [start(4, "r1", "apb:reviewer"), bash(5, "r1", "gh pr checkout issue-54-brief-check",
                                                       "apb:reviewer")]
        self.assertIsNone(ct.card_times(events, issue([pr()]))["build_min"])

    def test_a_builder_started_after_the_pr_opened_is_not_the_start(self):
        events = [start(12, "fixer"), bash(13, "fixer", "git push origin issue-54-brief-check")]
        self.assertIsNone(ct.card_times(events, issue([pr()]))["build_min"])

    def test_two_builders_on_the_card_take_the_earlier(self):
        events = builder_54() + [start(6, "b2"), bash(7, "b2", "git push origin issue-54-brief-check")]
        self.assertEqual(ct.card_times(events, issue([pr()]))["build_min"], 8.0)


class Review(unittest.TestCase):
    def test_first_human_stop_after_the_pr_opened(self):
        events = builder_54() + [stop(8, "human", "say go for #54"), stop(11, "claude"),
                                 stop(12, "human", "answer decision 3 on PR #58"), stop(15, "human", "merge PR #58")]
        r = ct.card_times(events, issue([pr()]))
        self.assertEqual(r["review_min"], 2.0)
        self.assertEqual(r["human_min"], 18.0)

    def test_a_stop_naming_the_pr_beats_an_earlier_one_that_does_not(self):
        events = builder_54() + [stop(12, "human", "merge PR #57"), stop(14, "human", "merge [PR #58](x)")]
        self.assertEqual(ct.card_times(events, issue([pr()]))["review_min"], 4.0)

    def test_the_card_number_counts_as_naming_it(self):
        events = builder_54() + [stop(12, "human", "merge PR #57"), stop(13, "human", "look at Card #54")]
        self.assertEqual(ct.card_times(events, issue([pr()]))["review_min"], 3.0)

    def test_a_link_to_the_pr_counts_as_naming_it(self):
        events = builder_54() + [stop(12, "human", "x"), stop(13, "human", "see github.com/o/r/pull/58")]
        self.assertEqual(ct.card_times(events, issue([pr()]))["review_min"], 3.0)

    def test_a_stop_after_the_last_action_is_not_the_hand_off(self):
        events = builder_54() + [stop(31, "human", "merge PR #58")]
        r = ct.card_times(events, issue([pr()]))
        self.assertIsNone(r["review_min"])
        self.assertIsNone(r["human_min"])

    def test_sent_back_stops_are_not_hand_offs(self):
        events = builder_54() + [stop(12, sent_back=True), stop(14, "human", "merge PR #58")]
        self.assertEqual(ct.card_times(events, issue([pr()]))["review_min"], 4.0)

    def test_no_hand_off_leaves_review_and_human_unknown(self):
        r = ct.card_times(builder_54(), issue([pr()]))
        self.assertEqual(r["build_min"], 8.0)
        self.assertIsNone(r["review_min"])
        self.assertIsNone(r["human_min"])
        self.assertIsNone(r["messages"])


class LastAction(unittest.TestCase):
    def test_closed_without_a_merge(self):
        events = builder_54() + [stop(12, "human", "PR #58")]
        r = ct.card_times(events, issue([pr(merged=None, closed=20)]))
        self.assertEqual((r["human_min"], r["last_action"]), (8.0, "close"))

    def test_still_open_uses_the_last_human_comment(self):
        events = builder_54() + [stop(12, "human", "PR #58")]
        comments = [{"body": "looks off", "createdAt": gh_at(16)}, {"body": "fixed" + SIGN, "createdAt": gh_at(18)}]
        r = ct.card_times(events, issue([pr(merged=None, comments=comments)]))
        self.assertEqual((r["human_min"], r["last_action"]), (4.0, "comment"))

    def test_the_merged_pr_is_picked_among_several(self):
        prs = [pr(number=60, opened=1, merged=None, closed=5), pr(number=58), pr(number=61, opened=40, merged=None)]
        self.assertEqual(ct.card_times(builder_54(), issue(prs))["pr"], 58)

    def test_no_pr_at_all(self):
        r = ct.card_times(builder_54(), issue([]))
        self.assertIsNone(r["pr"])
        self.assertIsNone(r["build_min"])


class Routine(unittest.TestCase):
    YES = ["go", "Go.", "GO!", "resolved", "Resolved.", "merged", "**merged**", "a", "B", "(c)", "b.",
           "9a", "9 a", "9: a", "Decision 9: a", "decision 9 (b)", "1a, 2b", "1a and 2b", "1 a 2 b", " go \n"]
    NO = ["ok", "go ahead and fix item 3", "why is this red?", "a and also change the title", "yes",
          "merged, but the preview is broken", "abc", "", "  ", "resolved all but 4", "<b>"]

    def test_routine_replies(self):
        for text in self.YES:
            self.assertTrue(ct.routine(text), text)

    def test_other_replies(self):
        for text in self.NO:
            self.assertFalse(ct.routine(text), text)

    def test_it_is_the_hooks_own_rule(self):
        self.assertIs(ct.routine, ct.events_hook.routine)


class Messages(unittest.TestCase):
    def setUp(self):
        self.base = builder_54() + [stop(12, "human", "answer decision 3 on PR #58")]

    def test_only_other_messages_from_the_human_count(self):
        events = self.base + [prompt(11, routine=False),  # before the hand-off
                              prompt(13, routine=False), prompt(14, routine=True), prompt(15, automatic=True),
                              prompt(16, routine=False, session="another"), prompt(17, routine=False),
                              prompt(31, routine=False)]  # after the merge
        r = ct.card_times(events, issue([pr()]))
        self.assertEqual((r["messages"], r["chat"], r["pr_messages"]), (2, 2, 0))

    def test_pr_comments_from_the_human_count_but_not_claudes(self):
        comments = [{"body": "b", "createdAt": gh_at(13)}, {"body": "why this name?", "createdAt": gh_at(14)},
                    {"body": "Fixed in abc." + SIGN, "createdAt": gh_at(15)},
                    {"body": "and this?", "createdAt": gh_at(11)}]  # before the hand-off
        r = ct.card_times(self.base + [prompt(20, routine=False)], issue([pr(comments=comments)]))
        self.assertEqual((r["messages"], r["chat"], r["pr_messages"]), (2, 1, 1))

    def test_a_reply_quoting_claude_is_the_humans(self):
        quote = "> 3. Rename the button.\n>\n> — Claude (reviewer)\n\nNo, keep the name, it matches the docs."
        comments = [{"body": quote, "createdAt": gh_at(13)},
                    {"body": "> why?\n\nBecause the docs say so." + SIGN, "createdAt": gh_at(14)}]
        r = ct.card_times(self.base, issue([pr(comments=comments)]))
        self.assertEqual((r["messages"], r["pr_messages"]), (1, 1))
        self.assertFalse(ct.is_claude(quote))
        self.assertTrue(ct.is_claude("Fixed." + SIGN))

    def test_unsigned_comments_from_claudes_account_do_not_count(self):
        comments = [{"body": "why this name?", "author": "hornof", "createdAt": gh_at(13)},
                    {"body": "Fixed in abc.", "author": "hornof-claude", "createdAt": gh_at(14)},
                    {"body": "Research notes.\n\n— Claude (advisor)", "author": "hornof", "createdAt": gh_at(15)}]
        r = ct.card_times(self.base, issue([pr(comments=comments)]), "hornof-claude")
        self.assertEqual(r["pr_messages"], 1)
        self.assertEqual(ct.card_times(self.base, issue([pr(comments=comments)]))["pr_messages"], 2)

    def test_the_last_human_comment_ignores_claudes_account(self):
        events = builder_54() + [stop(12, "human", "PR #58")]
        comments = [{"body": "looks off", "author": "hornof", "createdAt": gh_at(16)},
                    {"body": "Fixed.", "author": "hornof-claude", "createdAt": gh_at(18)}]
        r = ct.card_times(events, issue([pr(merged=None, comments=comments)]), "hornof-claude")
        self.assertEqual((r["human_min"], r["last_action"]), (4.0, "comment"))

    def test_random_comments_by_author(self):
        """Claude's comments carry no signature here; only the author login marks them."""
        rng = random.Random(64)
        texts = ["why?", "b", "go", "rename it", "Fixed.", "merged", "looks off"]
        for i in range(300):
            comments, want = [], 0
            for _ in range(rng.randint(0, 8)):
                minute, author, body = rng.randint(5, 35), rng.choice(["hornof", "hornof-claude"]), rng.choice(texts)
                comments.append({"body": body, "author": author, "createdAt": gh_at(minute)})
                if author == "hornof" and not ct.routine(body) and 12 < minute <= 30:
                    want += 1
            with self.subTest(i=i, comments=comments):
                r = ct.card_times(self.base, issue([pr(comments=comments)]), "hornof-claude")
                self.assertEqual(r["pr_messages"], want)

    def test_github_authors_reach_the_comments(self):
        node = {"number": 58, "createdAt": gh_at(10), "mergedAt": None, "closedAt": None, "headRefName": "b",
                "comments": {"nodes": [{"body": "x", "createdAt": gh_at(13), "author": {"login": "hornof-claude"}}]},
                "reviews": {"nodes": [{"body": "y", "submittedAt": gh_at(14), "author": None}]},
                "reviewThreads": {"nodes": [{"comments": {"nodes": [
                    {"body": "z", "createdAt": gh_at(15), "author": {"login": "hornof"}}]}}]}}
        got = {"data": {"repository": {"issue": {"number": 54, "state": "OPEN", "comments": {"nodes": []},
                                                 "closedByPullRequestsReferences": {"nodes": [node]}}}}}
        authors = [c["author"] for c in ct.flatten(got)["prs"][0]["comments"]]
        self.assertEqual(authors, ["hornof-claude", None, "hornof"])
        self.assertEqual(ct.QUERY.count("author { login }"), 3)

    def test_a_message_logged_without_its_type_makes_the_count_unknown(self):
        events = self.base + [prompt(13, routine=False), {"time": at(14), "event": "UserPromptSubmit", "session": S}]
        r = ct.card_times(events, issue([pr()]))
        self.assertIsNone(r["messages"])
        self.assertEqual(r["unclassified"], 1)

    def test_no_messages_is_zero(self):
        self.assertEqual(ct.card_times(self.base, issue([pr()]))["messages"], 0)


class Comment(unittest.TestCase):
    def result(self, **over):
        events = builder_54() + [stop(12, "human", "PR #58"), prompt(13, routine=False)]
        r = ct.card_times(events, issue([pr()]))
        r.update(over)
        return r

    def test_the_data_line_holds_the_numbers(self):
        r = self.result()
        text = ct.comment(r)
        data = ct.data_of(text)
        self.assertEqual(data, {"issue": 54, "pr": 58, "build_min": 8.0, "review_min": 2.0, "human_min": 18.0,
                                "messages": 1, "chat": 1, "pr_messages": 0})
        self.assertIn("— Claude (orchestrator)", text)
        self.assertIn("Build: 8 min", text)
        self.assertIn("Review: 2 min", text)
        self.assertIn("Human: 18 min", text)
        self.assertIn("PR #58", text)

    def test_unknown_parts_say_why(self):
        text = ct.comment(self.result(build_min=None, messages=None, unclassified=2))
        self.assertIn("Build: not known", text)
        self.assertIn("not known", text.split("Messages")[1])
        self.assertIsNone(ct.data_of(text)["build_min"])

    def test_the_guess_goes_in_when_given(self):
        self.assertIn("Guessed beforehand: build 15, review 5", ct.comment(self.result(), "build 15, review 5"))
        self.assertNotIn("Guessed", ct.comment(self.result()))

    def test_already_posted(self):
        text = ct.comment(self.result())
        self.assertTrue(ct.posted(["hello", text]))
        self.assertFalse(ct.posted(["hello", "Card times, by hand"]))

    def test_collect_reads_every_card(self):
        a = ct.comment(self.result())
        b = ct.comment(self.result(issue=55, pr=59, build_min=3.0))
        rows = ct.collect([a, "unrelated", b])
        self.assertEqual([(r["issue"], r["build_min"]) for r in rows], [(54, 8.0), (55, 3.0)])


def oracle(events, data):
    """A second, simpler working-out of the three times, for the random tests. It assumes what the
    generator guarantees: one merged PR, builders named by the branch only, todos name the PR or nothing."""
    p = data["prs"][0]
    opened, merged = ct.seconds(p["createdAt"]), ct.seconds(p["mergedAt"])
    ordered = sorted(events, key=lambda e: e["time"])
    firsts, pushed = {}, set()
    for e in ordered:
        a = e.get("agent_id")
        if a and e.get("agent_type", "").endswith("builder"):
            firsts.setdefault(a, ct.seconds(e["time"]))
            if p["headRefName"] in (e.get("command") or "").split():
                pushed.add(a)
    starts = [firsts[a] for a in pushed if firsts[a] <= opened]
    build = (opened - min(starts)) / 60 if starts else None
    stops = [e for e in ordered if e["event"] == "Stop" and e.get("holder") == "human"
             and opened <= ct.seconds(e["time"]) <= merged]
    named = [e for e in stops if "#%d" % p["number"] in e.get("todo", "")]
    hand = (named or stops or [None])[0]
    if hand is None:
        return build, None, None, None
    h = ct.seconds(hand["time"])
    chat = [e for e in ordered if e["event"] == "UserPromptSubmit" and e["session"] == hand["session"]
            and h < ct.seconds(e["time"]) <= merged and not e.get("automatic")]
    count = None if any("routine" not in e for e in chat) else sum(1 for e in chat if not e["routine"])
    return build, (h - opened) / 60, (merged - h) / 60, count


class Properties(unittest.TestCase):
    def test_random_timelines_match_the_oracle(self):
        rng = random.Random(55)
        for case in range(300):
            opened, merged = rng.randint(5, 40), rng.randint(41, 90)
            events = []
            for i in range(rng.randint(0, 4)):
                agent = "a%d" % i
                kind = rng.choice(["apb:builder", "apb:builder", "apb:reviewer"])
                t = rng.randint(0, 100)
                events.append(start(t, agent, kind))
                if rng.random() < 0.3:
                    events.append(start(t + rng.randint(1, 10), agent, kind))  # resumed later
                branch = rng.choice(["issue-54-brief-check", "issue-53-other", "fix-times"])
                events.append(bash(t + rng.randint(0, 5), agent, "git push origin " + branch, kind))
            for _ in range(rng.randint(0, 5)):
                t = rng.randint(0, 100)
                holder = rng.choice(["human", "claude", None])
                todo = rng.choice(["merge PR #58", "go for the next card", None])
                events.append(stop(t, holder, todo, session=rng.choice([S, S, "other"])))
            for _ in range(rng.randint(0, 6)):
                kind = rng.choice(["routine", "other", "auto", "unknown"])
                session = rng.choice([S, S, "other"])
                t = rng.randint(0, 100)
                if kind == "unknown":
                    events.append({"time": at(t), "event": "UserPromptSubmit", "session": session})
                else:
                    events.append(prompt(t, routine=kind == "routine", automatic=kind == "auto", session=session))
            rng.shuffle(events)
            events.sort(key=lambda e: e["time"])  # the file is in time order
            data = issue([pr(opened=opened, merged=merged)])
            r = ct.card_times(events, data)
            want = oracle(events, data)
            got = (r["build_min"], r["review_min"], r["human_min"], r["messages"])
            for g, w, name in zip(got, want, ["build", "review", "human", "messages"]):
                if w is None or g is None:
                    self.assertEqual(g, w, "case %d, %s" % (case, name))
                else:
                    self.assertAlmostEqual(g, w, delta=0.06, msg="case %d, %s" % (case, name))
            for g in got[:3]:
                self.assertTrue(g is None or g >= 0)


GQL = {"data": {"repository": {"issue": {
    "number": 54, "state": "CLOSED", "comments": {"nodes": [{"body": "hi"}]},
    "closedByPullRequestsReferences": {"nodes": [{
        "number": 58, "createdAt": gh_at(10), "mergedAt": gh_at(30), "closedAt": gh_at(30),
        "headRefName": "issue-54-brief-check",
        "comments": {"nodes": [{"body": "why?", "createdAt": gh_at(13)}]},
        "reviews": {"nodes": [{"body": "", "submittedAt": gh_at(14)}, {"body": "index" + SIGN, "submittedAt": gh_at(11)}]},
        "reviewThreads": {"nodes": [{"comments": {"nodes": [{"body": "d" + SIGN, "createdAt": gh_at(11)},
                                                          {"body": "b", "createdAt": gh_at(15)}]}}]}}]}}}}}


class GitHub(unittest.TestCase):
    def test_flatten_puts_every_pr_comment_in_one_list(self):
        data = ct.flatten(GQL)
        self.assertEqual(data["number"], 54)
        self.assertEqual(data["comments"], ["hi"])
        bodies = sorted(c["body"] for c in data["prs"][0]["comments"])
        self.assertEqual(bodies, sorted(["why?", "index" + SIGN, "d" + SIGN, "b"]))  # empty review left out

    def run_main(self, argv, answers, events=()):
        path = os.path.join(tempfile.mkdtemp(), "events.jsonl")
        with open(path, "w") as f:
            for e in events:
                f.write(json.dumps(e) + "\n")
        calls = []

        def fake(args):
            calls.append(args)
            return answers.pop(0)
        printed = []
        try:
            with mock.patch.object(ct, "run", fake), mock.patch("builtins.print", lambda *a, **k: printed.append(a)):
                code = ct.main(argv + ["--events", path, "--repo", "o/r"])
        finally:
            os.remove(path)
            os.rmdir(os.path.dirname(path))
        return code, calls, printed

    def test_post_posts_once(self):
        code, calls, _ = self.run_main(["54", "--post"], [json.dumps(GQL), ""], builder_54())
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1][:4], ["gh", "issue", "comment", "54"])
        body = calls[1][calls[1].index("--body") + 1]
        self.assertEqual(ct.data_of(body)["build_min"], 8.0)

    def test_post_skips_a_card_that_has_its_comment(self):
        gql = json.loads(json.dumps(GQL))
        old = ct.comment(ct.card_times(builder_54(), ct.flatten(GQL)))
        gql["data"]["repository"]["issue"]["comments"]["nodes"].append({"body": old})
        code, calls, printed = self.run_main(["54", "--post"], [json.dumps(gql)], builder_54())
        self.assertEqual((code, len(calls)), (0, 1))
        self.assertIn("already", " ".join(str(a) for a in printed))

    def test_post_refuses_an_open_issue(self):
        gql = json.loads(json.dumps(GQL))
        gql["data"]["repository"]["issue"]["state"] = "OPEN"
        code, calls, _ = self.run_main(["54", "--post"], [json.dumps(gql)], builder_54())
        self.assertEqual((code, len(calls)), (1, 1))

    def test_without_post_it_only_prints(self):
        code, calls, printed = self.run_main(["54"], [json.dumps(GQL)], builder_54())
        self.assertEqual((code, len(calls)), (0, 1))
        self.assertIn("Build: 8 min", " ".join(str(a) for a in printed))

    def test_main_takes_claudes_login(self):
        gql = json.loads(json.dumps(GQL))
        pr_node = gql["data"]["repository"]["issue"]["closedByPullRequestsReferences"]["nodes"][0]
        pr_node["comments"]["nodes"].append({"body": "Renamed it.", "createdAt": gh_at(16),
                                             "author": {"login": "hornof-claude"}})
        events = builder_54() + [stop(12, "human", "PR #58")]
        _, _, printed = self.run_main(["54", "--claude-login", "hornof-claude"], [json.dumps(gql)], events)
        self.assertIn("(chat 0, PR 1)", " ".join(str(a) for a in printed))  # "why?" only
        _, _, printed = self.run_main(["54", "--claude-login", "someone-else"], [json.dumps(gql)], events)
        self.assertIn("(chat 0, PR 2)", " ".join(str(a) for a in printed))

    def test_collect_prints_one_row_per_card(self):
        body = ct.comment(ct.card_times(builder_54(), ct.flatten(GQL)))
        pages = [[{"body": "hi"}, {"body": body}], [{"body": None}]]
        code, calls, printed = self.run_main(["--collect"], [json.dumps(pages)])
        self.assertEqual(code, 0)
        self.assertIn("--paginate", calls[0])
        rows = [a[0].split("\t") for a in printed]
        self.assertEqual(rows[0], ct.FIELDS)
        self.assertEqual(rows[1][:3], ["54", "58", "8.0"])
        self.assertEqual(len(rows), 2)

    def test_no_events_file_posts_nothing(self):
        for argv in (["54", "--post"], ["54"]):
            code, calls, printed = self.run_main(argv, [json.dumps(GQL), ""], [])
            self.assertEqual((code, calls), (1, []), argv)
            self.assertIn("no events", " ".join(str(a) for a in printed))

    def test_a_gh_call_past_its_time_limit_ends_cleanly(self):
        def slow(args):
            raise ct.subprocess.TimeoutExpired(args, ct.TIME_LIMIT)
        printed = []
        path = os.path.join(tempfile.mkdtemp(), "events.jsonl")
        with open(path, "w") as f:
            f.write(json.dumps(builder_54()[0]) + "\n")
        with mock.patch.object(ct, "run", slow), mock.patch("builtins.print", lambda *a, **k: printed.append(a)):
            code = ct.main(["54", "--post", "--repo", "o/r", "--events", path])
        self.assertEqual(code, 2)
        self.assertIn("gh timed out", " ".join(str(a) for a in printed))

    def test_every_gh_call_has_a_time_limit(self):
        with mock.patch.object(ct.subprocess, "run") as sub:
            sub.return_value.stdout = "{}"
            ct.run(["gh", "x"])
        self.assertIn("timeout", sub.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
