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


def stop(minute, holder=None, todo=None, session=S, second=0, **extra):
    line = {"time": at(minute, second), "event": "Stop", "session": session}
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


def issue(prs, number=54, state="CLOSED", comments=(), card_comments=()):
    return {"number": number, "state": state, "comments": list(comments), "prs": prs,
            "card_comments": list(card_comments)}


def builder_54():
    return [start(2, "b1"), bash(3, "b1", "gh issue view 54 --repo hornof/apb"),
            bash(9, "b1", "git -C /wt/issue-54 push -u origin issue-54-brief-check"),
            {"time": at(10), "event": "SubagentStop", "session": S, "agent_id": "b1", "agent_type": "apb:builder"}]


class Example(unittest.TestCase):
    """Card #54 as it happened: builder 22:23:07, PR 22:29:32, hand-offs 22:34:09 (a decision),
    22:40:05 and 22:44:02 (merge, before and after the red suite was fixed), merge 22:47:52."""

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
            {"time": "2026-11-18T22:35:46.227Z", "event": "UserPromptSubmit", "session": S,
             "automatic": True},  # the builder reporting back: not the human
            {"time": "2026-11-18T22:36:57.828Z", "event": "UserPromptSubmit", "session": S, "routine": True},
            {"time": "2026-11-18T22:40:05.152Z", "event": "Stop", "session": S, "holder": "human",
             "todo": "merge [PR #58 (Card #54)](https://github.com/hornof/apb/pull/58)"},
            {"time": "2026-11-18T22:40:50.000Z", "event": "UserPromptSubmit", "session": S, "routine": False},
            {"time": "2026-11-18T22:41:11.109Z", "event": "SubagentStart", "session": S,
             "agent_id": "ab0", "agent_type": "apb:builder"},  # the suite was red: the builder again
            {"time": "2026-11-18T22:44:02.493Z", "event": "Stop", "session": S, "holder": "human",
             "todo": "merge [PR #58 (Card #54)](https://github.com/hornof/apb/pull/58)"},
        ]
        data = issue([{"number": 58, "createdAt": "2026-11-18T22:29:32Z", "mergedAt": "2026-11-18T22:47:52Z",
                       "closedAt": "2026-11-18T22:47:53Z", "headRefName": "issue-54-brief-check",
                       "comments": []}])
        r = ct.card_times(events, data)
        self.assertEqual(r["pr"], 58)
        self.assertAlmostEqual(r["build_min"], 6.4, places=1)
        self.assertAlmostEqual(r["review_min"], 4.6, places=1)
        # waits: 22:34:09 to 22:36:57, 22:40:05 to 22:40:50, 22:44:02 to the merge; the builder's pass is out
        self.assertAlmostEqual(r["human_min"], 7.4, places=1)
        self.assertEqual(r["hand_offs"], 3)
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
        self.assertEqual(r["human_min"], 18.0)  # no action from the human seen: 12 to 15, then 15 to 30
        self.assertEqual(r["hand_offs"], 2)

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
        self.assertIsNone(r["hand_offs"])


