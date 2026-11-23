"""Tests for consistency-check.py, the end-of-turn check (#121). Run from the repo root:
python3 -m unittest scripts/test_consistency_check.py

Each case of the check has a test built from the incident that added it (CASES names the incident).
The board cases are also checked with random boards against a second, simpler rule (an oracle that
works out each card's one right column from its issue, its PRs and its worktrees), in both directions:
a card in the right column is never named, a card in a wrong one always is. The whole script is then
run for real, with a fake `gh` on the PATH, to check what it asks GitHub, what it prints and how it exits,
and that a GitHub error never counts as a problem.

loose-follow-up (#173) is tested on the real PRs #161, #163 and #164 as GitHub had them on 2026-11-22
(test_consistency_check_2026_11_22.json, read with gh pr view, gh api .../reviews and gh issue view), and
on random Notes posts and replies against a bookkeeping of what each reply settled, written as the
replies are made.
"""
import importlib.util
import json
import os
import random
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "consistency-check.py")

spec = importlib.util.spec_from_file_location("consistency_check", SCRIPT)
cc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cc)

HUMAN, CLAUDE = "hum", "claude-bot"
REPO = "hum/proj"
ADVISOR = "\n\n— Claude (advisor)"
DECISION = ("Decision 6: should the button say Save or Done?\n(a) Save\n(b) Done\n"
            "Recommended: a\nResolve with no reply = a")


def card(n, status="todo", state="OPEN", assignees=()):
    return {"number": n, "status": status, "state": state, "assignees": list(assignees)}


def comment(body, author=CLAUDE, at="2026-11-20T10:00:00Z", url=None):
    return {"body": body, "author": author, "createdAt": at, "url": url or "https://github.com/%s/pull/1#c" % REPO}


def thread(*comments, resolved=False):
    return {"isResolved": resolved, "comments": list(comments)}


def pr(n, closes=(), draft=False, head=None, assignees=(), threads=(), comments=(), reviews=(), commits=()):
    return {"number": n, "draft": draft, "head": head or "issue-%d-x" % (closes[0] if closes else n),
            "assignees": list(assignees), "closes": list(closes), "threads": list(threads),
            "comments": list(comments), "reviews": list(reviews), "commits": list(commits)}


def snap(board=(), prs=(), worktrees=(), assigned=(), human=HUMAN, agents=None):
    return {"repo": REPO, "human": human, "claude_login": CLAUDE, "board": list(board), "prs": list(prs),
            "worktrees": list(worktrees), "assigned": list(assigned), "agents": agents or {}}


# A full commit id on the PR; a Fix post gives its short form, abc1234.
OID = "abc1234" + "5" * 33


def cases(found):
    return sorted({p["case"] for p in found})


class Cases(unittest.TestCase):
    def test_every_case_names_its_incident(self):
        self.assertGreaterEqual(len(cc.CASES), 9)
        for case in cc.CASES:
            self.assertTrue(case["id"] and case["incident"], case)
            self.assertRegex(case["incident"], r"#\d+|20\d\d-\d\d-\d\d", case["id"])

    def test_case_ids_are_unique(self):
        ids = [c["id"] for c in cc.CASES]
        self.assertEqual(len(ids), len(set(ids)))

    def test_a_consistent_project_has_no_problem(self):
        s = snap(board=[card(1, "todo"), card(2, "in progress"), card(3, "in review", assignees=[HUMAN]),
                        card(4, "done", "CLOSED")],
                 prs=[pr(10, closes=[2], draft=True), pr(11, closes=[3], assignees=[HUMAN])],
                 worktrees=["issue-2-thing", "main"],
                 assigned=[{"kind": "issue", "number": 3, "state": "OPEN", "last_comment": None,
                            "prs": [{"kind": "pr", "number": 11, "state": "OPEN", "draft": False}]},
                           {"kind": "pr", "number": 11, "state": "OPEN", "draft": False}])
        self.assertEqual(cc.check(s, {"prs": [11], "merge": [11], "issues": [], "decisions": []}), [])


class BuildInTodo(unittest.TestCase):
    """2026-11-20: #117 and #118 were built as separate sessions while their cards sat in Todo."""

    def test_a_worktree_for_a_todo_card_is_named(self):
        found = cc.check(snap(board=[card(117), card(118)], worktrees=["issue-117-popups", "issue-118"]))
        self.assertEqual(cases(found), ["build-in-todo"])
        text = " ".join(p["problem"] for p in found)
        self.assertIn("#117", text)
        self.assertIn("#118", text)
        self.assertIn("start-build 117", text)

    def test_moving_the_card_clears_it(self):
        self.assertEqual(cc.check(snap(board=[card(117, "in progress")], worktrees=["issue-117-popups"])), [])

    def test_a_draft_pr_for_a_todo_card_is_a_build(self):
        found = cc.check(snap(board=[card(5)], prs=[pr(9, closes=[5], draft=True, head="feature")]))
        self.assertEqual(cases(found), ["build-in-todo"])

    def test_a_branch_named_for_the_card_counts_even_without_closes(self):
        found = cc.check(snap(board=[card(5)], prs=[pr(9, closes=[], draft=True, head="issue-5-x")]))
        self.assertEqual(cases(found), ["build-in-todo"])

    def test_a_number_inside_another_number_is_not_the_card(self):
        self.assertEqual(cc.check(snap(board=[card(5)], worktrees=["issue-51-x", "issue-15", "fix-5"])), [])

    def test_a_closed_card_is_not_a_build(self):
        found = cc.check(snap(board=[card(5, "done", "CLOSED")], worktrees=["issue-5-old"]))
        self.assertEqual(found, [])


