"""Tests for technical uncertainties and spikes at the PRD stage. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts

A PRD can be approved while a technical question that decides the design is still open. The PRD
template gets a "Technical uncertainties" section; apb-prd runs a spike (a time-boxed investigation
whose output is an answer, not product code) for each one before the review; the reviewer checks
for the section and for one spike file per uncertainty.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = "templates/PRD-template.md"
PRD_SKILL = "skills/apb-prd/SKILL.md"
REVIEW_SKILL = "skills/apb-review/SKILL.md"
REVIEWER = "agents/reviewer.md"
CHANGED = (TEMPLATE, PRD_SKILL, REVIEW_SKILL, REVIEWER, "agents/builder.md")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def flat(text):
    return " ".join(text.replace("*", "").split())


def template_sections():
    """{number: name} for the template's numbered sections, in order."""
    return [(int(n), name.strip()) for n, name in re.findall(r"^## (\d+)\. (.+)$", read(TEMPLATE), re.M)]


def prd_steps():
    """The numbered steps of apb-prd, each with its sub-bullets, as {number: flat text}."""
    text = read(PRD_SKILL)
    parts = re.split(r"^(\d+)\. ", text, flags=re.M)
    return [(int(parts[i]), flat(parts[i + 1])) for i in range(1, len(parts), 2)]


class Template(unittest.TestCase):
    def test_sections_are_numbered_straight_through(self):
        numbers = [n for n, _ in template_sections()]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    def test_uncertainties_come_right_after_risks(self):
        names = [name for _, name in template_sections()]
        self.assertIn("Technical uncertainties", names)
        self.assertEqual(names[names.index("Technical uncertainties") - 1], "Risks and assumptions")

    def test_decisions_and_open_questions_stay_last(self):
        names = [name for _, name in template_sections()]
        self.assertEqual(names[-2:], ["Decisions so far", "Open questions"])

    def test_uncertainty_section_says_what_counts_and_that_none_must_be_written(self):
        body = flat(re.search(r"^## \d+\. Technical uncertainties\n(.*?)(?=^## )", read(TEMPLATE),
                              re.S | re.M).group(1))
        for words in ("design", "requirements", "how long the build takes", "spike", "docs/spikes/",
                      '"None"'):
            self.assertIn(words, body, words)
        self.assertRegex(body, r"[Ww]rite .None.|.None. is (a )?valid")

    def test_uncertainty_section_allows_the_accepted_risk_exception(self):
        # The same exception apb-prd step 3 and the reviewer allow: the human accepts the risk instead.
        body = flat(re.search(r"^## \d+\. Technical uncertainties\n(.*?)(?=^## )", read(TEMPLATE),
                              re.S | re.M).group(1))
        self.assertIn("accepts the risk", body)
        self.assertIn("Open questions", body)
        self.assertEqual(body.count('"None"'), 1)


