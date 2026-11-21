"""Tests for how apb-setup asks the project's name. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts -p test_setup_name_question.py

The name and one-sentence description are free text, so setup asks for them in one plain
sentence in chat and takes whatever the human types. A multiple-choice card would make the
human pick "Other" just to type. Multiple choice stays for questions with real choices, such
as public or private.
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
    """Top-level numbered steps of apb-setup, keyed by their label ("1", "1a", "4", ...)."""
    parts = re.split(r"^(\d+[a-z]?)\. ", read(SETUP), flags=re.M)
    return {parts[i]: flat(parts[i + 1]) for i in range(1, len(parts), 2)}


def quoted_question():
    """The question the skill tells Claude to ask, written out in quotes in step 1."""
    m = re.search(r"plain text in chat[^\"]*\"([^\"]+)\"", steps()["1"])
    return m and m.group(1)


class NameIsAPlainQuestion(unittest.TestCase):
    def test_step_1_asks_name_and_description_as_plain_text_in_chat(self):
        self.assertIn("as plain text in chat", steps()["1"])
        q = quoted_question() or ""
        self.assertIn("called", q)
        self.assertIn("one sentence", q)

    def test_the_question_is_written_out_and_is_one_sentence(self):
        q = quoted_question()
        self.assertIsNotNone(q, "step 1 should spell out the question to ask")
        # Independent check on the wording itself: one sentence, one question mark, no options.
        self.assertTrue(q.endswith("?"), q)
        self.assertEqual(len(re.findall(r"[.!?](\s|$)", q)), 1, q)
        self.assertNotRegex(q.lower(), r"\bother\b|decide later|\bpick\b|\bchoose\b|\bor\b")

    def test_no_card_no_options_no_other(self):
        body = steps()["1"]
        self.assertRegex(body, r"not (as )?a multiple-choice (card|question)")
        self.assertRegex(body, r"never make the human pick \"Other\"")

    def test_the_typed_answer_is_used(self):
        body = steps()["1"]
        m = re.search(r"Use the name for the repo \(as a short slug such as `([^`]+)`\)", body)
        self.assertIsNotNone(m, body[:400])
        # Independent check: the example really is a valid repo slug.
        self.assertRegex(m.group(1), r"^[a-z0-9]+(-[a-z0-9]+)*$")
        self.assertIn("the sentence for the README's description", body)
        self.assertNotRegex(body, r"[Tt]ake (whatever|what) the human types as the repo name")

    def test_name_is_asked_before_the_repo_is_created(self):
        body = steps()["1"]
        self.assertLess(body.index("as plain text in chat"), body.index("gh repo create"))


class RealChoicesStayMultipleChoice(unittest.TestCase):
    def test_public_or_private_is_still_a_choice(self):
        body = steps()["1"]
        self.assertIn("public or private", body)
        self.assertRegex(body, r"as a multiple-choice question, whether it should be public or private")

    def test_no_option_anywhere_tells_the_human_to_pick_other(self):
        # The rehearsal's broken card: an option whose text says to pick a different option.
        text = flat(read(SETUP))
        for m in re.finditer(r"pick (\")?Other", text):
            window = text[max(0, m.start() - 40):m.start()]
            self.assertIn("never make the human", window, text[m.start() - 80:m.end() + 40])


if __name__ == "__main__":
    unittest.main()
