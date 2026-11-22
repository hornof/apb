"""Tests for consistency-check.py, the end-of-turn check (#121). Run from the repo root:
python3 -m unittest scripts/test_consistency_check.py

Each case of the check has a test built from the incident that added it (CASES names the incident).
The board cases are also checked with random boards against a second, simpler rule (an oracle that
works out each card's one right column from its issue, its PRs and its worktrees), in both directions:
a card in the right column is never named, a card in a wrong one always is. The whole script is then
run for real, with a fake `gh` on the PATH, to check what it asks GitHub, what it prints and how it exits,
and that a GitHub error never counts as a problem.
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


def pr(n, closes=(), draft=False, head=None, assignees=(), threads=(), comments=(), reviews=()):
    return {"number": n, "draft": draft, "head": head or "issue-%d-x" % (closes[0] if closes else n),
            "assignees": list(assignees), "closes": list(closes), "threads": list(threads),
            "comments": list(comments), "reviews": list(reviews)}


def snap(board=(), prs=(), worktrees=(), assigned=(), advisor=False, human=HUMAN):
    return {"repo": REPO, "human": human, "claude_login": CLAUDE, "board": list(board), "prs": list(prs),
            "worktrees": list(worktrees), "assigned": list(assigned), "advisor": advisor}


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

    def test_the_advisor_counts_as_claude(self):
        t = thread(comment(DECISION), comment("Plain words." + ADVISOR, author=HUMAN))
        self.assertEqual(cases(cc.check(snap(prs=[pr(120, threads=[t])]))), ["reply-in-unanswered-decision"])

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

    def test_a_later_reply_from_claude_answers_it(self):
        p = pr(120, assignees=[HUMAN], comments=[
            comment("Please add the rule to the playbook." + ADVISOR, author=HUMAN, at="2026-11-20T10:00:00Z"),
            comment("Done in abc1234.\n\n— Claude (orchestrator)", at="2026-11-20T10:05:00Z")])
        self.assertEqual(cc.check(snap(prs=[p]), self.hand_off(120)), [])

    def test_a_plain_words_summary_is_no_request(self):
        p = pr(120, assignees=[HUMAN], comments=[comment(
            "Decision 7 in plain words: https://github.com/hum/proj/pull/120#discussion_r7" + ADVISOR, author=HUMAN)])
        self.assertEqual(cc.check(snap(prs=[p]), self.hand_off(120)), [])

    def test_a_fix_item_with_no_commit_holds_the_merge(self):
        review = "Index: 2 Fix.\n\n## Fix\n1. Rename the flag. abc1234\n2. Add the test.\n\n## Notes\n1. fine\n\n— Claude (reviewer)"
        p = pr(120, assignees=[HUMAN], comments=[comment(review)])
        found = cc.check(snap(prs=[p]), self.hand_off(120))
        self.assertEqual(cases(found), ["merge-with-open-request"])
        self.assertIn("Fix item 2", found[0]["problem"])

    def test_every_fix_item_answered_is_fine(self):
        review = ("**Fix**\n1. Rename the flag. Fixed in abc1234.\n2. Add the test: not fixed, because the "
                  "hook has none.\n\n**Notes**\n1. fine\n\n— Claude (reviewer)")
        p = pr(120, assignees=[HUMAN], reviews=[comment(review)])
        self.assertEqual(cc.check(snap(prs=[p]), self.hand_off(120)), [])

    def test_only_a_merge_hand_off_is_checked(self):
        p = pr(120, assignees=[HUMAN], comments=[comment("Please add it." + ADVISOR, author=HUMAN)])
        self.assertEqual(cc.check(snap(prs=[p])), [])


class AdvisorComment(unittest.TestCase):
    """2026-11-21: a new advisor session skipped the PR-level plain-words comment on a Decision."""

    def hand_off(self):
        return {"prs": [120], "merge": [], "issues": [], "decisions": [120]}

    def test_a_decision_hand_off_needs_the_advisors_comment(self):
        p = pr(120, assignees=[HUMAN], threads=[thread(comment(DECISION, at="2026-11-21T10:00:00Z"))])
        found = cc.check(snap(prs=[p], advisor=True), self.hand_off())
        self.assertEqual(cases(found), ["decision-without-advisor-comment"])

    def test_the_advisors_comment_after_the_decision_clears_it(self):
        p = pr(120, assignees=[HUMAN], threads=[thread(comment(DECISION, at="2026-11-21T10:00:00Z"))],
               comments=[comment("Decision 6 is already plain." + ADVISOR, author=HUMAN, at="2026-11-21T10:10:00Z")])
        self.assertEqual(cc.check(snap(prs=[p], advisor=True), self.hand_off()), [])

    def test_an_advisor_comment_from_before_the_decision_does_not_count(self):
        p = pr(120, assignees=[HUMAN], threads=[thread(comment(DECISION, at="2026-11-21T10:00:00Z"))],
               comments=[comment("Looks fine." + ADVISOR, author=HUMAN, at="2026-11-21T09:00:00Z")])
        self.assertIn("decision-without-advisor-comment", cases(cc.check(snap(prs=[p], advisor=True), self.hand_off())))

    def test_a_project_without_an_advisor_is_not_checked(self):
        p = pr(120, assignees=[HUMAN], threads=[thread(comment(DECISION))])
        self.assertEqual(cc.check(snap(prs=[p], advisor=False), self.hand_off()), [])


def oracle_column(c, prs, worktrees):
    """The one column a card belongs in, or a set of columns that are all fine, worked out separately."""
    if c["state"] == "CLOSED":
        return {"done"}
    mine = [p for p in prs if c["number"] in p["closes"] or p["head"].split("-")[:2] == ["issue", str(c["number"])]]
    if any(c["number"] in p["closes"] and not p["draft"] for p in mine):
        return {"in review"}
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
            named = {p["card"] for p in found if p["case"] in ("build-in-todo", "pr-ready-not-in-review", "board-vs-issue")}
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


def gql_pr(n, closes=(), draft=False, head="issue-1-x"):
    return {"number": n, "isDraft": draft, "headRefName": head, "assignees": {"nodes": []},
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
        # The resolved Decision with no confirmation is found from GitHub's answer too.
        self.assertEqual(cases(cc.check(s)), ["resolved-decision-unconfirmed"])

    def test_an_organization_has_no_human(self):
        answer = graphql_answer([], [])
        answer["data"]["repository"]["owner"]["__typename"] = "Organization"
        self.assertIsNone(cc.from_answers([answer], REPO, CLAUDE, [])["human"])

    def test_no_board(self):
        answer = graphql_answer([], [])
        answer["data"]["repository"]["projectsV2"]["nodes"] = []
        self.assertEqual(cc.from_answers([answer], REPO, CLAUDE, [])["board"], [])

    def test_a_signed_advisor_comment_anywhere_shows_an_advisor(self):
        p = gql_pr(5)
        p["comments"]["nodes"] = [{"body": "x" + ADVISOR, "author": {"login": HUMAN},
                                   "createdAt": "2026-11-20T10:00:00Z", "url": "u"}]
        self.assertTrue(cc.from_answers([graphql_answer([], [p])], REPO, CLAUDE, [])["advisor"])
        self.assertFalse(cc.from_answers([graphql_answer([], [gql_pr(5)])], REPO, CLAUDE, [])["advisor"])
        self.assertTrue(cc.from_answers([graphql_answer([], [])], REPO, CLAUDE, [], ["notes" + ADVISOR])["advisor"])

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
