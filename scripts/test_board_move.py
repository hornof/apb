"""Tests for board-move.py (#121): one command does both halves of a pair that must happen together.
Run from the repo root: python3 -m unittest scripts/test_board_move.py

A fake board stands in for GitHub: it answers the script's GraphQL queries and applies its mutations.
Random boards check, against the board's own state afterwards (not against the calls made), that the
moved card is in its new column, at the top of it, and that no other card changed column or order.
Both directions: start-build then pr-ready, and refusals that must leave the board untouched.
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
SCRIPT = os.path.join(HERE, "board-move.py")

spec = importlib.util.spec_from_file_location("board_move", SCRIPT)
bm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bm)

COLUMNS = ["Todo", "In progress", "In review", "Done"]


class FakeBoard:
    """A board with items in order: [{"id", "issue", "status"}]; issues: {n: state}; prs: {n: {...}}."""

    def __init__(self, items, issues=None, prs=None, on_board=True):
        self.items = [dict(i) for i in items]
        self.issues = issues or {i["issue"]: "OPEN" for i in items}
        self.prs = prs or {}
        self.on_board = on_board
        self.calls = []

    def status(self, n):
        return next(i["status"] for i in self.items if i["issue"] == n)

    def column(self, name):
        return [i["issue"] for i in self.items if i["status"] == name]

    def __call__(self, query, **fields):
        self.calls.append((query, fields))
        if "updateProjectV2ItemFieldValue" in query:
            assert fields["project"] == "P1" and fields["field"] == "F1"
            item = next(i for i in self.items if i["id"] == fields["item"])
            item["status"] = COLUMNS[int(fields["option"][1:])]
            return {"data": {"updateProjectV2ItemFieldValue": {"clientMutationId": None}}}
        if "updateProjectV2ItemPosition" in query:
            assert "afterId" not in query
            item = next(i for i in self.items if i["id"] == fields["item"])
            self.items.remove(item)
            self.items.insert(0, item)
            return {"data": {"updateProjectV2ItemPosition": {"clientMutationId": None}}}
        repo = {"projectsV2": {"nodes": [{"id": "P1", "field": {"id": "F1", "options": [
            {"id": "O%d" % k, "name": c} for k, c in enumerate(COLUMNS)]}}]}}
        if "pullRequest(" in query:
            p = self.prs.get(fields["number"])
            repo["pullRequest"] = None if p is None else {
                "state": p["state"], "isDraft": p["draft"],
                "closingIssuesReferences": {"nodes": [{"number": c} for c in p["closes"]]}}
            return {"data": {"repository": repo}}
        n = fields["number"]
        if n not in self.issues:
            repo["issue"] = None
        else:
            mine = [i for i in self.items if i["issue"] == n] if self.on_board else []
            repo["issue"] = {"state": self.issues[n], "projectItems": {"nodes": [
                {"id": "OTHER", "project": {"id": "P9"}, "fieldValueByName": {"name": "Done"}}]
                + [{"id": i["id"], "project": {"id": "P1"}, "fieldValueByName": {"name": i["status"]}} for i in mine]}}
        return {"data": {"repository": repo}}


def board(*pairs):
    return FakeBoard([{"id": "I%d" % n, "issue": n, "status": s} for n, s in pairs])


class StartBuild(unittest.TestCase):
    """2026-11-20: #117 and #118 were started with their cards left in Todo. Starting a build moves the card."""

    def test_moves_the_card_to_the_top_of_in_progress(self):
        b = board((1, "In progress"), (117, "Todo"), (118, "Todo"))
        out = bm.start_build("hum/proj", 117, b)
        self.assertEqual(b.status(117), "In progress")
        self.assertEqual(b.column("In progress"), [117, 1])
        self.assertEqual(b.column("Todo"), [118])
        self.assertIn("#117", out)
        self.assertIn("builder", out)

    def test_a_closed_card_is_refused_and_nothing_moves(self):
        b = board((5, "Done"))
        b.issues[5] = "CLOSED"
        with self.assertRaises(bm.Refused):
            bm.start_build("hum/proj", 5, b)
        self.assertEqual(b.status(5), "Done")
        self.assertFalse(any("mutation" in q for q, _ in b.calls))

    def test_a_card_not_on_the_board_is_refused(self):
        b = board((5, "Todo"))
        b.on_board = False
        with self.assertRaises(bm.Refused):
            bm.start_build("hum/proj", 5, b)
        with self.assertRaises(bm.Refused):
            bm.start_build("hum/proj", 99, b)

    def test_a_card_in_review_or_done_is_refused_and_nothing_moves(self):
        # #159: start-build refused only a closed card, so a card already In review went back to In
        # progress. A card past In progress never goes back by start-build.
        for column in ("In review", "Done"):
            b = board((1, "In progress"), (5, column), (6, column))
            before = [dict(i) for i in b.items]
            with self.assertRaises(bm.Refused) as e:
                bm.start_build("hum/proj", 5, b)
            self.assertIn("#5", str(e.exception))
            self.assertIn(column, str(e.exception))
            self.assertEqual([dict(i) for i in b.items], before)
            self.assertFalse(any("mutation" in q for q, _ in b.calls))

    def test_each_refusal_says_how_to_rework_that_card(self):
        # Review fix 2 on #168: a card in Done has no open PR to turn back into a draft (it merged), so
        # its hint is the board move; a card In review turns its PR back into a draft.
        hints = {"In review": "gh pr ready --undo", "Done": "move the card to Todo on the board, then run start-build 5"}
        for column, hint in hints.items():
            b = board((5, column))
            with self.assertRaises(bm.Refused) as e:
                bm.start_build("hum/proj", 5, b)
            self.assertIn(hint, str(e.exception), column)
            for other in hints.values():
                if other != hint:
                    self.assertNotIn(other, str(e.exception), column)

    def test_a_card_in_todo_or_in_progress_moves(self):
        for column in ("Todo", "In progress"):
            b = board((1, "In progress"), (5, column))
            bm.start_build("hum/proj", 5, b)
            self.assertEqual(b.column("In progress"), [5, 1])

    def test_the_card_on_another_board_doesnt_decide(self):
        # The card's column on this board decides, not its column on another board (Done in the fake).
        b = board((5, "Todo"))
        bm.start_build("hum/proj", 5, b)
        self.assertEqual(b.status(5), "In progress")

    def test_a_card_on_another_board_too_moves_only_here(self):
        b = board((5, "Todo"))
        bm.start_build("hum/proj", 5, b)
        self.assertTrue(all(f.get("item") != "OTHER" for _, f in b.calls))