class BuildInIsolatedWorktree(unittest.TestCase):
    """#159: a builder launched with the Agent tool's worktree isolation (#142) works on the branch Claude
    Code gives its worktree, worktree-agent-<agent id>, until it makes its own. The events file says which
    card each agent was launched on (hooks/apb-events.py, an AgentLaunch line)."""

    # The branch Claude Code gave this card's own builder, 2026-11-22, and the agent id it logged.
    BRANCH, AGENT = "worktree-agent-aa4609599f80f78b1", "aa4609599f80f78b1"

    def test_an_isolated_worktree_for_a_todo_card_is_named(self):
        found = cc.check(snap(board=[card(159), card(160)], worktrees=["main", self.BRANCH],
                              agents={self.AGENT: [159]}))
        self.assertEqual(cases(found), ["build-in-todo"])
        self.assertEqual([p["card"] for p in found], [159])
        self.assertIn(self.BRANCH, found[0]["problem"])
        self.assertIn("start-build 159", found[0]["problem"])

    def test_moving_the_card_clears_it(self):
        self.assertEqual(cc.check(snap(board=[card(159, "in progress")], worktrees=[self.BRANCH],
                                       agents={self.AGENT: [159]})), [])

    def test_an_agent_with_no_launch_line_names_no_card(self):
        self.assertEqual(cc.check(snap(board=[card(159)], worktrees=[self.BRANCH])), [])
        self.assertEqual(cc.check(snap(board=[card(159)], worktrees=[self.BRANCH], agents={"a0": [159]})), [])

    def test_only_the_card_the_agent_was_launched_on(self):
        found = cc.check(snap(board=[card(159), card(15)], worktrees=[self.BRANCH], agents={self.AGENT: [15]}))
        self.assertEqual([p["card"] for p in found], [15])

    def test_a_branch_that_only_contains_the_id_is_not_the_agents(self):
        s = snap(board=[card(159)], worktrees=["my-worktree-agent-aa4609599f80f78b1-x", "x" + self.AGENT],
                 agents={self.AGENT: [159]})
        self.assertEqual(cc.check(s), [])

    def test_agent_cards_from_the_events_file(self):
        project = tempfile.mkdtemp()
        os.makedirs(os.path.join(project, ".apb"))
        with open(os.path.join(project, ".apb", "events.jsonl"), "w") as f:
            for line in [{"event": "SubagentStart", "agent_id": self.AGENT},
                         {"event": "AgentLaunch", "launched": self.AGENT, "cards": [159]},
                         {"event": "AgentLaunch", "launched": "b2", "cards": []},
                         {"event": "AgentLaunch", "launched": 7, "cards": [3]},
                         {"event": "AgentLaunch", "launched": "c3", "cards": ["x", 4, True]}]:
                f.write(json.dumps(line) + "\n")
            f.write("not json\n")
        self.assertEqual(cc.agent_cards(project), {self.AGENT: [159], "b2": [], "c3": [4]})
        self.assertEqual(cc.agent_cards(os.path.join(project, "missing")), {})

    def test_a_real_isolated_worktree_end_to_end(self):
        # git's own answer for a worktree on Claude Code's branch name, through to the check.
        folder = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", "-b", "main", folder], check=True, timeout=30)
        subprocess.run(["git", "-C", folder, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "x"], check=True, timeout=30)
        wt = os.path.join(folder, ".claude", "worktrees", "agent-" + self.AGENT)
        subprocess.run(["git", "-C", folder, "worktree", "add", "-q", "-b", self.BRANCH, wt], check=True, timeout=30)
        os.makedirs(os.path.join(folder, ".apb"))
        with open(os.path.join(folder, ".apb", "events.jsonl"), "w") as f:
            f.write(json.dumps({"event": "AgentLaunch", "launched": self.AGENT, "cards": [159]}) + "\n")
        s = snap(board=[card(159)], worktrees=cc.worktree_branches(folder), agents=cc.agent_cards(folder))
        self.assertEqual(cases(cc.check(s)), ["build-in-todo"])


class ReadyPrNotInReview(unittest.TestCase):
    """Card #121's second case: a PR ready for review closes the card, but the card isn't In review."""

    def test_a_ready_pr_needs_in_review(self):
        for status in ("todo", "in progress"):
            found = cc.check(snap(board=[card(7, status)], prs=[pr(20, closes=[7])]))
            self.assertIn("pr-ready-not-in-review", cases(found), status)
            self.assertIn("pr-ready 20", " ".join(p["problem"] for p in found))

    def test_in_review_is_right(self):
        self.assertEqual(cc.check(snap(board=[card(7, "in review")], prs=[pr(20, closes=[7])])), [])

    def test_a_draft_does_not_need_in_review(self):
        # #156: a card goes to In review only when its PR is marked ready.
        self.assertEqual(cc.check(snap(board=[card(7, "in progress")], prs=[pr(20, closes=[7], draft=True)])), [])


class InReviewWithDraftPr(unittest.TestCase):
    """2026-11-21: #153 and #140 went to In review a minute after their draft PRs (#154, #155) opened (#156)."""

    def test_a_card_in_review_with_only_a_draft_pr_is_named(self):
        found = cc.check(snap(board=[card(153, "in review")], prs=[pr(154, closes=[153], draft=True)]))
        self.assertEqual(cases(found), ["in-review-with-draft-pr"])
        self.assertEqual(found[0]["card"], 153)
        text = found[0]["problem"]
        self.assertIn("#153", text)
        self.assertIn("#154", text)
        self.assertIn("draft", text)
        self.assertIn("pr-draft 154", text)  # says how to put it right

    def test_both_incident_cards_are_named(self):
        found = cc.check(snap(board=[card(153, "in review"), card(140, "in review")],
                              prs=[pr(154, closes=[153], draft=True), pr(155, closes=[140], draft=True)]))
        self.assertEqual(sorted(p["card"] for p in found if p["case"] == "in-review-with-draft-pr"), [140, 153])

    def test_moving_it_back_to_in_progress_clears_it(self):
        self.assertEqual(cc.check(snap(board=[card(153, "in progress")], prs=[pr(154, closes=[153], draft=True)])), [])

    def test_marking_the_pr_ready_clears_it(self):
        self.assertEqual(cc.check(snap(board=[card(153, "in review")], prs=[pr(154, closes=[153])])), [])

    def test_a_ready_pr_beside_a_draft_one_keeps_it_in_review(self):
        s = snap(board=[card(153, "in review")], prs=[pr(154, closes=[153], draft=True), pr(157, closes=[153])])
        self.assertEqual(cc.check(s), [])

    def test_a_branch_named_for_the_card_without_closes_is_not_its_pr(self):
        # Only a PR that closes the card says which column it belongs in.
        s = snap(board=[card(153, "in review")], prs=[pr(154, closes=[], draft=True, head="issue-153-x")])
        self.assertEqual(cc.check(s), [])

    def test_in_review_with_no_open_pr_is_left_alone(self):
        self.assertEqual(cc.check(snap(board=[card(153, "in review")])), [])


class BoardAndIssue(unittest.TestCase):
    """Card #121's fourth case: a card is Done but its issue is open, or the other way round."""

    def test_done_but_open(self):
        found = cc.check(snap(board=[card(3, "done", "OPEN")]))
        self.assertEqual(cases(found), ["board-vs-issue"])

    def test_closed_but_not_done(self):
        for status in ("todo", "in progress", "in review", ""):
            found = cc.check(snap(board=[card(3, status, "CLOSED")]))
            self.assertEqual(cases(found), ["board-vs-issue"], status)

    def test_matching_is_fine(self):
        self.assertEqual(cc.check(snap(board=[card(3, "done", "CLOSED"), card(4, "todo", "OPEN")])), [])


