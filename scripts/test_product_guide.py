"""Tests for the product guide, docs/product-guide.md. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts

The guide helps people do their own product management: whether a new idea belongs in the current
work, where it goes if not (the backlog), going back over the backlog, ranking it, and choosing what
to build next. A backlog idea is an issue labeled `backlog` that is not on the board; it goes on the
board, as a `build` card, only once it is chosen. The playbook, the README and apb-board point to it.
"""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUIDE = "docs/product-guide.md"
SECTIONS = ["Out of scope or not", "The backlog", "Grooming", "Priorities", "The next piece of work",
            "How the backlog shows on the board"]


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def headings(text, level="## "):
    return [line[len(level):].strip() for line in text.splitlines() if line.startswith(level)]


def section(text, name, level="## "):
    """The body of the heading `name`, up to the next heading of the same level."""
    m = re.search(r"^%s%s\n(.*?)(?=^%s|\Z)" % (re.escape(level), re.escape(name), re.escape(level)),
                  text, re.S | re.M)
    return m.group(1) if m else ""


def flat(text):
    return " ".join(text.replace("*", "").split())


class TheGuide(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read(GUIDE)
        cls.parts = {name: flat(section(cls.text, name)) for name in SECTIONS}

    def test_has_one_section_per_topic_in_order(self):
        self.assertEqual(headings(self.text), SECTIONS)
        for name, body in self.parts.items():
            self.assertGreater(len(body.split()), 40, name)

    def test_out_of_scope_tests_against_the_approved_prd(self):
        body = self.parts["Out of scope or not"]
        for words in ("PRD", "current card", "backlog", "the human"):
            self.assertIn(words, body, words)

    def test_backlog_is_a_labeled_issue_with_a_write_up(self):
        body = self.parts["The backlog"]
        for words in ("`backlog`", "gh label create backlog --force", "gh issue create",
                      "Where it came from", "Why it matters", "Size"):
            self.assertIn(words, body, words)

    def test_grooming_updates_merges_and_drops(self):
        body = self.parts["Grooming"]
        for words in ("Update", "Merge", "Drop", "not planned"):
            self.assertIn(words, body, words)

    def test_priorities_rank_by_value_then_check_size_against_closed_cards(self):
        body = self.parts["Priorities"]
        self.assertIn("value", body)
        self.assertIn("card-times.py --collect", body)
        self.assertLess(body.index("value"), body.index("card-times.py --collect"))
        self.assertIn("https://github.com/hornof/apb/issues/24", body)

    def test_next_piece_turns_the_idea_into_a_build_card(self):
        body = self.parts["The next piece of work"]
        for words in ("`build`", "--remove-label backlog", "Todo", "gh project item-add"):
            self.assertIn(words, body, words)

    def test_board_gets_no_new_column(self):
        body = self.parts["How the backlog shows on the board"]
        self.assertIn("no column", body)
        self.assertIn("label:backlog", body)
        # The columns it names are the board's real columns, as apb-board sets them up.
        board = read("skills/apb-board/SKILL.md")
        self.assertIn("Todo / In progress / In review / Done", board)
        self.assertIn("Todo, In progress, In review and Done", body)

    def test_a_backlog_issue_never_carries_build(self):
        # The map artifact counts `build` issues as cards; an idea must not count until chosen.
        text = flat(self.text)
        self.assertIn("never both", text)

    def test_one_name_for_the_label(self):
        for rel in (GUIDE, "docs/playbook.md", "README.md", "skills/apb-board/SKILL.md"):
            self.assertNotIn("future work", read(rel).lower(), rel)

    def test_collect_is_a_real_option_of_card_times(self):
        # Independent check: ask the script itself, rather than trusting its docstring.
        out = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "card-times.py"), "--help"],
                             capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("--collect", out.stdout)

    def test_every_repo_file_it_names_exists(self):
        # A project's own files, such as "the project's `docs/MAP.md`", aren't in apb; their template is.
        project_files = {"docs/MAP.md": "templates/MAP-template.md"}
        named = re.findall(r"`((?:agents|hooks|scripts|skills|templates|docs)/[\w./-]+)`", self.text)
        self.assertTrue(named)
        for rel in named:
            self.assertTrue(os.path.exists(os.path.join(ROOT, project_files.get(rel, rel))), rel)
        for rel in project_files:
            if rel in named:
                self.assertIn("project's `%s`" % rel, self.text, "%s is named as the project's own file" % rel)

    def test_every_playbook_section_it_points_to_exists(self):
        names = headings(read("docs/playbook.md"))
        refs = re.findall(r'playbook\'s "([^"]+)"', self.text)
        self.assertTrue(refs)
        for ref in refs:
            self.assertIn(ref, names, ref)

    def test_is_timeless(self):
        # A reusable doc: no years, no project names, no story of where a rule came from.
        self.assertIsNone(re.search(r"\b20\d\d\b", self.text))
        for word in ("sketch-gallery", "trial", "we learned"):
            self.assertNotIn(word, self.text.lower(), word)


class OtherDocsPointToIt(unittest.TestCase):
    def test_readme_lists_it(self):
        rows = section(read("README.md"), "What's in the box")
        self.assertIn("| `%s` |" % GUIDE, rows)

    def test_playbook_points_to_it(self):
        self.assertIn("`%s`" % GUIDE, section(read("docs/playbook.md"), "Helping the human keep track"))

    def test_apb_board_creates_the_label(self):
        board = read("skills/apb-board/SKILL.md")
        self.assertIn("gh label create backlog --force", board)
        self.assertIn("`%s`" % GUIDE, board)


if __name__ == "__main__":
    unittest.main()