class PrReady(unittest.TestCase):
    """#156: a card goes to In review when its PR is marked ready, not when the draft opens."""

    def test_moves_each_card_the_pr_closes(self):
        b = board((3, "In progress"), (4, "In progress"), (9, "In review"))
        b.prs[20] = {"state": "OPEN", "draft": False, "closes": [3, 4]}
        bm.pr_ready("hum/proj", 20, b)
        # Each moved card goes to the top, so the last one moved is on top.
        self.assertEqual(b.column("In review"), [4, 3, 9])
        self.assertEqual(b.column("In progress"), [])

    def test_a_draft_is_refused(self):
        b = board((3, "In progress"))
        b.prs[20] = {"state": "OPEN", "draft": True, "closes": [3]}
        with self.assertRaises(bm.Refused):
            bm.pr_ready("hum/proj", 20, b)
        self.assertEqual(b.status(3), "In progress")

    def test_a_merged_pr_or_none_is_refused(self):
        b = board((3, "In progress"))
        b.prs[20] = {"state": "MERGED", "draft": False, "closes": [3]}
        for n in (20, 21):
            with self.assertRaises(bm.Refused):
                bm.pr_ready("hum/proj", n, b)
        self.assertEqual(b.status(3), "In progress")

    def test_a_pr_that_closes_nothing_is_refused(self):
        b = board((3, "In progress"))
        b.prs[20] = {"state": "OPEN", "draft": False, "closes": []}
        with self.assertRaises(bm.Refused):
            bm.pr_ready("hum/proj", 20, b)


