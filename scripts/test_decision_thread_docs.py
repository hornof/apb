"""Tests for issue #169: plain-English Decisions, and the advisor replies inside the Decision thread
only when it helps. Run from the repo root: python3 -m unittest discover -s scripts -t scripts

#97 kept every reply out of a Decision thread until the human answered, the advisor's included, and sent
the advisor's "plain words" rewrite of each Decision to a PR-level comment. On PR #164 the human asked for
the advisor's view, it was posted at the PR level, and he couldn't find it. The cause was Decisions that
were hard to read. So #169 asks the reviewer for plain-English Decisions, lets the advisor reply inside
the thread when it helps, and removes #97's rule. decision-check.py counts only the human's own replies
as the answer (tested in test_decision_check.py).

The rules sit where each writer of a Decision thread reads them: the playbook's "How the human reviews"
(the orchestrator and the advisor), the "Discuss a card on the card" rule in "Helping the human keep
track" (one of the playbook rules the advisor follows as written), and agents/reviewer.md (the reviewer,
who writes the Decisions).
"""
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULE = "The advisor replies inside a Decision thread only when it helps."
OLD_RULE = "A Decision thread holds only the Decision until the human answers"


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

    def test_the_advisor_rule_sits_under_the_decision_items_bullet(self):
        lines = self.reviews.splitlines()
        i = next(n for n, l in enumerate(lines) if flat(l).startswith("- Decision items get one thread each"))
        self.assertTrue(lines[i + 1].startswith("    - "), "the rule is a sub-bullet of it")
        self.assertTrue(flat(lines[i + 1][6:]).startswith(RULE), lines[i + 1])

    def test_the_advisor_rule_says_when_where_and_how(self):
        rule = block(self.reviews, RULE)
        for words in ("reads every Decision", "doesn't comment on every one", "a mistake",
                      "leaves something out", "disagrees with the recommendation", "whenever the human asks",
                      "inside the Decision thread", "simple and easy to follow", "as short as possible"):
            self.assertIn(words, rule, words)

    def test_the_reviewer_and_orchestrator_still_wait_for_the_human(self):
        rule = block(self.reviews, RULE)
        self.assertIn("nobody else replies in it: not the reviewer or the orchestrator", rule)
        self.assertIn("Resolve button", rule)

    def test_only_the_humans_reply_answers(self):
        rule = block(self.reviews, RULE)
        for words in ("Only the human's own replies answer a Decision", "an advisor reply never does",
                      "resolving the thread with no reply from the human still takes the recommendation",
                      "scripts/decision-check.py"):
            self.assertIn(words, rule, words)

    def test_decisions_are_in_plain_english(self):
        shape = block(self.reviews, "Every Decision is in plain English")
        for words in ("what the choice is and why it matters", "what each option means in practice",
                      "Recommended: <letter>", "and why, in one line", "Resolve with no reply = <letter>",
                      "scripts/decision-check.py"):
            self.assertIn(words, shape, words)
        self.assertIn("Decision: taste or a trade-off, in plain English.", flat(self.playbook))

    def test_discuss_a_card_on_the_card_lets_only_the_advisor_reply_early(self):
        rule = block(self.track, "Discuss a card on the card.")
        self.assertIn("Never reply in a Decision thread until the human has replied in it or resolved it", rule)
        self.assertIn("Only the advisor may, when it helps", rule)
        self.assertIn('"How the human reviews"', rule)
        self.assertIn("My answer is in the decision 3 thread", rule)

    def test_the_stop_hooks_own_cases_bullet_starts_its_own_line(self):
        # Review fix 1 on #176: removing #97's case joined this bullet onto the one before it.
        self.assertTrue(any(l.startswith("- **The Stop hook's own cases,**") for l in self.playbook.splitlines()))
        self.assertNotIn(".- **The Stop hook's own cases", self.playbook)

    def test_the_end_of_turn_check_lets_the_advisor_reply(self):
        self.assertIn("reply-in-unanswered-decision: someone other than the human or the advisor replied",
                      flat(self.playbook))


class Reviewer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = flat(read("agents/reviewer.md"))

    def test_the_reviewer_writes_each_decision_in_plain_english(self):
        decision = block(read("agents/reviewer.md"), "Decision: a matter of taste or a trade-off")
        for words in ("plain English", "what the choice is and why it matters",
                      "what it means in practice", "Recommended: <letter>", "and why, in one line",
                      "Resolve with no reply = <letter>"):
            self.assertIn(words, decision, words)

    def test_the_reviewer_never_replies_in_its_own_decision_threads(self):
        self.assertIn("Never reply in a Decision thread until the human has replied in it or resolved it", self.text)
        self.assertIn("Never resolve a thread; only the human does.", self.text)


class NinetySevenIsGone(unittest.TestCase):
    """No tracked file outside the logs and the tests keeps #97's rule."""

    TRACES = (OLD_RULE, "plain-words summary", "plain-words comment", "PR-level comment that links each",
              "decision-without-advisor-comment", "not the reviewer, the orchestrator or the advisor")

    def test_no_file_keeps_the_old_rule(self):
        files = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True,
                               timeout=30, check=True).stdout.split()
        for rel in files:
            name = os.path.basename(rel)
            if rel == "LOG.md" or name.startswith("test_") or not rel.endswith((".md", ".py", ".json")):
                continue
            text = flat(read(rel))
            for trace in self.TRACES:
                self.assertNotIn(trace, text, "%s still says %r" % (rel, trace))


if __name__ == "__main__":
    unittest.main()