class HandOffs(unittest.TestCase):
    """The PR came back after a hand-off: the builder's second pass is not the human's time."""

    def test_two_hand_offs(self):
        events = builder_54() + [stop(14, "human", "merge PR #58"), prompt(15, routine=False),
                                 bash(16, "b1", "git push origin issue-54-brief-check"),
                                 stop(18, "claude"), stop(25, "human", "merge PR #58")]
        r = ct.card_times(events, issue([pr()]))
        self.assertEqual(r["review_min"], 4.0)  # opened to the first hand-off
        self.assertEqual(r["human_min"], 6.0)  # 14 to the human's message at 15, then 25 to the merge
        self.assertEqual(r["hand_offs"], 2)
        self.assertEqual((r["first_hand_off"], r["last_hand_off"]), (ct.seconds(at(14)), ct.seconds(at(25))))

    def test_one_hand_off_is_both_first_and_last(self):
        r = ct.card_times(builder_54() + [stop(14, "human", "merge PR #58")], issue([pr()]))
        self.assertEqual((r["review_min"], r["human_min"], r["hand_offs"]), (4.0, 16.0, 1))
        self.assertEqual(r["first_hand_off"], r["last_hand_off"])

    def test_only_stops_naming_the_pr_count_when_some_do(self):
        events = builder_54() + [stop(12, "human", "merge PR #57"), stop(14, "human", "merge PR #58"),
                                 stop(20, "human", "say go for the next card"), stop(25, "human", "merge PR #58"),
                                 prompt(16, routine=False)]
        r = ct.card_times(events, issue([pr()]))
        self.assertEqual((r["review_min"], r["human_min"], r["hand_offs"]), (4.0, 7.0, 2))

    def test_stops_outside_the_pr_do_not_count(self):
        events = builder_54() + [stop(8, "human", "merge PR #58"), stop(14, "human", "merge PR #58"),
                                 stop(31, "human", "merge PR #58")]
        r = ct.card_times(events, issue([pr()]))
        self.assertEqual((r["human_min"], r["hand_offs"]), (16.0, 1))

    def test_messages_still_count_from_the_first_hand_off(self):
        events = builder_54() + [stop(14, "human", "PR #58"), prompt(15, routine=False), stop(25, "human", "PR #58"),
                                 prompt(26, routine=False)]
        self.assertEqual(ct.card_times(events, issue([pr()]))["messages"], 2)