class PrDraft(unittest.TestCase):
    """#156, the other direction: a PR turned back into a draft sends its card back to In progress."""

    def test_moves_each_card_back_to_the_top_of_in_progress(self):
        b = board((1, "In progress"), (3, "In review"), (9, "In review"))
        b.prs[20] = {"state": "OPEN", "draft": True, "closes": [3]}
        out = bm.pr_draft("hum/proj", 20, b)
        self.assertEqual(b.column("In progress"), [3, 1])
        self.assertEqual(b.column("In review"), [9])
        self.assertIn("#3", out)

    def test_a_ready_pr_is_refused(self):
        b = board((3, "In review"))
        b.prs[20] = {"state": "OPEN", "draft": False, "closes": [3]}
        with self.assertRaises(bm.Refused):
            bm.pr_draft("hum/proj", 20, b)
        self.assertEqual(b.status(3), "In review")
        self.assertFalse(any("mutation" in q for q, _ in b.calls))

    def test_a_merged_pr_none_or_one_closing_nothing_is_refused(self):
        b = board((3, "In review"))
        b.prs[20] = {"state": "MERGED", "draft": True, "closes": [3]}
        b.prs[22] = {"state": "OPEN", "draft": True, "closes": []}
        for n in (20, 21, 22):
            with self.assertRaises(bm.Refused):
                bm.pr_draft("hum/proj", n, b)
        self.assertEqual(b.status(3), "In review")


class DraftThenReady(unittest.TestCase):
    """#156's "done when": opening a draft leaves the card In progress, marking it ready moves it to In
    review, and turning it back into a draft moves it back."""

    def test_the_whole_life_of_a_pr(self):
        b = board((156, "Todo"), (2, "In review"))
        bm.start_build("hum/proj", 156, b)
        b.prs[160] = {"state": "OPEN", "draft": True, "closes": [156]}  # the builder opens its draft
        with self.assertRaises(bm.Refused):
            bm.pr_ready("hum/proj", 160, b)  # a move at draft-open is refused
        self.assertEqual(b.status(156), "In progress")
        b.prs[160]["draft"] = False  # gh pr ready
        bm.pr_ready("hum/proj", 160, b)
        self.assertEqual(b.column("In review"), [156, 2])
        b.prs[160]["draft"] = True  # gh pr ready --undo
        bm.pr_draft("hum/proj", 160, b)
        self.assertEqual(b.column("In progress"), [156])
        self.assertEqual(b.column("In review"), [2])


