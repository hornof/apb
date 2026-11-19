"""Tests for the PRD input's question about what might not be technically possible. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts -p test_prd_input_feasibility.py

The human often knows up front that something may not be possible, or depends on another site or
service cooperating. apb-setup step 1b asks it in the PRD input issue, and apb-prd step 3 starts
section 11 (Technical uncertainties) from the answer.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = "skills/apb-setup/SKILL.md"
PRD_SKILL = "skills/apb-prd/SKILL.md"
QUESTION = ("Anything you're not sure is technically possible, "
            "or that depends on another site or service cooperating?")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def flat(text):
    return " ".join(text.replace("*", "").split())


def input_questions():
    """The questions apb-setup step 1b lists for the PRD input issue, in order."""
    text = read(SETUP)
    start = text.index("1b. PRD input issue:")
    end = text.index("\n\n", start)
    return [q.strip() for q in re.findall(r"^   - (.+)$", text[start:end], re.M)]


def prd_step(n):
    parts = re.split(r"^(\d+)\. ", read(PRD_SKILL), flags=re.M)
    steps = {int(parts[i]): flat(parts[i + 1]) for i in range(1, len(parts), 2)}
    return steps[n]


class SetupQuestion(unittest.TestCase):
    def test_question_is_in_the_list_once(self):
        self.assertEqual(input_questions().count(QUESTION), 1, input_questions())

    def test_question_comes_right_after_where_will_people_use_it(self):
        qs = input_questions()
        where = [i for i, q in enumerate(qs) if q.startswith("Where will people use it")]
        self.assertEqual(len(where), 1)
        self.assertEqual(qs[where[0] + 1], QUESTION)

    def test_other_questions_unchanged(self):
        # The card touches nothing else in the list: same questions, same order, plus the new one.
        others = [q for q in input_questions() if q != QUESTION]
        self.assertEqual([q.split()[0] for q in others],
                         ["What", "Who", "What", "What's", "Public", "Where", "Any"])
        self.assertEqual(len(input_questions()), len(others) + 1)


class PrdSeedsFromTheAnswer(unittest.TestCase):
    def test_step_3_starts_section_11_from_the_human_answer(self):
        body = prd_step(3)
        self.assertIn("Run a spike for each technical uncertainty", body)
        self.assertIn("section 11 (Technical uncertainties)", body)
        self.assertIn("PRD input", body)
        self.assertIn("technically possible", body)
        self.assertRegex(body, r"adds? (its|your) own")

    def test_seeding_comes_before_the_none_case(self):
        # The human's answer has to be read before the PM decides there are no uncertainties.
        body = prd_step(3)
        self.assertLess(body.index("technically possible"), body.index('"None"'))


class Timeless(unittest.TestCase):
    def test_no_years_project_or_people_names(self):
        names = re.compile(r"\b(sketch-gallery|tahoe|vail|luke|sam|inktober)\b")
        for rel in (SETUP, PRD_SKILL):
            text = read(rel)
            self.assertIsNone(re.search(r"\b20\d\d\b", text), rel)
            self.assertEqual(names.findall(text.lower()), [], rel)


if __name__ == "__main__":
    unittest.main()