class Assignment(unittest.TestCase):
    """2026-11-21 4:36 PM: #132 and PR #134 waited on the human with no assignee (#121 comment 4);
    #111: assigned to the human with nothing waiting."""

    def test_a_hand_off_needs_the_pr_and_its_card_assigned(self):
        s = snap(board=[card(132, "in review")], prs=[pr(134, closes=[132])])
        found = cc.check(s, {"prs": [134], "merge": [134], "issues": [], "decisions": []})
        self.assertEqual(cases(found), ["hand-off-not-assigned"])
        text = " ".join(p["problem"] for p in found)
        self.assertIn("PR #134", text)
        self.assertIn("#132", text)
        self.assertIn("--add-assignee hum", text)

    def test_assigned_clears_it(self):
        s = snap(board=[card(132, "in review", assignees=[HUMAN])], prs=[pr(134, closes=[132], assignees=[HUMAN])])
        self.assertEqual(cc.check(s, {"prs": [134], "merge": [134], "issues": [], "decisions": []}), [])

    def test_a_question_on_a_card_needs_the_card_assigned(self):
        c = dict(card(40, "in progress"), last_comment={"author": CLAUDE, "body": "Which name should it use?"}, prs=[])
        found = cc.check(snap(board=[c]), {"prs": [], "merge": [], "issues": [40], "decisions": []})
        self.assertEqual(cases(found), ["hand-off-not-assigned"])

    def test_choosing_the_next_card_needs_no_assignment_on_either_turn(self):
        # Review fix 1 on #157: "choose the next card to build, #130 or #131" flagged both cards as
        # unassigned, and once assigned, the next turn flagged both as stale.
        hand_off = {"prs": [], "merge": [], "issues": [130, 131], "decisions": []}
        first = snap(board=[card(130), card(131)])
        self.assertEqual(cc.check(first, hand_off), [])
        assigned = [card(130, assignees=[HUMAN]), card(131, assignees=[HUMAN])]
        items = [{"kind": "issue", "number": n, "state": "OPEN", "last_comment": None, "prs": []} for n in (130, 131)]
        second = snap(board=assigned, assigned=items)
        self.assertEqual(cases(cc.check(second, hand_off)), ["stale-assignment"])

    def test_the_two_cases_never_ask_for_opposite_things(self):
        # Both directions, for every kind of card: of "assigned" and "not assigned", exactly one is clean.
        rng = random.Random(157)
        lasts = [None, {"author": CLAUDE, "body": "Which one?"}, {"author": CLAUDE, "body": "Done.\n\n— Claude (orchestrator)"},
                 {"author": HUMAN, "body": "why?"}, {"author": HUMAN, "body": "b" + ADVISOR}]
        for _ in range(300):
            last = rng.choice(lasts)
            pr_kind = rng.choice([None, "ready", "draft"])
            prs = [] if pr_kind is None else [pr(70, closes=[7], draft=pr_kind == "draft", assignees=[HUMAN])]
            hand_off = {"prs": [], "merge": [], "issues": [7], "decisions": []}
            clean = []
            for assignees in ([], [HUMAN]):
                c = dict(card(7, "in review" if pr_kind == "ready" else "in progress", assignees=assignees), last_comment=last)
                item = {"kind": "issue", "number": 7, "state": "OPEN", "last_comment": last,
                        "prs": [{"kind": "pr", "number": 70, "state": "OPEN", "draft": pr_kind == "draft"}] if prs else []}
                found = cc.check(snap(board=[c], prs=prs, assigned=[item] if assignees else []), hand_off)
                clean.append(not [p for p in found if p["case"] in ("hand-off-not-assigned", "stale-assignment")
                                  and "card #7" in p["problem"]])
            self.assertEqual(sum(clean), 1, (last, pr_kind, clean))

    def test_no_hand_off_no_assignment_needed(self):
        s = snap(board=[card(132, "in review")], prs=[pr(134, closes=[132])])
        self.assertEqual(cc.check(s), [])

    def test_an_organization_repo_has_no_known_human(self):
        s = snap(board=[card(132, "in review")], prs=[pr(134, closes=[132])], human=None)
        self.assertEqual(cc.check(s, {"prs": [134], "merge": [134], "issues": [], "decisions": []}), [])

    def test_a_stale_assignment_is_named(self):
        s = snap(assigned=[{"kind": "pr", "number": 99, "state": "MERGED", "draft": False}])
        found = cc.check(s)
        self.assertEqual(cases(found), ["stale-assignment"])
        self.assertIn("--remove-assignee hum", found[0]["problem"])


class ResolvedDecision(unittest.TestCase):
    """2026-11-20: Decision 6 on PR #119 was resolved and nothing acted on it until the human asked."""

    def test_resolved_with_no_confirmation(self):
        t = thread(comment(DECISION, url="https://github.com/hum/proj/pull/119#discussion_r6"), resolved=True)
        found = cc.check(snap(prs=[pr(119, closes=[], threads=[t])]))
        self.assertEqual(cases(found), ["resolved-decision-unconfirmed"])
        self.assertIn("discussion_r6", found[0]["problem"])

    def test_the_confirming_reply_clears_it(self):
        t = thread(comment(DECISION), comment("Resolved = a: the builder renames the button now.\n\n— Claude (orchestrator)"),
                   resolved=True)
        self.assertEqual(cc.check(snap(prs=[pr(119, threads=[t])])), [])

    def test_a_confirmation_from_the_human_does_not_count(self):
        t = thread(comment(DECISION), comment("Resolved = a", author=HUMAN), resolved=True)
        self.assertEqual(cases(cc.check(snap(prs=[pr(119, threads=[t])]))), ["resolved-decision-unconfirmed"])

    def test_a_thread_the_human_started_is_no_decision(self):
        t = thread(comment("why this name?", author=HUMAN), resolved=True)
        self.assertEqual(cc.check(snap(prs=[pr(119, threads=[t])])), [])


class ReplyInDecision(unittest.TestCase):
    """2026-11-20: the orchestrator's discussion summary went inside Decision 7's thread on PR #120."""

    def test_claude_replying_before_the_human_is_named(self):
        t = thread(comment(DECISION, url="https://github.com/hum/proj/pull/120#discussion_r7"),
                   comment("In plain words: ...\n\n— Claude (orchestrator)"))
        found = cc.check(snap(prs=[pr(120, threads=[t])]))
        self.assertEqual(cases(found), ["reply-in-unanswered-decision"])
        self.assertIn("discussion_r7", found[0]["problem"])

    def test_the_advisor_may_reply_before_the_human(self):
        # #169: the advisor replies inside the Decision thread when it helps (this reverses #97).
        t = thread(comment(DECISION), comment("Option b also changes the docs." + ADVISOR, author=HUMAN))
        self.assertEqual(cc.check(snap(prs=[pr(120, threads=[t])])), [])

    def test_the_advisors_reply_is_not_the_humans_answer(self):
        t = thread(comment(DECISION), comment("b is safer." + ADVISOR, author=HUMAN),
                   comment("On it.\n\n— Claude (orchestrator)"))
        self.assertEqual(cases(cc.check(snap(prs=[pr(120, threads=[t])]))), ["reply-in-unanswered-decision"])

    def test_an_unsigned_reply_from_claudes_account_is_named(self):
        t = thread(comment(DECISION), comment("b", author=CLAUDE))
        self.assertEqual(cases(cc.check(snap(prs=[pr(120, threads=[t])]))), ["reply-in-unanswered-decision"])

    def test_without_claudes_login_an_unsigned_claude_reply_is_not_the_humans(self):
        # Review fix 2 on #176: as in decision-check.py, a reply is the human's only from the human's login.
        t = thread(comment(DECISION + "\n\n— Claude (reviewer)"), comment("b", author="hornof-claude"))
        s = snap(prs=[pr(120, threads=[t])], human="hornof")
        s["claude_login"] = None
        self.assertEqual(cases(cc.check(s)), ["reply-in-unanswered-decision"])
        t = thread(comment(DECISION + "\n\n— Claude (reviewer)"), comment("b", author="hornof"),
                   comment("On it.", author="hornof-claude"))
        s["prs"] = [pr(120, threads=[t])]
        self.assertEqual(cc.check(s), [])

    def test_the_message_no_longer_sends_the_text_to_a_pr_level_comment(self):
        t = thread(comment(DECISION), comment("In plain words.\n\n— Claude (orchestrator)"))
        found = cc.check(snap(prs=[pr(120, threads=[t])]))
        self.assertNotIn("PR-level", found[0]["problem"])

    def test_after_the_human_answers_anyone_may_reply(self):
        t = thread(comment(DECISION), comment("b", author=HUMAN), comment("On it.\n\n— Claude (orchestrator)"))
        self.assertEqual(cc.check(snap(prs=[pr(120, threads=[t])])), [])

    def test_an_untouched_decision_is_fine(self):
        self.assertEqual(cc.check(snap(prs=[pr(120, threads=[thread(comment(DECISION))])])), [])


