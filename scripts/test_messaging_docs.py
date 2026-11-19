"""Tests for how the docs describe the orchestrator's messages to the human. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts

The playbook's "Helping the human keep track" holds every rule for those messages, so a new project's
orchestrator follows them with nothing else to read. It has four first lines, says each rule once and
names no project, person or date. The project CLAUDE.md template lists the same four first lines.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTION = "Helping the human keep track"
ALL_CLEAR = "All clear: <boards> empty, no open PRs, as of <time>."
NOTHING = "Nothing for you to do right now. Waiting on <what>, ETA <time>."


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def section(text, name, level="## "):
    """The body of the heading `name`, up to the next heading of the same level."""
    m = re.search(r"^%s%s\n(.*?)(?=^%s|\Z)" % (re.escape(level), re.escape(name), re.escape(level)),
                  text, re.S | re.M)
    return m.group(1) if m else ""


def flat(text):
    return " ".join(text.replace("*", "").split())


class Playbook(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.body = section(read("docs/playbook.md"), SECTION)
        cls.text = flat(cls.body)

    def test_has_four_first_lines(self):
        self.assertIn("one of four lines", self.text)
        for line in (NOTHING, "1 thing for you to do right now: <the thing>.",
                     "N things for you to do right now:", ALL_CLEAR):
            self.assertIn(line, self.text, line)

    def test_all_clear(self):
        for words in ("Send it once", "nothing is open on any board or repo the orchestrator watches",
                      "one desktop alert", "Nothing goes under it", "never \"or stop here\"",
                      "Don't repeat it on later stops while everything is still clear"):
            self.assertIn(words, self.text, words)

    def test_nothing_is_only_for_work_in_flight(self):
        self.assertIn("\"Nothing for you to do right now\" means something is running or waiting on GitHub",
                      self.text)

    def test_etas_are_clock_times(self):
        # In "While Claude waits", next to the rule to run date; said there once, not again here.
        text = flat(read("docs/playbook.md"))
        self.assertIn("an ETA as a clock time, for example \"ETA 2:40 PM\", not \"about 10 minutes\"", text)
        self.assertIn("Run `date` in the same turn as any clock time you state", text)
        self.assertNotIn("about M minutes", text)

    def test_template_has_no_old_first_line(self):
        self.assertNotIn("about M minutes", flat(read("templates/CLAUDE-template.md")))

    def test_rules_from_the_orchestrators_notes(self):
        for words in ("Status messages are about now",  # no past timings in them
                      "I saw your merge of <link>",  # say when you notice what the human did
                      "Discuss a card on the card",  # answers go on GitHub, one line in chat
                      "Never repeat the card's content in chat",
                      "Name the repo with every card or PR number",
                      "Hand-offs are short and complete",
                      "Keep one name for each thing",
                      "every step in order",
                      "in the same turn"):  # the alert goes with the message
            self.assertIn(words, self.text, words)

    def test_each_rule_once(self):
        whole = flat(read("docs/playbook.md"))
        for words in ("Never alert for progress", "Plain English.", "Check before asking the human to act",
                      ALL_CLEAR, "one line per card in In progress and In review"):
            self.assertEqual(whole.count(words), 1, words)

    def test_checks_before_a_hand_off(self):
        text = flat(read("docs/playbook.md"))
        for words in ("every link opens, checked by a script where possible",
                      "the PR description matches the latest push"):
            self.assertIn(words, text, words)

    def test_names_no_project_person_or_date(self):
        for words in ("sketch-gallery", "Luke", "Sam", "2026"):
            self.assertNotIn(words, self.body, words)


class Template(unittest.TestCase):
    def test_lists_the_four_first_lines(self):
        text = flat(read("templates/CLAUDE-template.md"))
        self.assertIn("one of four lines", text)
        self.assertIn(ALL_CLEAR, text)
        self.assertNotIn("one of three lines", text)


if __name__ == "__main__":
    unittest.main()
