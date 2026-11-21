"""Tests for how the docs describe the "your turn" flag: the card assigned to the human while it waits
on them. Run from the repo root: python3 -m unittest discover -s scripts -t scripts

The playbook's "Helping the human keep track" holds the rule; the orchestrator's hand-off steps in
apb-build and apb-review say when to assign and unassign, and to run scripts/human-turn.py on every
status check.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def section(text, name, level="## "):
    m = re.search(r"^%s%s\n(.*?)(?=^%s|\Z)" % (re.escape(level), re.escape(name), re.escape(level)),
                  text, re.S | re.M)
    return m.group(1) if m else ""


def flat(text):
    return " ".join(text.replace("*", "").split())


class Playbook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = flat(section(read("docs/playbook.md"), "Helping the human keep track"))

    def test_the_rule(self):
        for words in ("Assign the card to the human while it waits on them",
                      "a PR to merge, a Decision to answer or a question on the card",
                      "assigns the card's issue to the human, and the PR too",
                      "unassigns them in the same turn",
                      "Nothing else uses the assignee",
                      "github.com/issues/assigned",
                      "scripts/human-turn.py"):
            self.assertIn(words, self.text, words)

    def test_said_once(self):
        self.assertEqual(self.text.count("Nothing else uses the assignee"), 1)


class Skills(unittest.TestCase):
    def test_build_assigns_and_checks(self):
        text = flat(read("skills/apb-build/SKILL.md"))
        for words in ("human-turn.py", "--add-assignee", "--remove-assignee"):
            self.assertIn(words, text, words)

    def test_review_assigns_at_the_hand_off(self):
        text = flat(read("skills/apb-review/SKILL.md"))
        for words in ("--add-assignee", "--remove-assignee", "human-turn.py"):
            self.assertIn(words, text, words)

    def test_loop_assigns_unassigns_and_checks(self):
        step3 = re.search(r"^3\. (.*?)(?=^4\. )", read("skills/apb-loop/SKILL.md"), re.S | re.M)
        self.assertIsNotNone(step3)
        step3 = flat(step3.group(1))
        for words in ("--add-assignee", "--remove-assignee", "human-turn.py",
                      "Assign the card to the human while it waits on them"):
            self.assertIn(words, step3, words)

    def test_the_script_is_named_where_it_lives(self):
        self.assertTrue(os.path.exists(os.path.join(ROOT, "scripts", "human-turn.py")))


if __name__ == "__main__":
    unittest.main()