class HumanWaits(unittest.TestCase):
    """Human = the sum of the human's waits: each hand-off to the human's next action (a chat message,
    a GitHub comment on the PR or the card, or the merge), and the last hand-off to the merge."""

    def test_a_decision_then_the_merge(self):  # card #17: decision 23:15, reply 23:21, merge hand-off 23:23
        events = builder_54() + [stop(15, "human", "answer decision 9 on PR #58"), prompt(21, routine=True),
                                 stop(23, "human", "merge PR #58")]
        r = ct.card_times(events, issue([pr(merged=24)]))
        self.assertEqual((r["human_min"], r["hand_offs"]), (7.0, 2))  # 15 to 21, then 23 to 24

    def test_a_question_mid_wait_does_not_restart_the_clock(self):
        events = builder_54() + [stop(14, "human", "merge PR #58"), prompt(20, routine=False),
                                 stop(21, "human", "merge PR #58")]
        self.assertEqual(ct.card_times(events, issue([pr()]))["human_min"], 15.0)  # 14 to 20, then 21 to 30

    def test_an_answer_to_the_humans_question_with_no_new_hand_off_starts_a_new_wait(self):
        # card #17: the human asked at 23:17, the orchestrator answered and stopped at 23:18 with no
        # holder line, so the human held the PR again (as hooks/apb-stop.py reads it)
        events = builder_54() + [stop(14, "human", "answer decision 3 on PR #58"), prompt(15, routine=False),
                                 stop(16), prompt(20, routine=True), stop(21), stop(25, "human", "merge PR #58")]
        r = ct.card_times(events, issue([pr()]))
        self.assertEqual((r["human_min"], r["hand_offs"]), (14.0, 2))  # 14-15, 16-20, 21-25, 25-30

    def test_a_stop_while_a_builder_runs_does_not_start_a_wait(self):
        # card #17: the human answered at 23:21:36, the orchestrator started a builder and stopped with no
        # holder line at 23:21:50; the ball was with the builder until its report at 23:23:06
        events = builder_54() + [stop(14, "human", "answer decision 3 on PR #58"), prompt(15, routine=False),
                                 start(16, "fix"), stop(16, second=30),
                                 {"time": at(20), "event": "SubagentStop", "session": S, "agent_id": "fix",
                                  "agent_type": "apb:builder"},
                                 prompt(20, automatic=True), stop(25, "human", "merge PR #58")]
        self.assertEqual(ct.card_times(events, issue([pr()]))["human_min"], 6.0)  # 14-15, 25-30

    def test_an_agent_running_in_another_session_does_not_stop_a_wait(self):
        events = builder_54() + [stop(14, "human", "PR #58"), prompt(15, routine=False),
                                 dict(start(15, "elsewhere"), session="another"), stop(16),
                                 stop(25, "human", "PR #58")]
        self.assertEqual(ct.card_times(events, issue([pr()]))["human_min"], 15.0)  # 14-15, 16-25, 25-30

    def test_a_turn_without_a_human_message_does_not_start_a_wait(self):
        events = builder_54() + [stop(14, "human", "PR #58"),
                                 prompt(17, automatic=True), stop(18),  # the builder reporting back
                                 prompt(19), stop(20),  # a prompt logged before the routine mark
                                 stop(21, sent_back=True), stop(25, "human", "PR #58")]
        self.assertEqual(ct.card_times(events, issue([pr()]))["human_min"], 10.0)  # 14-19, 25-30

    def test_a_github_only_reply_ends_the_wait(self):
        events = builder_54() + [stop(14, "human", "answer decision 3 on PR #58"), stop(25, "human", "merge PR #58")]
        reply = {"body": "b", "author": "hornof", "createdAt": gh_at(18)}
        on_pr = ct.card_times(events, issue([pr(comments=[reply])]), "hornof-claude")
        on_card = ct.card_times(events, issue([pr()], card_comments=[reply]), "hornof-claude")
        self.assertEqual((on_pr["human_min"], on_card["human_min"]), (9.0, 9.0))  # 14 to 18, then 25 to 30

    def test_claudes_comments_and_automatic_prompts_do_not_end_a_wait(self):
        events = builder_54() + [stop(14, "human", "PR #58"), prompt(15, automatic=True),
                                 prompt(16, routine=False, session="another"), stop(25, "human", "PR #58")]
        fixed = {"body": "Fixed.", "author": "hornof-claude", "createdAt": gh_at(17)}
        signed = {"body": "Fixed." + SIGN, "author": "hornof", "createdAt": gh_at(18)}
        r = ct.card_times(events, issue([pr(comments=[fixed])], card_comments=[signed]), "hornof-claude")
        self.assertEqual(r["human_min"], 16.0)  # nothing from the human: 14 to 25, then 25 to 30

    def test_an_action_before_the_hand_off_does_not_end_it(self):
        events = builder_54() + [prompt(13, routine=False), stop(14, "human", "PR #58")]
        reply = {"body": "b", "author": "hornof", "createdAt": gh_at(12)}
        self.assertEqual(ct.card_times(events, issue([pr(comments=[reply])]), "hornof-claude")["human_min"], 16.0)

    def test_card_comments_come_from_github(self):
        got = json.loads(json.dumps(GQL))
        got["data"]["repository"]["issue"]["comments"]["nodes"] = [
            {"body": "go", "createdAt": gh_at(13), "author": {"login": "hornof"}}]
        data = ct.flatten(got)
        self.assertEqual(data["comments"], ["go"])
        self.assertEqual(data["card_comments"], [{"body": "go", "createdAt": gh_at(13), "author": "hornof"}])
        self.assertEqual(ct.QUERY.count("author { login }"), 4)