class MergeWithOpenRequest(unittest.TestCase):
    """2026-11-20: PR #120 was called ready while the advisor's playbook comment was still unapplied."""

    def hand_off(self, n):
        return {"prs": [n], "merge": [n], "issues": [], "decisions": []}

    def test_an_unanswered_advisor_comment_holds_the_merge(self):
        p = pr(120, assignees=[HUMAN], comments=[comment("Please add the rule to the playbook." + ADVISOR,
                                                         author=HUMAN, at="2026-11-20T10:00:00Z")])
        found = cc.check(snap(board=[], prs=[p]), self.hand_off(120))
        self.assertEqual(cases(found), ["merge-with-open-request"])

    ASK_URL = "https://github.com/hum/proj/pull/120#issuecomment-901"

    def advisor_ask(self):
        return comment("Please add the rule to the playbook." + ADVISOR, author=HUMAN, at="2026-11-20T10:00:00Z",
                       url=self.ASK_URL)

    def test_a_later_reply_that_links_it_answers_it(self):
        p = pr(120, assignees=[HUMAN], comments=[
            self.advisor_ask(),
            comment("Done in abc1234, for %s.\n\n— Claude (orchestrator)" % self.ASK_URL, at="2026-11-20T10:05:00Z")])
        self.assertEqual(cc.check(snap(prs=[p]), self.hand_off(120)), [])

    def test_a_later_reply_that_quotes_it_answers_it(self):
        p = pr(120, assignees=[HUMAN], comments=[
            self.advisor_ask(),
            comment("> Please add the rule to the playbook.\n\nNot doing it: the rule is in the skill.\n\n"
                    "— Claude (orchestrator)", at="2026-11-20T10:05:00Z")])
        self.assertEqual(cc.check(snap(prs=[p]), self.hand_off(120)), [])

    def test_a_reply_that_doesnt_address_it_doesnt_answer_it(self):
        # #159: any later Claude comment counted, the reviewer's index post included.
        others = [
            "Index: 0 Fix, 2 Notes.\n\n## Notes\n1. fine, filed #9\n2. fine, filed #10\n\n— Claude (reviewer)",
            "Rebased on main.\n\n— Claude (orchestrator)",
            "> — Claude (advisor)\n\nSeen.\n\n— Claude (orchestrator)",  # quotes only the signature
            "> the\n\nSeen.\n\n— Claude (orchestrator)",  # a quote too short to say which comment
            "See https://github.com/hum/proj/pull/120#issuecomment-9012.\n\n— Claude (orchestrator)",
        ]
        for body in others:
            p = pr(120, assignees=[HUMAN], comments=[self.advisor_ask(), comment(body, at="2026-11-20T10:05:00Z")])
            self.assertEqual(cases(cc.check(snap(prs=[p]), self.hand_off(120))), ["merge-with-open-request"], body)

    def test_the_reviewer_linking_it_doesnt_answer_it(self):
        index = "Index: the advisor asked %s.\n\n— Claude (reviewer)" % self.ASK_URL
        p = pr(120, assignees=[HUMAN], comments=[self.advisor_ask(), comment(index, at="2026-11-20T10:05:00Z")])
        self.assertEqual(cases(cc.check(snap(prs=[p]), self.hand_off(120))), ["merge-with-open-request"])

    def test_a_reply_before_it_doesnt_answer_it(self):
        p = pr(120, assignees=[HUMAN], comments=[
            comment("> Please add the rule to the playbook.\n\nDone.\n\n— Claude (orchestrator)", at="2026-11-20T09:05:00Z"),
            self.advisor_ask()])
        self.assertEqual(cases(cc.check(snap(prs=[p]), self.hand_off(120))), ["merge-with-open-request"])

    def test_a_review_of_the_advisor_is_answered_by_its_link(self):
        url = "https://github.com/hum/proj/pull/120#pullrequestreview-77"
        p = pr(120, assignees=[HUMAN], reviews=[comment("Please rename it." + ADVISOR, author=HUMAN, url=url)],
               comments=[comment("Renamed, for %s.\n\n— Claude (orchestrator)" % url, at="2026-11-20T10:05:00Z")])
        self.assertEqual(cc.check(snap(prs=[p]), self.hand_off(120)), [])

    def test_an_advisor_comment_that_links_a_decision_is_a_request_like_any_other(self):
        # #169 removes #97's PR-level plain-words summary, so a link to a thread no longer excuses a comment.
        p = pr(120, assignees=[HUMAN], comments=[comment(
            "Decision 7 needs a third option: https://github.com/hum/proj/pull/120#discussion_r7" + ADVISOR,
            author=HUMAN)])
        self.assertEqual(cases(cc.check(snap(prs=[p]), self.hand_off(120))), ["merge-with-open-request"])

    def test_a_fix_item_with_no_commit_holds_the_merge(self):
        review = "Index: 2 Fix.\n\n## Fix\n1. Rename the flag. abc1234\n2. Add the test.\n\n## Notes\n1. fine, filed #9\n\n— Claude (reviewer)"
        p = pr(120, assignees=[HUMAN], comments=[comment(review)], commits=[OID])
        found = cc.check(snap(prs=[p]), self.hand_off(120))
        self.assertEqual(cases(found), ["merge-with-open-request"])
        self.assertIn("Fix item 2", found[0]["problem"])

    def test_every_fix_item_answered_is_fine(self):
        review = ("**Fix**\n1. Rename the flag. Fixed in abc1234.\n2. Add the test: not fixed, because the "
                  "hook has none.\n\n**Notes**\n1. fine, filed #9\n\n— Claude (reviewer)")
        p = pr(120, assignees=[HUMAN], reviews=[comment(review)], commits=[OID])
        self.assertEqual(cc.check(snap(prs=[p]), self.hand_off(120)), [])

    def fix_post(self, item):
        return "## Fix\n1. %s\n\n## Notes\n1. fine, filed #9\n\n— Claude (reviewer)" % item

    def test_a_fix_item_sha_must_be_a_commit_on_the_pr(self):
        # #159: any hex or digit word of 7 or more characters passed as the commit.
        for item in ["Rename the flag. Fixed in 1234567.", "Rename the flag, issue 20261122.",
                     "Rename the flag. Fixed in abc1235.", "Fixed in https://github.com/hum/proj/commit/fff0000"]:
            p = pr(120, assignees=[HUMAN], comments=[comment(self.fix_post(item))], commits=[OID])
            found = cc.check(snap(prs=[p]), self.hand_off(120))
            self.assertEqual(cases(found), ["merge-with-open-request"], item)
            self.assertIn("Fix item 1", found[0]["problem"])

    def test_a_commit_on_the_pr_in_any_form_answers_the_item(self):
        for item in ["Fixed in abc1234.", "Fixed in `%s`." % OID, "Fixed: %s" % OID[:12],
                     "Fixed in ABC1234.", "Fixed in https://github.com/hum/proj/commit/%s" % OID,
                     "Fixed in https://github.com/hum/proj/pull/120/commits/abc12345"]:
            p = pr(120, assignees=[HUMAN], comments=[comment(self.fix_post(item))], commits=["0" * 40, OID])
            self.assertEqual(cc.check(snap(prs=[p]), self.hand_off(120)), [], item)

    def test_a_reason_still_answers_the_item_with_no_commit(self):
        p = pr(120, assignees=[HUMAN], comments=[comment(self.fix_post("Add the test: won't fix, no hook."))])
        self.assertEqual(cc.check(snap(prs=[p]), self.hand_off(120)), [])

    def test_only_a_merge_hand_off_is_checked(self):
        p = pr(120, assignees=[HUMAN], comments=[comment("Please add it." + ADVISOR, author=HUMAN)])
        self.assertEqual(cc.check(snap(prs=[p])), [])


