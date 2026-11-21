"""Tests for issue #97: a Decision thread holds only the Decision until the human answers. Run from the
repo root: python3 -m unittest discover -s scripts -t scripts

The advisor posted a plain-words summary of a Decision as a reply inside the Decision's thread. GitHub
puts the thread's Resolve button under the last reply, so the button moved below the advisor's text and
the human, looking for it under the Decision, didn't find it. Resolving with no reply is how the human
accepts a recommendation, so the button has to sit right under the Decision.

The rule sits where each writer of a Decision thread reads it: the playbook's "How the human reviews"
(the orchestrator), the "Discuss a card on the card" rule in "Helping the human keep track" (one of the
three playbook rules the advisor follows as written), and agents/reviewer.md (the reviewer, who posts
the Decisions).
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULE = "A Decision thread holds only the Decision until the human answers."


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def section(text, name):
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(name), text, re.S | re.M)
    return m.group(1) if m else ""


def flat(text):
    return " ".join(text.replace("*", "").split())


def block(text, start):
    """The bullet whose text starts with `start`, with its sub-bullets, without markup."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        if stripped.startswith("- ") and flat(stripped[2:]).startswith(start):
            indent = len(line) - len(stripped)
            out = [stripped[2:]]
            for nxt in lines[i + 1:]:
                s = nxt.lstrip()
                if s and (len(nxt) - len(s)) <= indent:
                    break
                out.append(s)
            return flat("\n".join(out))
    return ""


class Playbook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playbook = read("docs/playbook.md")
        cls.reviews = section(cls.playbook, "How the human reviews")
        cls.track = section(cls.playbook, "Helping the human keep track")

    def test_the_rule_sits_under_the_decision_items_bullet(self):
        lines = self.reviews.splitlines()
        i = next(n for n, l in enumerate(lines) if flat(l).startswith("- Decision items get one thread each"))
        self.assertTrue(lines[i + 1].startswith("    - "), "the rule is a sub-bullet of it")
        self.assertTrue(flat(lines[i + 1][6:]).startswith(RULE), lines[i + 1])

    def test_the_rule_says_who_why_and_where_the_summary_goes(self):
        rule = block(self.reviews, RULE)
        for words in ("Until the human replies in it or resolves it, nobody else replies",
                      "not the reviewer, the orchestrator or the advisor", "Resolve button",
                      "under a thread's last reply", "right under the Decision", "plain-words summary",
                      "one PR-level comment that links each Decision thread",
                      "or nowhere, when the Decision is already plain"):
            self.assertIn(words, rule, words)

    def test_the_advisor_reads_it_in_discuss_a_card_on_the_card(self):
        rule = block(self.track, "Discuss a card on the card.")
        self.assertIn("Never reply in a Decision thread until the human has replied in it or resolved it",
                      rule)
        self.assertIn("a PR-level comment that links each thread", rule)
        self.assertIn('"How the human reviews"', rule)

    def test_the_example_answer_in_a_thread_still_fits(self):
        # Answering a question the human asked in a thread is allowed: the human has replied by then.
        rule = block(self.track, "Discuss a card on the card.")
        self.assertIn("My answer is in the decision 3 thread", rule)


class Reviewer(unittest.TestCase):
    def test_the_reviewer_never_replies_in_its_own_decision_threads(self):
        text = flat(read("agents/reviewer.md"))
        self.assertIn("Never reply in a Decision thread until the human has replied in it or resolved it", text)
        self.assertIn("Never resolve a thread; only the human does.", text)


if __name__ == "__main__":
    unittest.main()