class DraftPr(unittest.TestCase):
    """Issue #131: a builder opens a draft PR in its first minute and marks it ready when the build is
    done. Build runs to the ready time, and review starts there; a PR never a draft uses createdAt."""

    def test_build_ends_and_review_starts_when_the_draft_is_marked_ready(self):
        events = builder_54() + [stop(25, "human", "merge PR #58")]
        draft = pr(opened=3, merged=30) | {"readyAt": gh_at(20)}
        r = ct.card_times(events, issue([draft]))
        self.assertEqual(r["build_min"], 18.0)   # builder start 12:02 to ready 12:20
        self.assertEqual(r["review_min"], 5.0)   # ready 12:20 to the hand-off 12:25

    def test_a_pr_never_a_draft_uses_its_opening(self):
        events = builder_54() + [stop(25, "human", "merge PR #58")]
        r = ct.card_times(events, issue([pr(opened=10, merged=30)]))
        self.assertEqual((r["build_min"], r["review_min"]), (8.0, 15.0))

    def test_a_hand_off_before_the_ready_time_is_not_the_review_hand_off(self):
        events = builder_54() + [stop(12, "human", "answer on #54"), stop(25, "human", "merge PR #58")]
        r = ct.card_times(events, issue([pr(opened=3, merged=30) | {"readyAt": gh_at(20)}]))
        self.assertEqual(r["review_min"], 5.0)

    def test_flatten_takes_the_last_ready_time(self):
        got = json.loads(json.dumps(GQL))
        node = got["data"]["repository"]["issue"]["closedByPullRequestsReferences"]["nodes"][0]
        node["timelineItems"] = {"nodes": [{"createdAt": gh_at(15)}, {"createdAt": gh_at(18)}]}
        self.assertEqual(ct.flatten(got)["prs"][0]["readyAt"], gh_at(18))
        self.assertIn("READY_FOR_REVIEW_EVENT", ct.QUERY)

    def test_flatten_with_no_ready_event_has_no_ready_time(self):
        got = json.loads(json.dumps(GQL))
        node = got["data"]["repository"]["issue"]["closedByPullRequestsReferences"]["nodes"][0]
        node["timelineItems"] = {"nodes": []}
        self.assertIsNone(ct.flatten(got)["prs"][0]["readyAt"])
        self.assertIsNone(ct.flatten(GQL)["prs"][0]["readyAt"])


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
        self.assertEqual(ct.QUERY.count("author { login }"), 4)

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
                                "messages": 1, "chat": 1, "pr_messages": 0, "hand_offs": 1})
        self.assertIn("— Claude (orchestrator)", text)
        self.assertIn("Build: 8 min", text)
        self.assertIn("Review: 2 min", text)
        self.assertIn("Human: 18 min", text)
        self.assertIn("Hand-offs: 1, at 12:12", text)
        self.assertIn("PR #58", text)

    def test_two_hand_offs_are_named_in_the_comment(self):
        events = builder_54() + [stop(12, "human", "PR #58"), stop(25, "human", "PR #58")]
        text = ct.comment(ct.card_times(events, issue([pr()])))
        self.assertIn("Review: 2 min, PR #58 opened 12:10 to the first hand-off to the human 12:12", text)
        self.assertIn("Human: 18 min, the human's waits: from each hand-off to their next message or comment, "
                      "and from the last hand-off 12:25 to the merge 12:30", text)
        self.assertIn("Hand-offs: 2, at 12:12 and 12:25", text)
        self.assertEqual(ct.data_of(text)["hand_offs"], 2)

    def test_no_hand_off_line_without_a_hand_off(self):
        text = ct.comment(ct.card_times(builder_54(), issue([pr()])))
        self.assertIn("Hand-offs: not known", text)

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
    hands = named or stops
    if not hands:
        return build, None, None, None, None
    first = hands[0]
    h = ct.seconds(first["time"])
    human_comments = [ct.seconds(c["createdAt"]) for c in p["comments"] + data["card_comments"]
                      if c["author"] == "hornof"]
    chat = [e for e in ordered if e["event"] == "UserPromptSubmit" and e["session"] == first["session"]
            and h < ct.seconds(e["time"]) <= merged and not e.get("automatic")]
    count = None if any("routine" not in e for e in chat) else (
        sum(1 for e in chat if not e["routine"]) +
        sum(1 for c in p["comments"] if c["author"] == "hornof" and not ct.routine(c["body"])
            and h < ct.seconds(c["createdAt"]) <= merged))
    first_t, last_t = ct.seconds(hands[0]["time"]), ct.seconds(hands[-1]["time"])
    waits = list(hands)
    for i, e in enumerate(ordered):  # a holderless Stop ending a turn the human started resumes the wait
        if e["event"] != "Stop" or e.get("holder") or e.get("sent_back"):
            continue
        if not first_t < ct.seconds(e["time"]) < last_t:
            continue
        before = [x for x in ordered[:i] if x["session"] == e["session"]]
        holders = [x["holder"] for x in before if x["event"] == "Stop" and x.get("holder")]
        ends = [j for j, x in enumerate(before) if x["event"] == "Stop" and not x.get("sent_back")]
        turn = before[ends[-1] + 1:] if ends else before
        lasts = {}
        for x in before:
            if x.get("agent_id"):
                lasts[x["agent_id"]] = x["event"]
        busy = any(kind != "SubagentStop" for kind in lasts.values())  # an agent of this session still runs
        if holders and holders[-1] == "human" and not busy and any(
                x["event"] == "UserPromptSubmit" and "routine" in x and not x.get("automatic") for x in turn):
            waits.append(e)
    waits.sort(key=lambda e: e["time"])
    human = 0.0
    for i, w in enumerate(waits):  # each wait, one at a time
        t = ct.seconds(w["time"])
        if i == len(waits) - 1:
            human += merged - t
            continue
        acts = [ct.seconds(e["time"]) for e in ordered if e["event"] == "UserPromptSubmit"
                and e["session"] == w["session"] and not e.get("automatic")] + human_comments
        later = [a for a in acts if a > t] + [ct.seconds(waits[i + 1]["time"])]
        human += min(later) - t
    return build, (h - opened) / 60, human / 60, count, len(hands)


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
                if rng.random() < 0.7:
                    events.append({"time": at(t + rng.randint(6, 30)), "event": "SubagentStop", "session": S,
                                   "agent_id": agent, "agent_type": kind})
            for _ in range(rng.randint(0, 10)):
                t = rng.randint(0, 100)
                holder = rng.choice(["human", "claude", None, None])
                todo = rng.choice(["merge PR #58", "go for the next card", None])
                events.append(stop(t, holder, todo, session=rng.choice([S, S, "other"])))
            for _ in range(rng.randint(0, 10)):
                kind = rng.choice(["routine", "other", "auto", "unknown"])
                session = rng.choice([S, S, "other"])
                t = rng.randint(0, 100)
                if kind == "unknown":
                    events.append({"time": at(t), "event": "UserPromptSubmit", "session": session})
                else:
                    events.append(prompt(t, routine=kind == "routine", automatic=kind == "auto", session=session))
            if rng.random() < 0.5:  # a question mid-wait: hand-off, the human asks, an answer with no holder line
                a = rng.randint(opened - 5, merged)
                kind = rng.choice(["routine", "other", "auto", "unknown"])
                asked = {"time": at(a + 1), "event": "UserPromptSubmit", "session": S} if kind == "unknown" else \
                    prompt(a + 1, routine=kind == "routine", automatic=kind == "auto")
                events += [stop(a, "human", "answer decision 3 on PR #58"), asked,
                           stop(a + 2, sent_back=rng.random() < 0.3),
                           stop(rng.randint(a + 3, a + 20), "human", "merge PR #58")]
            rng.shuffle(events)
            events.sort(key=lambda e: e["time"])  # the file is in time order
            comments = [[{"body": rng.choice(["b", "why?", "Fixed."]), "author": rng.choice(["hornof", "hornof-claude"]),
                          "createdAt": gh_at(rng.randint(0, 100))} for _ in range(rng.randint(0, 2))]
                        for _ in range(2)]
            data = issue([pr(opened=opened, merged=merged, comments=comments[0])], card_comments=comments[1])
            r = ct.card_times(events, data, "hornof-claude")
            want = oracle(events, data)
            got = (r["build_min"], r["review_min"], r["human_min"], r["messages"], r["hand_offs"])
            for g, w, name in zip(got, want, ["build", "review", "human", "messages", "hand_offs"]):
                if w is None or g is None:
                    self.assertEqual(g, w, "case %d, %s" % (case, name))
                else:
                    self.assertAlmostEqual(g, w, delta=0.06, msg="case %d, %s" % (case, name))
            for g in got[:3]:
                self.assertTrue(g is None or g >= 0)
            if r["hand_offs"]:
                self.assertLessEqual(r["first_hand_off"], r["last_hand_off"])
                # the human's time is at least the last wait and at most the first hand-off to the end
                self.assertGreaterEqual(r["human_min"], (r["end"] - r["last_hand_off"]) / 60 - 0.06)
                self.assertLessEqual(r["human_min"], (r["end"] - r["first_hand_off"]) / 60 + 0.06)


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