FIXTURE = os.path.join(HERE, "test_consistency_check_2026_11_22.json")


def real_prs():
    """PRs #161, #163 and #164 of hornof/apb as GitHub had them on 2026-11-22 (their comments, reviews,
    commits, and the comments on the cards they close), read with gh for #173."""
    with open(FIXTURE, encoding="utf-8") as f:
        return {p["number"]: p for p in json.load(f)["prs"]}


REAL_LOGIN = "hornof-claude"  # Claude's account on hornof/apb
LATER = "2026-11-22T23:00:00Z"


def real_snap(prs, merged=True):
    s = snap(prs=[] if merged else prs, human="hornof")
    s["repo"], s["claude_login"] = "hornof/apb", REAL_LOGIN
    if merged:
        s["merged"] = list(prs)
    return s


def reply(body, at=LATER, author=REAL_LOGIN, url="https://github.com/hornof/apb/pull/1#issuecomment-1"):
    return {"body": body + "\n\n— Claude (orchestrator)", "author": author, "createdAt": at, "url": url}


def loose(found):
    return [p for p in found if p["case"] == "loose-follow-up"]


class LooseFollowUp(unittest.TestCase):
    """2026-11-22: six follow-ups from reviews and advisor comments were left with no card (PR #161
    Note 2, PR #163 Note 6, PR #164's advisor comment among them), found only when the human asked
    "what am I missing?" (#173). When a turn hands over a merge, or notices one, every reviewer Note and
    every "file a card", "want a card?" or "say so" on the PR or the card it closes needs a card (#N) or
    a "no action" reply."""

    def test_the_2026_11_22_merges_are_sent_back(self):
        prs = real_prs()
        expect = {161: ("Note 2", "https://github.com/hornof/apb/pull/161#issuecomment-7083351023"),
                  163: ("Note 6", "https://github.com/hornof/apb/pull/163#issuecomment-7083676251"),
                  164: ("I'd file one card for them now", "https://github.com/hornof/apb/pull/164#issuecomment-7085356206")}
        for n, (what, url) in expect.items():
            for key, merged in (("merged", True), ("merge", False)):
                found = loose(cc.check(real_snap([prs[n]], merged), {key: [n]}))
                self.assertTrue(found, (n, key))
                said = [p["problem"] for p in found if what in p["problem"] and url in p["problem"]]
                self.assertEqual(len(said), 1, (n, key, [p["problem"] for p in found]))
                self.assertIn("PR #%d" % n, said[0])

    # The replies that would have settled each PR: a card for the item the issue names, "no action" for the rest.
    SETTLED = {
        161: ["Note 2: filed as #180.", "Note 3: no action, the default checklist is what the repo wants."],
        163: ["Notes 3 to 5: no action.", "Note 6: #181"],
        164: ["Filed #182 for the advisor's comment https://github.com/hornof/apb/pull/164#issuecomment-7085356206.",
              "Notes 4 and 5: no action, both are harmless."],
    }
    NO_ACTION = {
        161: ["Notes 2 and 3: no action."],
        163: ["Note 3: no action\nNote 4: no action\nNote 5: no action\nNote 6: no action, the script's line is the hand-off."],
        164: ["> I'd file one card for them now instead of waiting for the new message to show up on GitHub.\n\n"
              "No action: #136's own fix covers it.", "Notes 4, 5: no action."],
    }

    def settle(self, n, lines, on_card=False):
        p = json.loads(json.dumps(real_prs()[n]))
        replies = [reply(line) for line in lines]
        p["card_comments" if on_card else "comments"] += replies
        return p

    def test_the_same_prs_with_a_card_linked_pass(self):
        for n, lines in self.SETTLED.items():
            for key, merged in (("merged", True), ("merge", False)):
                found = cc.check(real_snap([self.settle(n, lines)], merged), {key: [n]})
                self.assertEqual(loose(found), [], (n, key))

    def test_the_same_prs_with_no_action_replied_pass(self):
        for n, lines in self.NO_ACTION.items():
            found = cc.check(real_snap([self.settle(n, lines)]), {"merged": [n]})
            self.assertEqual(loose(found), [], n)

    def test_a_reply_on_the_card_it_closes_counts(self):
        for n, lines in self.SETTLED.items():
            self.assertEqual(loose(cc.check(real_snap([self.settle(n, lines, on_card=True)]), {"merged": [n]})), [], n)

    def test_each_item_is_its_own(self):
        # Settling every item but one leaves exactly that one.
        p = self.settle(163, ["Notes 3 to 5: no action."])
        found = loose(cc.check(real_snap([p]), {"merged": [163]}))
        self.assertEqual(len(found), 1, [x["problem"] for x in found])
        self.assertIn("Note 6", found[0]["problem"])

    def test_only_a_merge_handed_over_or_noticed_is_checked(self):
        p = real_prs()[163]
        self.assertEqual(loose(cc.check(real_snap([p], merged=False))), [])
        self.assertEqual(loose(cc.check(real_snap([p], merged=False), {"prs": [163], "issues": [162]})), [])
        self.assertEqual(loose(cc.check(real_snap([p]), {"merged": [999]})), [])

    def test_an_open_pr_said_to_be_merged_is_not_a_merge_noticed(self):
        # Review fix 1 on #179: "PR #179 is ready to be merged." names an open PR; only s["merged"] counts.
        post = comment("## Notes\n1. Slow.", url="https://github.com/hum/proj/pull/179#issuecomment-5")
        p = pr(179, closes=[173], comments=[post])
        self.assertEqual(loose(cc.check(snap(prs=[p]), {"merged": [179]})), [])
        # The same PR, merged, is checked; and asked to merge, it is checked as a merge handed over.
        s = snap()
        s["merged"] = [p]
        self.assertEqual(len(loose(cc.check(s, {"merged": [179]}))), 1)
        self.assertIn("hands over the merge of PR #179",
                      loose(cc.check(snap(prs=[p]), {"merge": [179]}))[0]["problem"])

    # ------------------------------------------------ made-up posts, for each rule on its own

    URL = "https://github.com/hum/proj/pull/120#issuecomment-500"

    def notes_post(self, *items, at="2026-11-20T10:00:00Z", author=CLAUDE):
        body = "## Fix items\n\n1. Rename it. Fixed in abc1234.\n\n## Notes\n\n" + "\n".join(
            "%d. %s" % (k, text) for k, text in items)
        return comment(body, author=author, at=at, url=self.URL)

    def found_for(self, comments=(), card_comments=(), reviews=()):
        p = pr(120, closes=[7], comments=comments, reviews=reviews, commits=[OID])
        p["card_comments"] = list(card_comments)
        return loose(cc.check(snap(prs=[p]), {"prs": [120], "merge": [120]}))

    def test_a_note_that_links_a_new_card_is_settled(self):
        for text in ["Slow on big boards; filed #44.", "See https://github.com/hum/proj/issues/44 for it.",
                     "Slow on big boards (#44)."]:
            self.assertEqual(self.found_for([self.notes_post((2, text))]), [], text)

    def test_a_note_that_names_only_the_pr_its_card_or_another_pr_is_loose(self):
        for text in ["Slow on big boards, as #7 says.", "Slow, like #120 itself.", "Slow, as on PR #44.",
                     "Slow, see https://github.com/hum/proj/pull/44.", "Slow, see `#44`.", "Slow, see &#44; here."]:
            found = self.found_for([self.notes_post((2, text))])
            self.assertEqual(len(found), 1, text)
            self.assertIn("Note 2", found[0]["problem"])
            self.assertIn(self.URL, found[0]["problem"])

    def test_no_action_in_the_note_itself_is_not_a_reply(self):
        found = self.found_for([self.notes_post((2, "Harmless; no action needed."))])
        self.assertEqual(len(found), 1)

    def test_a_reply_settles_only_the_notes_on_its_line(self):
        later = comment("Note 2: no action.\nNote 3: looking into it.", at="2026-11-20T11:00:00Z")
        found = self.found_for([self.notes_post((2, "a"), (3, "b")), later])
        self.assertEqual([("Note 3" in p["problem"]) for p in found], [True])

    def test_a_reply_before_the_note_doesnt_settle_it(self):
        earlier = comment("Note 2: no action.", at="2026-11-20T09:00:00Z")
        self.assertEqual(len(self.found_for([earlier, self.notes_post((2, "a"))])), 1)

    def test_a_reply_with_no_card_and_no_no_action_doesnt_settle_it(self):
        for body in ["Note 2: seen.", "Note 2: see #7.", "Note 2: see PR #44.", "Notes: no action.",
                     "Note 12: no action.", "No action on any of it.",
                     # Review fix 2 on #179: "no card" is a status, not a decision.
                     "Notes 2-3: no card, harmless.", "Notes 2 and 6 still have no card.",
                     "Note 2 has no card yet; filing after merge."]:
            later = comment(body, at="2026-11-20T11:00:00Z")
            self.assertEqual(len(self.found_for([self.notes_post((2, "a")), later])), 1, body)

    def test_reply_forms_that_settle_a_note(self):
        for body in ["Note 2: no action.", "note 2 - #45", "**Note 2:** filed #45.", "Notes 1 to 3: no action.",
                     "Notes 1, 2 and 3: no action.", "Notes 2-3: no action, harmless.",
                     "Note 2: https://github.com/hum/proj/issues/45"]:
            later = comment(body, at="2026-11-20T11:00:00Z", author=HUMAN)
            self.assertEqual(self.found_for([self.notes_post((2, "a")), later]), [], body)

    def test_bold_numbered_notes_are_items(self):
        body = "## Notes\n\n**3.** First.\n\n**4.** Second **with bold**.\n\n## Decisions\n\n7. Not a note."
        found = self.found_for([comment(body, url=self.URL)])
        self.assertEqual(sorted(p["problem"].split("Note ")[1].split()[0] for p in found), ["3", "4"])

    def test_a_notes_heading_in_bold_and_a_review_body(self):
        body = "**Notes**\n1. One.\n\n— Claude (reviewer)"
        self.assertEqual(len(self.found_for(reviews=[comment(body, url=self.URL)])), 1)

    def test_notes_from_the_human_or_the_advisor_are_not_reviewer_notes(self):
        self.assertEqual(self.found_for([self.notes_post((2, "a"), author=HUMAN)]), [])
        advisor = comment("## Notes\n1. One." + ADVISOR, author=HUMAN, url=self.URL)
        self.assertEqual(self.found_for([advisor]), [])

    def test_a_post_with_no_notes_section_has_no_notes(self):
        body = "Index: 1 Fix, 2 Notes (3 to 4), 0 Decisions.\n\n1. one\n2. two\n\n— Claude (reviewer)"
        self.assertEqual(self.found_for([comment(body, url=self.URL)]), [])

    REQUESTS = ["Want a card for this?", "If you want a card, say so.", "Say so and I'll file it.",
                "I'd file one card for them now.", "File a card now.", "We should **file a** card for it.",
                "Do you want a card?", "Say so if you want a separate card."]
    NOT_REQUESTS = ["The PR says so.", "If it's still blocked, the orchestrator should say so in the hand-off.",
                    "> File a card now.", "The rule is `file a card`.", "File a card now: #45.",
                    "No action: we won't file a card.", "Seen."]

    def test_what_counts_as_asking_for_a_card(self):
        for text in self.REQUESTS:
            for where in ("comments", "card_comments"):
                c = comment(text + ADVISOR, author=HUMAN, url=self.URL)
                found = self.found_for(**{where: [c]})
                self.assertEqual(len(found), 1, (text, where))
                self.assertIn(self.URL, found[0]["problem"])
        for text in self.NOT_REQUESTS:
            c = comment(text + ADVISOR, author=HUMAN, url=self.URL)
            self.assertEqual(self.found_for([c]), [], text)

    def test_a_request_is_settled_by_a_later_reply_that_addresses_it(self):
        ask = comment("Want a card for this?" + ADVISOR, author=HUMAN, url=self.URL)
        for body in ["Filed #45 for %s." % self.URL, "> Want a card for this?\n\nNo action: harmless.",
                     "No card for %s: no action." % self.URL]:
            for where in ("comments", "card_comments"):
                later = comment(body, at="2026-11-20T11:00:00Z")
                kw = {"comments": [ask]}
                kw[where] = kw.get(where, []) + [later]
                self.assertEqual(self.found_for(**kw), [], (body, where))

    def test_a_reply_that_doesnt_address_the_request_or_says_nothing_doesnt_settle_it(self):
        ask = comment("Want a card for this?" + ADVISOR, author=HUMAN, url=self.URL)
        for body, at in [("Filed #45.", "2026-11-20T11:00:00Z"),  # doesn't say for what
                         ("Seen %s." % self.URL, "2026-11-20T11:00:00Z"),  # no card, no "no action"
                         ("Filed #45 for %s." % self.URL, "2026-11-20T09:00:00Z"),  # before it
                         ("Filed #7 for %s." % self.URL, "2026-11-20T11:00:00Z")]:  # the PR's own card
            later = comment(body, at=at)
            self.assertEqual(len(self.found_for([ask, later])), 1, body)

    def test_a_request_inside_a_note_is_named_once_as_the_note(self):
        found = self.found_for([self.notes_post((2, "Slow on big boards. Want a card?"))])
        self.assertEqual(len(found), 1)
        self.assertIn("Note 2", found[0]["problem"])

    def test_the_problem_says_how_to_settle_it(self):
        found = self.found_for([self.notes_post((2, "a"))])
        self.assertIn("#<card>", found[0]["problem"])
        self.assertIn("no action", found[0]["problem"])


