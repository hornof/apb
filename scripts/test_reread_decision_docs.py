"""Tests for issue #96: the orchestrator rereads a decision's thread before asking it again. Run from
the repo root: python3 -m unittest discover -s scripts -t scripts

In tahoe-tracker the human answered a decision on the card's issue, the watcher then reported a
merge, and the orchestrator asked the decision again without rereading its thread. The rule sits
where the orchestrator reads it: the playbook's "While Claude waits", next to "Check before
stating", and the two skills it follows while decisions are open, apb-build and apb-review.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULE = "Reread a decision's thread before asking it again."
# `gh pr view N --comments` leaves out review-thread replies and resolved state, so the rule names this.
DECISION_CHECK = "python3 <apb plugin folder>/scripts/decision-check.py N"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def section(text, name):
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(name), text, re.S | re.M)
    return m.group(1) if m else ""


def flat(text):
    return " ".join(text.replace("*", "").split())


def bullet(text, start):
    """The top-level bullet that starts with `start`, without its markup."""
    for line in text.splitlines():
        if line.startswith("- ") and flat(line[2:]).startswith(start):
            return flat(line[2:])
    return ""


class Playbook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playbook = read("docs/playbook.md")
        cls.waits = section(cls.playbook, "While Claude waits")

    def test_the_rule_is_in_while_claude_waits_right_after_check_before_stating(self):
        tops = [flat(line[2:]) for line in self.waits.splitlines() if line.startswith("- ")]
        firsts = [t.split(".")[0] + "." for t in tops]
        self.assertIn(RULE, firsts)
        self.assertEqual(firsts[firsts.index(RULE) - 1], "Check before stating.")

    def test_the_rule_says_when_what_to_read_and_why(self):
        rule = bullet(self.waits, RULE)
        for words in ("asking a decision a second time", "still open", "same turn", "Decision thread",
                      DECISION_CHECK, "resolved or answered", "gh issue view N --comments", "merge",
                      "reply on the card"):
            self.assertIn(words, rule, words)

    def test_the_decision_rules_point_to_it(self):
        reviews = flat(section(self.playbook, "How the human reviews"))
        self.assertIn("reads the decision's thread on GitHub in the same turn", reviews)
        self.assertIn(RULE.rstrip("."), reviews)

    def test_the_watcher_gives_an_issue_comment_its_own_line(self):
        line = flat(next(l for l in self.waits.splitlines() if "an issue named with `--issue N`" in l))
        self.assertIn("It gets its own line, even when the same read finds something else, such as a merge.", line)
        self.assertIn("after a run that didn't watch it, the watcher reports the comments posted since that "
                      "run's last read.", line)


class Skills(unittest.TestCase):
    def test_apb_build_and_apb_review_say_it(self):
        for skill in ("skills/apb-build/SKILL.md", "skills/apb-review/SKILL.md"):
            text = flat(read(skill))
            self.assertRegex(text, r"Before you ask a [dD]ecision again, or say it is still open, read its thread",
                             skill)
            self.assertIn("same turn", text, skill)
            self.assertIn(DECISION_CHECK, text, skill)
            self.assertIn("gh issue view N --comments", text, skill)
            self.assertIn("merge", text, skill)


if __name__ == "__main__":
    unittest.main()