class PrdSkill(unittest.TestCase):
    def test_steps_are_numbered_straight_through(self):
        numbers = [n for n, _ in prd_steps()]
        self.assertEqual(numbers, list(range(1, len(numbers) + 1)))

    def spike_step(self):
        steps = [(n, body) for n, body in prd_steps() if "spike" in body.lower()]
        self.assertEqual(len(steps), 1, "one step runs the spikes")
        return steps[0]

    def test_spike_step_comes_after_filling_in_and_before_mockups_and_review(self):
        n, _ = self.spike_step()
        steps = dict(prd_steps())
        self.assertIn("Fill in every section", steps[n - 1])
        self.assertIn("mockups", steps[n + 1].lower())
        review = [k for k, body in steps.items() if "Run `apb-review`" in body]
        self.assertEqual(len(review), 1)
        self.assertLess(n, review[0])

    def test_spike_is_time_boxed_to_one_builder_session_with_the_question_in_the_hand_off(self):
        _, body = self.spike_step()
        for words in ("time-boxed", "one builder agent", "one session", "the question"):
            self.assertIn(words, body, words)

    def test_spike_result_file_holds_question_facts_options_effort_and_recommendation(self):
        _, body = self.spike_step()
        for words in ("`docs/spikes/<name>.md`", "the question", "what was tried", "URLs", "headers", "errors",
                      "options", "how much work", "recommendation", "best value"):
            self.assertIn(words, body, words)

    def test_spike_code_never_merges_but_findings_do_in_the_prd_pr(self):
        _, body = self.spike_step()
        self.assertIn("Spike code never merges", body)
        self.assertIn("findings file merges with the PRD", body)

    def test_orchestrator_commits_the_findings_file(self):
        _, body = self.spike_step()
        self.assertIn("The orchestrator commits the findings file to the PRD branch.", body)

    def test_builder_knows_a_spike_brief_is_not_a_departure(self):
        # A spike brief sends the builder no tests, no PR and no LOG.md line: builder.md has to say so,
        # or the brief clashes with "a brief never changes that".
        text = flat(read("agents/builder.md"))
        self.assertIn("On a spike (apb-prd step 3): no tests, no PR and no `LOG.md` line.", text)
        self.assertIn("`docs/spikes/<name>.md`", text)
        self.assertIn("name it in your report", text)
        n, _ = self.spike_step()
        self.assertEqual(n, 3, "builder.md names apb-prd step 3")

    def test_prd_cites_the_spike_in_decisions_so_far(self):
        _, body = self.spike_step()
        self.assertIn("Decisions so far", body)
        self.assertIn("cites", body)

    def test_pr_not_ready_until_answered_or_risk_accepted(self):
        _, body = self.spike_step()
        self.assertIn("not ready for review", body)
        self.assertIn("accepted the risk", body)
        self.assertIn("Open questions", body)

    def test_every_section_number_it_names_matches_the_template(self):
        sections = dict(template_sections())
        refs = re.findall(r"section (\d+) \(([^)]+)\)", read(PRD_SKILL))
        self.assertTrue(refs)
        for n, name in refs:
            self.assertEqual(sections.get(int(n)), name, "section %s" % n)
        # No bare "section N" left without its name.
        self.assertIsNone(re.search(r"section \d+\b(?! \()", read(PRD_SKILL)))

    def test_no_separate_spike_skill(self):
        self.assertFalse([d for d in os.listdir(os.path.join(ROOT, "skills")) if "spike" in d])


class Reviewer(unittest.TestCase):
    def test_reviewer_checks_the_section_and_one_spike_file_per_uncertainty(self):
        text = flat(read(REVIEWER))
        for words in ("PRD PR", "Technical uncertainties", "`docs/spikes/", "accepted the risk"):
            self.assertIn(words, text, words)

    def test_apb_review_points_the_reviewer_at_it(self):
        text = flat(read(REVIEW_SKILL))
        self.assertIn("Technical uncertainties", text)
        self.assertIn("`docs/spikes/", text)


NAMES = re.compile(r"\b(sketch-gallery|tahoe|vail|luke|sam)\b")


def names_in(text):
    """Project and people names in `text`, as whole words, ignoring case."""
    return NAMES.findall(text.lower())


class Timeless(unittest.TestCase):
    def test_no_years_project_or_people_names(self):
        for rel in CHANGED:
            text = read(rel)
            self.assertIsNone(re.search(r"\b20\d\d\b", text), rel)
            self.assertEqual(names_in(text), [], rel)

    def test_name_filter_matches_whole_words_only(self):
        # Both directions: a name is caught, a word that merely contains one is not.
        for text in ("Vail's cams", "ask Luke.", "a Sam project", "the Tahoe tracker", "see sketch-gallery"):
            self.assertNotEqual(names_in(text), [], text)
        for text in ("available", "prevail", "sample", "same", "lukewarm"):
            self.assertEqual(names_in(text), [], text)


if __name__ == "__main__":
    unittest.main()