class LooseFollowUpProperties(unittest.TestCase):
    """Random Notes posts and replies: a Note is named exactly when an independent bookkeeping of what
    was settled (written as the replies are generated, not read back by the check's rules) says it is
    loose. Both directions: a settled Note is never named, a loose one always is."""

    def test_against_the_bookkeeping(self):
        rng = random.Random(173)
        url = "https://github.com/hum/proj/pull/120#issuecomment-500"
        for _ in range(300):
            numbers = sorted(rng.sample(range(1, 12), rng.randint(1, 5)))
            settled = set()
            items = []
            for k in numbers:
                if rng.random() < 0.2:
                    items.append("%d. Slow on big boards; filed #%d." % (k, 200 + k))
                    settled.add(k)
                else:
                    items.append("%d. Slow on big boards, as #7 and PR #3 say." % k)
            post = comment("## Notes\n\n" + "\n".join(items), at="2026-11-20T10:00:00Z", url=url)
            replies = []
            for k in numbers:
                roll = rng.random()
                if roll < 0.25:
                    replies.append(comment("Note %d: no action." % k, at="2026-11-20T11:00:00Z", author=HUMAN))
                    settled.add(k)
                elif roll < 0.45:
                    replies.append(comment("Note %d: #%d" % (k, 300 + k), at="2026-11-20T11:00:00Z"))
                    settled.add(k)
                elif roll < 0.55:  # too early
                    replies.append(comment("Note %d: no action." % k, at="2026-11-20T09:00:00Z"))
                elif roll < 0.65:  # no disposition
                    replies.append(comment("Note %d: seen, see #7." % k, at="2026-11-20T11:00:00Z"))
                elif roll < 0.75:  # another number
                    replies.append(comment("Note %d: no action." % (k + 20), at="2026-11-20T11:00:00Z"))
            if len(numbers) > 1 and rng.random() < 0.3:
                lo, hi = numbers[0], numbers[-1]
                replies.append(comment("Notes %d to %d: no action." % (lo, hi), at="2026-11-20T12:00:00Z"))
                settled |= set(range(lo, hi + 1))
            rng.shuffle(replies)
            p = pr(120, closes=[7], comments=[post] + replies)
            found = loose(cc.check(snap(prs=[p]), {"merge": [120]}))
            named = {int(x["problem"].split("Note ")[1].split()[0]) for x in found}
            self.assertEqual(named, set(numbers) - settled, (items, [r["body"] for r in replies]))