class Properties(unittest.TestCase):
    def test_random_boards_draft_and_ready_round_trip(self):
        # Both directions, from any column: ready puts the card on top of In review, draft puts it back on
        # top of In progress, and no other card moves either way.
        rng = random.Random(156)
        for _ in range(300):
            numbers = rng.sample(range(1, 80), rng.randint(1, 10))
            b = board(*[(n, rng.choice(COLUMNS)) for n in numbers])
            target = rng.choice(numbers)
            before = [(i["issue"], i["status"]) for i in b.items if i["issue"] != target]
            b.prs[600] = {"state": "OPEN", "draft": False, "closes": [target]}
            for _ in range(rng.randint(1, 4)):
                if b.prs[600]["draft"]:
                    bm.pr_draft("hum/proj", 600, b)
                    self.assertEqual(b.column("In progress")[0], target)
                else:
                    bm.pr_ready("hum/proj", 600, b)
                    self.assertEqual(b.column("In review")[0], target)
                self.assertEqual([(i["issue"], i["status"]) for i in b.items if i["issue"] != target], before)
                b.prs[600]["draft"] = not b.prs[600]["draft"]

    def test_random_boards_both_steps(self):
        rng = random.Random(121)
        for _ in range(300):
            numbers = rng.sample(range(1, 80), rng.randint(1, 10))
            b = board(*[(n, rng.choice(COLUMNS)) for n in numbers])
            target = rng.choice(numbers)
            before = [(i["issue"], i["status"]) for i in b.items if i["issue"] != target]
            if b.status(target) in ("In review", "Done"):
                # #159: start-build never moves a card back from In review or Done.
                whole = [(i["issue"], i["status"]) for i in b.items]
                with self.assertRaises(bm.Refused):
                    bm.start_build("hum/proj", target, b)
                self.assertEqual([(i["issue"], i["status"]) for i in b.items], whole)
                continue
            bm.start_build("hum/proj", target, b)
            self.assertEqual(b.column("In progress")[0], target)
            self.assertEqual([(i["issue"], i["status"]) for i in b.items if i["issue"] != target], before)
            b.prs[500] = {"state": "OPEN", "draft": False, "closes": [target]}
            bm.pr_ready("hum/proj", 500, b)
            self.assertEqual(b.column("In review")[0], target)
            self.assertNotIn(target, b.column("In progress"))
            self.assertEqual([(i["issue"], i["status"]) for i in b.items if i["issue"] != target], before)


def fake_gh(folder, answer, fail=False):
    gh = os.path.join(folder, "gh")
    calls = os.path.join(folder, "calls.txt")
    with open(os.path.join(folder, "answer.json"), "w") as f:
        json.dump(answer, f)
    with open(gh, "w") as f:
        f.write("#!/bin/sh\nprintf '%%s\\n' \"$*\" >> '%s'\n" % calls)
        f.write("echo 'HTTP 502' >&2\nexit 1\n" if fail else "cat '%s/answer.json'\n" % folder)
    os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
    return calls


class Script(unittest.TestCase):
    def run_script(self, args, answer, fail=False):
        folder = tempfile.mkdtemp()
        calls = fake_gh(folder, answer, fail)
        env = dict(os.environ, PATH=folder + os.pathsep + os.environ["PATH"])
        r = subprocess.run([sys.executable, SCRIPT] + args + ["--repo", "hum/proj"], capture_output=True, text=True,
                           env=env, timeout=60)
        with open(calls) as f:
            return r, f.read()

    def test_a_gh_failure_exits_2_and_moves_nothing(self):
        r, calls = self.run_script(["start-build", "7"], {}, fail=True)
        self.assertEqual(r.returncode, 2)
        self.assertIn("gh failed", r.stderr)
        self.assertNotIn("mutation", calls)

    def test_a_refusal_exits_1(self):
        answer = {"data": {"repository": {"projectsV2": {"nodes": []}, "issue": None}}}
        r, _ = self.run_script(["start-build", "7"], answer)
        self.assertEqual(r.returncode, 1)
        self.assertIn("#7", r.stderr)

    def test_pr_draft_is_a_step(self):
        answer = {"data": {"repository": {"projectsV2": {"nodes": []}, "pullRequest": None}}}
        r, _ = self.run_script(["pr-draft", "7"], answer)
        self.assertEqual(r.returncode, 1)
        self.assertIn("#7", r.stderr)

    def test_usage(self):
        r = subprocess.run([sys.executable, SCRIPT], capture_output=True, text=True, timeout=60)
        self.assertNotEqual(r.returncode, 0)


class TimeLimits(unittest.TestCase):
    def test_every_child_has_a_time_limit(self):
        with open(SCRIPT, encoding="utf-8") as f:
            source = f.read()
        self.assertGreater(source.count("subprocess.run("), 0)
        self.assertEqual(source.count("subprocess.run("), source.count("timeout="))


if __name__ == "__main__":
    unittest.main()
