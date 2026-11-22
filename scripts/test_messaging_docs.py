"""Tests for how the docs describe the orchestrator's messages to the human. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts

The playbook's "Helping the human keep track" holds every rule for those messages, so a new project's
orchestrator follows them with nothing else to read. It has four ask lines, says each rule once and
names no project, person or date. The project CLAUDE.md template lists the same four lines. The ask is
the last thing in a hand-off (#153), in the same words in every place that says where it goes.
"""
import ast
import importlib.util
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTION = "Helping the human keep track"
ALL_CLEAR = "All clear: <boards> empty, no open PRs, as of <time>."
NOTHING = "Nothing for you to do right now. Waiting on <what>, ETA <time>."
ASK_LAST = ("The ask is the last thing in the message: one of the four lines, then only its numbered list and "
            "any link or fenced block that belongs to it. What happened and the status lines go above it.")


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

    def test_has_four_ask_lines(self):
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

    def test_keep_what_works(self):
        for words in ("Keep what works.",
                      "keep doing it exactly the same way",
                      "Don't change its form, place or wording unless the human agrees",
                      "A different form makes the human think something different is happening"):
            self.assertIn(words, self.text, words)

    def test_never_say_its_late(self):
        self.assertIn("Never say it's late or that the human seems tired, and never suggest stopping. The human decides when to stop.", self.text)
        self.assertNotIn("time of day", self.text)

    def test_what_counts_as_a_change(self):
        for words in ("What counts as a change.",
                      "Undoing or withdrawing something",
                      "promising to do something differently \"from now on\" (even only in chat)",
                      "rewording or \"clarifying\" a rule", "fixing a typo", "moving a card",
                      "restarting a watcher", "telling an agent to start or stop",
                      "Only reading, checking and answering are not changes",
                      "When the human says to stop changing things, make no change of any kind until they say otherwise",
                      "answer only about the time since then",
                      "If nothing changed, say \"nothing\""):
            self.assertIn(words, self.text, words)

    def test_copy_a_go_ahead_onto_the_card(self):
        for words in ("Copy a go-ahead from chat onto the card.",
                      "post it on the card or the coordination issue in the same turn",
                      "quoting their words and the time",
                      "Then the advisor sees it, and it stays on GitHub."):
            self.assertIn(words, self.text, words)

    def test_each_rule_once(self):
        whole = flat(read("docs/playbook.md"))
        for words in ("Never alert for progress", "Plain English.", "Check before asking the human to act",
                      ALL_CLEAR, "one line per card in In progress and In review",
                      "Keep what works.", "suggest stopping", "What counts as a change.",
                      "Copy a go-ahead from chat"):
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
    def test_lists_the_four_ask_lines(self):
        text = flat(read("templates/CLAUDE-template.md"))
        self.assertIn("one of four lines", text)
        self.assertIn(ALL_CLEAR, text)
        self.assertNotIn("one of three lines", text)

    def test_same_words_as_the_playbook_on_stopping(self):
        text = flat(read("templates/CLAUDE-template.md"))
        self.assertIn("Never say it's late or that the human seems tired, and never suggest stopping.", text)
        self.assertNotIn("time of day", text)


class AskGoesLast(unittest.TestCase):
    """#153: the ask is the last thing in a hand-off. The rule reads the same, word for word, in the
    playbook, the Stop hook that checks it, the review skill's hand-off step and the CLAUDE template, and
    none of them still puts the ask in the first line."""

    def places(self):
        hook = ast.get_docstring(ast.parse(read("hooks/apb-stop.py")))
        skill = read("skills/apb-review/SKILL.md")
        step5 = re.search(r"^5\. (.*?)(?=^6\. )", skill, re.S | re.M)
        return {"docs/playbook.md": section(read("docs/playbook.md"), SECTION),
                "hooks/apb-stop.py": hook or "",
                "skills/apb-review/SKILL.md step 5": step5.group(1) if step5 else "",
                "templates/CLAUDE-template.md": read("templates/CLAUDE-template.md")}

    def test_the_rule_reads_the_same_everywhere(self):
        for where, text in self.places().items():
            self.assertIn(ASK_LAST, flat(text), where)

    def test_no_place_puts_the_ask_first(self):
        for where, text in self.places().items():
            for words in ("first line", "starts with one of", "start the message with"):
                self.assertNotIn(words, flat(text).lower(), (where, words))

    def test_the_hooks_send_back_says_end_the_message_with(self):
        spec = importlib.util.spec_from_file_location("apb_stop", os.path.join(ROOT, "hooks", "apb-stop.py"))
        hook = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(hook)
        self.assertIn("so end the message with one of these lines", hook.REASON)
        self.assertNotIn("start the message with", hook.REASON)

    def test_the_status_lines_go_above_the_ask(self):
        self.assertIn("Above the ask, one line per card in In progress and In review",
                      flat(section(read("docs/playbook.md"), SECTION)))
        self.assertIn("Above the ask, one line per card in In progress and In review",
                      flat(read("templates/CLAUDE-template.md")))


if __name__ == "__main__":
    unittest.main()
