"""Tests for issue #145: apb keeps a list of projects in use. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts

The playbook's rule "An apb change reaches the projects already using apb in the same round" said to
apply a change to "every project in use", but apb kept no list of them. The orchestrator searched the
Mac's folders for an apb-style CLAUDE.md, which on PR #144 also found tahoe-tracker, a project no
longer in use (2026-11-21). Now one list, `docs/projects.md`, names each project's repo and whether
it is in use, and the rule reads it: a change goes to every project marked in use, and to no other.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = "docs/projects.md"
RULE = "An apb change reaches the projects already using apb in the same round."
# A row of the list: | owner/repo | yes | (the repo may be in backticks).
ROW = re.compile(r"^\|\s*`?([^|`\s]+)`?\s*\|\s*([^|]*?)\s*\|\s*$")
REPO = re.compile(r"^[\w.-]+/[\w.-]+$")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def section(text, name):
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(name), text, re.S | re.M)
    return m.group(1) if m else ""


def flat(text):
    return " ".join(text.replace("*", "").split())


def rows(text):
    """The list's entries as (repo, in use) pairs, in order. Header and divider rows are skipped."""
    out = []
    for line in text.splitlines():
        m = ROW.match(line.strip())
        if m and REPO.match(m.group(1)):
            out.append((m.group(1), m.group(2)))
    return out


def the_rule(playbook):
    """The playbook bullet that starts with the projects rule, flattened, or None."""
    items = [flat(b)[2:] for b in re.split(r"\n(?=- )", "\n" + playbook) if b.startswith("- ")]
    found = [b for b in items if b.startswith(RULE)]
    return found[0] if len(found) == 1 else None


class TheList(unittest.TestCase):
    def setUp(self):
        self.text = read(LIST)
        self.rows = rows(self.text)

    def test_starting_entries(self):
        entries = dict(self.rows)
        self.assertEqual(entries.get("hornof/sketch-gallery"), "yes")
        self.assertEqual(entries.get("hornof/tahoe-tracker"), "no")

    def test_every_row_is_well_formed(self):
        self.assertGreaterEqual(len(self.rows), 2)
        for repo, in_use in self.rows:
            self.assertRegex(repo, REPO)
            self.assertIn(in_use, ("yes", "no"), repo)

    def test_each_repo_once(self):
        repos = [r.lower() for r, _ in self.rows]
        self.assertEqual(len(repos), len(set(repos)))

    def test_columns_are_named(self):
        self.assertRegex(self.text, r"(?m)^\|\s*Repo\s*\|\s*In use\s*\|\s*$")

    def test_says_how_to_add_and_retire_a_project(self):
        text = flat(self.text)
        # Adding is a new row; a project no longer in use keeps its row with "no", so it is never
        # found again by a search and taken for one in use.
        self.assertIn("add a row", text)
        self.assertIn('change its In use to "no"', text)
        self.assertIn("keep the row", text)
        # The list is the only source: no search of the computer.
        self.assertIn("never by searching", text)

    def test_points_back_at_the_rule(self):
        self.assertIn(RULE.rstrip("."), flat(self.text))


class OnePlace(unittest.TestCase):
    """The list lives in one place: no other file in the repo holds a row of it."""

    def test_no_other_file_lists_projects(self):
        others = []
        for top in ("docs", "agents", "skills", "templates", "hooks", "scripts"):
            for folder, _, files in os.walk(os.path.join(ROOT, top)):
                for name in files:
                    path = os.path.join(folder, name)
                    rel = os.path.relpath(path, ROOT)
                    if rel == LIST or not name.endswith(".md"):
                        continue
                    with open(path, encoding="utf-8") as f:
                        if any(r == "hornof/sketch-gallery" for r, _ in rows(f.read())):
                            others.append(rel)
        self.assertEqual(others, [])


class Playbook(unittest.TestCase):
    def setUp(self):
        self.rule = the_rule(section(read("docs/playbook.md"), "Helping the human keep track"))

    def test_rule_names_the_list(self):
        self.assertIsNotNone(self.rule, "exactly one bullet starts with the projects rule")
        self.assertIn("`%s`" % LIST, self.rule)

    def test_rule_reads_the_list_and_nothing_else(self):
        self.assertIn("every project marked in use", self.rule)
        self.assertIn("and to no other", self.rule)
        self.assertIn("never by searching", self.rule)
        # The old wording, which had no list behind it, is gone.
        self.assertNotIn("every project in use", self.rule)

    def test_rule_says_the_list_is_where_projects_change(self):
        self.assertIn("by editing that list", self.rule)

    def test_rule_keeps_the_projects_line(self):
        # The Stop hook still reads the Projects line; the list changes which projects, not the line.
        self.assertIn("`Projects: <links to those PRs>`", self.rule)
        self.assertIn("`Projects: none`", self.rule)


class Oracle(unittest.TestCase):
    """The helpers find what they should and nothing else (guards against a vacuous pass)."""

    def test_rows(self):
        table = "| Repo | In use |\n|---|---|\n| a/b | yes |\n| `c/d` | no |\nnot | a/x | row\n"
        self.assertEqual(rows(table), [("a/b", "yes"), ("c/d", "no")])

    def test_rule_inside_another_bullet_is_not_found(self):
        self.assertIsNone(the_rule("- Other text. " + RULE + "\n"))

    def test_rule_found_with_wrapped_lines(self):
        self.assertEqual(the_rule("- **" + RULE + "**\n  more\n- next\n"), RULE + " more")


if __name__ == "__main__":
    unittest.main()