class PlaybookListsEveryCase(unittest.TestCase):
    def test_every_case_is_in_the_playbook(self):
        with open(os.path.join(os.path.dirname(HERE), "docs", "playbook.md"), encoding="utf-8") as f:
            playbook = f.read()
        for case in cc.CASES:
            self.assertIn("**%s:**" % case["id"], playbook, case["id"])


class NoAdvisorSummaryCase(unittest.TestCase):
    """#169 removes #97's rule: the advisor no longer posts a PR-level plain-words comment for each
    Decision, so nothing checks for one."""

    def test_the_case_is_gone(self):
        self.assertNotIn("decision-without-advisor-comment", [c["id"] for c in cc.CASES])

    def test_a_decision_hand_off_needs_no_advisor_comment(self):
        p = pr(120, assignees=[HUMAN], threads=[thread(comment(DECISION, at="2026-11-21T10:00:00Z"))],
               comments=[comment("notes" + ADVISOR, author=HUMAN, at="2026-11-21T09:00:00Z")])
        hand_off = {"prs": [120], "merge": [], "issues": [], "decisions": [120]}
        self.assertEqual(cc.check(snap(prs=[p]), hand_off), [])


def oracle_column(c, prs, worktrees):
    """The one column a card belongs in, or a set of columns that are all fine, worked out separately."""
    if c["state"] == "CLOSED":
        return {"done"}
    mine = [p for p in prs if c["number"] in p["closes"] or p["head"].split("-")[:2] == ["issue", str(c["number"])]]
    if any(c["number"] in p["closes"] and not p["draft"] for p in mine):
        return {"in review"}
    if any(c["number"] in p["closes"] for p in mine):
        return {"in progress"}  # its PRs are all drafts (#156)
    if mine or any(w.split("-")[:2] == ["issue", str(c["number"])] for w in worktrees):
        return {"in progress", "in review"}
    return {"todo", "in progress", "in review"}


class BoardProperties(unittest.TestCase):
    """Random boards: a card is named by a board case exactly when the oracle says its column is wrong."""

    def test_against_the_oracle(self):
        rng = random.Random(121)
        statuses = ["todo", "in progress", "in review", "done"]
        for _ in range(400):
            numbers = rng.sample(range(1, 60), rng.randint(1, 8))
            board = [card(n, rng.choice(statuses), rng.choice(["OPEN", "CLOSED"])) for n in numbers]
            prs, worktrees = [], []
            for k, n in enumerate(numbers):
                roll = rng.random()
                if roll < 0.3:
                    prs.append(pr(100 + k, closes=[n], draft=rng.random() < 0.5,
                                  head=rng.choice(["issue-%d-x" % n, "other-%d" % k])))
                elif roll < 0.45:
                    worktrees.append(rng.choice(["issue-%d-y" % n, "issue-%d" % n, "issue-%d9-z" % n]))
            found = cc.check(snap(board=board, prs=prs, worktrees=worktrees))
            named = {p["card"] for p in found if p["case"] in ("build-in-todo", "pr-ready-not-in-review",
                                                               "in-review-with-draft-pr", "board-vs-issue")}
            expected = {c["number"] for c in board if c["status"] not in oracle_column(c, prs, worktrees)}
            self.assertEqual(named, expected, (board, prs, worktrees, found))


# ---------------------------------------------------------------- from GitHub's answer

def graphql_answer(items, prs, assigned=(), has_next=False, cursor=None):
    return {"data": {
        "repository": {
            "owner": {"__typename": "User", "login": HUMAN},
            "projectsV2": {"nodes": [{"items": {"pageInfo": {"hasNextPage": has_next, "endCursor": cursor},
                                                "nodes": items}}]},
            "pullRequests": {"nodes": prs}},
        "assigned": {"nodes": list(assigned)}}}


def gql_item(n, status, state="OPEN", assignees=()):
    return {"fieldValueByName": {"name": status},
            "content": {"number": n, "state": state, "assignees": {"nodes": [{"login": a} for a in assignees]}}}


def gql_pr(n, closes=(), draft=False, head="issue-1-x", commits=()):
    return {"number": n, "isDraft": draft, "headRefName": head, "assignees": {"nodes": []},
            "commits": {"nodes": [{"commit": {"oid": c}} for c in commits]},
            "closingIssuesReferences": {"nodes": [{"number": c} for c in closes]},
            "reviewThreads": {"nodes": [{"isResolved": True, "comments": {"nodes": [
                {"body": DECISION, "author": {"login": CLAUDE}, "createdAt": "2026-11-20T10:00:00Z",
                 "url": "https://github.com/hum/proj/pull/%d#discussion_r1" % n}]}}]},
            "comments": {"nodes": []}, "reviews": {"nodes": []}}


