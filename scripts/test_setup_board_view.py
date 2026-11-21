"""Tests for the kanban view in apb-setup's board step. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts -p test_setup_board_view.py

Done when (issue #93): a fresh setup ends with the board showing a "kanban" board view grouped by
Status, with no manual step; it works for user-owned boards with the human's `gh` login; and the
hand-off names what, if anything, still needs a manual step. The script's behaviour is tested in
test_board_view.py; these check that setup runs it, as the human, and carries its result through.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = "skills/apb-setup/SKILL.md"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def flat(text):
    return " ".join(text.replace("*", "").split())


def steps():
    parts = re.split(r"^(\d+[a-z]?)\. ", read(SETUP), flags=re.M)
    return {parts[i]: flat(parts[i + 1]) for i in range(1, len(parts), 2)}


class BoardStepRunsTheScript(unittest.TestCase):
    def body(self):
        return steps()["3"]

    def test_runs_board_view(self):
        self.assertIn("python3 <apb plugin folder>/scripts/board-view.py <owner> <board number>", self.body())

    def test_names_the_target(self):
        body = self.body()
        self.assertIn('"kanban"', body)
        self.assertRegex(body, r"board layout[^.]*grouped by Status")
        self.assertIn("Todo / In progress / In review / Done", body)
        self.assertIn("View 1", body)

    def test_says_a_rerun_changes_nothing(self):
        self.assertIn("Running it again changes nothing.", self.body())
        self.assertNotIn("Run twice", self.body())

    def test_runs_after_the_board_exists(self):
        body = self.body()
        self.assertLess(body.index("create"), body.index("board-view.py"))

    def test_runs_as_the_humans_login_and_says_why(self):
        body = self.body()
        self.assertRegex(body, r"human's own `gh` login")
        self.assertIn("drops `GH_TOKEN` and `GITHUB_TOKEN`", body)
        self.assertRegex(body, r"fine-grained[^.]*(App|app)")
        self.assertIn("user-owned", body)

    def test_failure_goes_to_the_hand_off(self):
        body = self.body()
        self.assertIn("Manual step", body)
        self.assertRegex(body, r"hand-off")

    def test_no_sentence_has_the_human_build_the_view(self):
        for sentence in re.split(r"(?<=[.!?])\s", flat(read(SETUP))):
            if "kanban" in sentence and re.search(r"(ask|tell) the human", sentence):
                self.assertRegex(sentence, r"[Oo]nly if|fails|Manual step", sentence)


class HandOffNamesManualSteps(unittest.TestCase):
    def test_last_step_names_board_view_manual_steps(self):
        s = steps()
        last = s[max(s, key=lambda k: (int(re.match(r"\d+", k).group()), k))]
        self.assertRegex(last, r"hand-off[^.]*every \"Manual step\" line[^.]*board-view\.py[^.]*step 3")

    def test_general_words_not_a_login(self):
        self.assertNotIn("hornof", read(SETUP).lower())


if __name__ == "__main__":
    unittest.main()