class FromGitHub(unittest.TestCase):
    def test_the_answer_becomes_a_snapshot(self):
        answer = graphql_answer([gql_item(1, "Todo"), gql_item(2, "In progress", assignees=[HUMAN]), {"content": {}}],
                                [gql_pr(5, closes=[2], draft=True, head="issue-2-x")])
        s = cc.from_answers([answer], REPO, CLAUDE, ["issue-2-x"])
        self.assertEqual(s["human"], HUMAN)
        self.assertEqual([(c["number"], c["status"]) for c in s["board"]], [(1, "todo"), (2, "in progress")])
        self.assertEqual(s["board"][1]["assignees"], [HUMAN])
        self.assertEqual(s["prs"][0]["closes"], [2])
        self.assertTrue(s["prs"][0]["draft"])
        self.assertEqual(s["prs"][0]["threads"][0]["comments"][0]["author"], CLAUDE)
        self.assertEqual(s["prs"][0]["commits"], [])
        self.assertEqual(s["agents"], {})
        # The resolved Decision with no confirmation is found from GitHub's answer too.
        self.assertEqual(cases(cc.check(s)), ["resolved-decision-unconfirmed"])

    def test_commits_and_agents(self):
        answer = graphql_answer([], [gql_pr(5, commits=[OID])])
        s = cc.from_answers([answer], REPO, CLAUDE, [], agents={"a1": [3]})
        self.assertEqual(s["prs"][0]["commits"], [OID])
        self.assertEqual(s["agents"], {"a1": [3]})

    def test_merged_prs_and_the_comments_on_the_cards_a_pr_closes(self):
        # #173: a merge the turn notices is read from the recently merged PRs, with its cards' comments.
        open_pr = gql_pr(5, closes=[2])
        open_pr["closingIssuesReferences"]["nodes"][0]["comments"] = {"nodes": [
            {"body": "Want a card?", "author": {"login": HUMAN}, "createdAt": "2026-11-22T10:00:00Z",
             "url": "https://github.com/hum/proj/issues/2#issuecomment-3"}]}
        merged = gql_pr(6, closes=[4])
        merged["comments"] = {"nodes": [{"body": "## Notes\n1. Slow.", "author": {"login": CLAUDE},
                                         "createdAt": "2026-11-22T10:00:00Z",
                                         "url": "https://github.com/hum/proj/pull/6#issuecomment-9"}]}
        answer = graphql_answer([], [open_pr])
        answer["data"]["repository"]["merged"] = {"nodes": [merged]}
        s = cc.from_answers([answer], REPO, CLAUDE, [])
        self.assertEqual([c["body"] for c in s["prs"][0]["card_comments"]], ["Want a card?"])
        self.assertEqual([p["number"] for p in s["merged"]], [6])
        self.assertEqual(s["merged"][0]["card_comments"], [])
        found = cc.check(s, {"merged": [6], "merge": [5]})
        self.assertEqual(sorted(p["problem"].split(", but ")[0] for p in loose(found)),
                         ["this turn hands over the merge of PR #5", "this turn notices PR #6 merged"])
        # An answer from before #173, with neither, reads as none.
        s = cc.from_answers([graphql_answer([], [gql_pr(5, closes=[2])])], REPO, CLAUDE, [])
        self.assertEqual((s["merged"], s["prs"][0]["card_comments"]), ([], []))

    def test_the_query_asks_for_merged_prs_and_card_comments(self):
        self.assertIn("merged: pullRequests(states: MERGED", cc.QUERY)
        self.assertRegex(cc.QUERY, r"closingIssuesReferences\(first: \d+\) \{ nodes \{ number comments\(")

    def test_the_query_asks_for_each_prs_commits(self):
        self.assertIn("commits(", cc.QUERY)
        self.assertIn("oid", cc.QUERY)

    def test_an_organization_has_no_human(self):
        answer = graphql_answer([], [])
        answer["data"]["repository"]["owner"]["__typename"] = "Organization"
        self.assertIsNone(cc.from_answers([answer], REPO, CLAUDE, [])["human"])

    def test_no_board(self):
        answer = graphql_answer([], [])
        answer["data"]["repository"]["projectsV2"]["nodes"] = []
        self.assertEqual(cc.from_answers([answer], REPO, CLAUDE, [])["board"], [])

    def test_worktree_branches(self):
        folder = tempfile.mkdtemp()
        subprocess.run(["git", "init", "-q", "-b", "main", folder], check=True, timeout=30)
        subprocess.run(["git", "-C", folder, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q",
                        "--allow-empty", "-m", "x"], check=True, timeout=30)
        wt = os.path.join(tempfile.mkdtemp(), "wt")
        subprocess.run(["git", "-C", folder, "worktree", "add", "-q", "-b", "issue-7-thing", wt], check=True, timeout=30)
        self.assertEqual(sorted(cc.worktree_branches(folder)), ["issue-7-thing", "main"])
        self.assertEqual(cc.worktree_branches(os.path.join(folder, "missing")), [])


# ---------------------------------------------------------------- the script, run for real

def fake_gh(folder, pages, fail=False):
    """A gh that answers each `gh api graphql` with the next page in pages, or fails."""
    for i, page in enumerate(pages):
        with open(os.path.join(folder, "page-%d.json" % i), "w") as f:
            json.dump(page, f)
    calls = os.path.join(folder, "calls.txt")
    count = os.path.join(folder, "count")
    with open(count, "w") as f:
        f.write("0")
    gh = os.path.join(folder, "gh")
    with open(gh, "w") as f:
        f.write("#!/bin/sh\nprintf '%%s\\n' \"$*\" >> '%s'\n" % calls)
        if fail:
            f.write("echo 'HTTP 502' >&2\nexit 1\n")
        else:
            f.write("n=$(cat '%s'); echo $((n+1)) > '%s'\ncat '%s'/page-$n.json\n" % (count, count, folder))
    os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
    return calls


class Script(unittest.TestCase):
    def run_script(self, pages, fail=False, extra=()):
        folder = tempfile.mkdtemp()
        calls = fake_gh(folder, pages, fail)
        env = dict(os.environ, PATH=folder + os.pathsep + os.environ["PATH"])
        r = subprocess.run([sys.executable, SCRIPT, "--repo", REPO, "--claude-login", CLAUDE,
                            "--project", folder] + list(extra),
                           capture_output=True, text=True, env=env, timeout=60)
        with open(calls) as f:
            return r, f.read()

    def test_problems_exit_1_and_name_the_case(self):
        r, calls = self.run_script([graphql_answer([gql_item(3, "Done", "OPEN")], [])])
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("board-vs-issue", r.stdout)
        self.assertIn("#3", r.stdout)
        self.assertIn("api graphql", calls)
        self.assertIn("repo:hum/proj assignee:hum", calls)

    def test_all_consistent_exits_0(self):
        r, _ = self.run_script([graphql_answer([gql_item(3, "Done", "CLOSED")], [])])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("consistent", r.stdout.lower())

    def test_reads_every_page_of_the_board(self):
        first = graphql_answer([gql_item(1, "Todo")], [], has_next=True, cursor="C1")
        second = graphql_answer([gql_item(2, "Done", "OPEN")], [])
        r, calls = self.run_script([first, second])
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertIn("#2", r.stdout)
        self.assertIn("after=C1", calls)

    def test_a_github_error_is_no_problem(self):
        r, _ = self.run_script([], fail=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("couldn't run", r.stderr)


class Budget(unittest.TestCase):
    """Review fix 2 on #157: the git worktree list comes out of the check's budget, never on top of it."""

    def fetch_with_slow_gh(self, budget, delay):
        folder = tempfile.mkdtemp()
        with open(os.path.join(folder, "page.json"), "w") as f:
            json.dump(graphql_answer([], []), f)
        gh = os.path.join(folder, "gh")
        with open(gh, "w") as f:
            f.write("#!/bin/sh\nsleep %s\ncat '%s/page.json'\n" % (delay, folder))
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        seen = []
        real = cc.worktree_branches
        cc.worktree_branches = lambda project, time_limit=5: seen.append(time_limit) or []
        try:
            return cc.fetch(REPO, folder, CLAUDE, gh=gh, budget=budget, time_limit=budget), seen
        finally:
            cc.worktree_branches = real

    def test_the_worktree_call_gets_only_what_is_left(self):
        _, seen = self.fetch_with_slow_gh(budget=3, delay=1)
        self.assertEqual(len(seen), 1)
        self.assertLessEqual(seen[0], 2.05)
        self.assertGreater(seen[0], 0)

    def test_no_time_left_is_an_error_not_an_overrun(self):
        with self.assertRaises((cc.OutOfTime, subprocess.TimeoutExpired)):
            self.fetch_with_slow_gh(budget=0.5, delay=1)


class TimeLimits(unittest.TestCase):
    def test_every_child_has_a_time_limit(self):
        with open(SCRIPT, encoding="utf-8") as f:
            source = f.read()
        calls = source.count("subprocess.run(")
        self.assertGreater(calls, 0)
        self.assertEqual(calls, source.count("timeout="))


if __name__ == "__main__":
    unittest.main()
