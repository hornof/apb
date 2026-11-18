"""Tests for the README's "How apb checks quality" section. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts

The section names the testing tools up front: for each kind of check, the tool, what it catches and
when it runs. It names only tools apb itself uses or tells projects to use, so every tool it names
must also be named somewhere else in apb. Its "when it runs" words match the CI template.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTION = "How apb checks quality"
HERE = os.path.basename(__file__)

# Well-known testing, CI and preview tools. The section may name any of them only if the rest of
# apb names it too. Listed here, outside the README, so the check doesn't depend on the section.
KNOWN_TOOLS = [
    "Vitest", "Jest", "Mocha", "pytest", "unittest", "fast-check", "Hypothesis", "Playwright",
    "Cypress", "Selenium", "Puppeteer", "Lighthouse", "axe", "pa11y", "Stryker", "mutmut",
    "PIT", "GitHub Actions", "CircleCI", "Travis", "Jenkins", "Cloudflare Pages", "Netlify",
    "Vercel", "GitHub Pages", "Codecov", "SonarQube", "ESLint", "Prettier",
]

# The web-project defaults the section must name: each is already in apb's templates or docs.
DEFAULTS = ["Vitest", "fast-check", "Playwright", "Lighthouse", "axe", "Stryker", "GitHub Actions",
            "Cloudflare Pages"]


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


def names(text, tool):
    # Case doesn't matter: the settings template names `npx stryker` and `npx vitest`.
    return re.search(r"(?<![\w-])%s(?![\w-])" % re.escape(tool), text, re.I) is not None


def table(body):
    """The rows of the first markdown table in `body`, as lists of cells, header first."""
    rows = [l.strip() for l in body.splitlines() if l.strip().startswith("|")]
    cells = [[c.strip() for c in r.strip("|").split("|")] for r in rows]
    return [c for c in cells if not all(re.fullmatch(r":?-+:?", x) for x in c)]


def rest_of_apb():
    """Every file in apb except the README and the test files, as one text."""
    texts = []
    for top in ("agents", "docs", "hooks", "scripts", "skills", "templates"):
        for folder, _, files in os.walk(os.path.join(ROOT, top)):
            for name in files:
                if name.startswith("test_") or name == HERE or name.endswith(".pyc"):
                    continue
                with open(os.path.join(folder, name), encoding="utf-8") as f:
                    texts.append(f.read())
    return "\n".join(texts)


class Section(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.readme = read("README.md")
        cls.body = section(cls.readme, SECTION)
        cls.text = flat(cls.body)
        cls.rows = table(cls.body)

    def test_is_near_the_top(self):
        order = headings(self.readme)
        self.assertIn(SECTION, order)
        self.assertEqual(order[order.index(SECTION) - 1], "The process")
        self.assertLess(order.index(SECTION), order.index("What's in the box"))

    def test_table_gives_tool_catch_and_when_for_each_check(self):
        self.assertEqual([h.lower() for h in self.rows[0]], ["check", "tool", "what it catches", "when it runs"])
        self.assertGreaterEqual(len(self.rows), 5)
        for row in self.rows[1:]:
            self.assertEqual(len(row), 4, row)
            for cell in row:
                self.assertTrue(cell, row)

    def test_each_tool_cell_names_a_known_tool_or_a_person(self):
        for row in self.rows[1:]:
            tool = row[1]
            self.assertTrue(any(names(tool, t) for t in KNOWN_TOOLS) or "by hand" in tool, tool)

    def test_names_the_web_defaults(self):
        for tool in DEFAULTS:
            self.assertTrue(names(self.text, tool), tool)

    def test_names_no_tool_the_rest_of_apb_does_not(self):
        rest = rest_of_apb()
        for tool in KNOWN_TOOLS:
            if names(self.text, tool):
                self.assertTrue(names(rest, tool), "%s is named only in the README" % tool)

    def test_says_each_tdd_picks_and_these_are_the_web_defaults(self):
        self.assertRegex(self.text, r"TDD picks")
        self.assertRegex(self.text, r"defaults for web projects")

    def test_when_it_runs_matches_the_ci_template(self):
        # The same words must be in the template, so a change to one shows up against the other.
        template = flat(read("templates/ci-template.yml").replace("#", " "))
        for words in ("quick set", "full set", "once per PR", "`full-tests` label", "every push to main",
                      "docs only", "unit tests run every time"):
            # Whole words: "once per PR" must not match "Once per project".
            pattern = r"(?<!\w)%s(?!\w)" % re.escape(words)
            self.assertRegex(self.text, re.compile(pattern, re.I), words)
            self.assertRegex(template, re.compile(pattern, re.I), words)

    def test_the_old_core_rules_sentence_is_gone(self):
        # One place names the tools; the core rules don't repeat an older, shorter list.
        self.assertNotIn("default verifiers", self.readme)


class ApbsOwnCode(unittest.TestCase):
    """The section says apb's own code is checked the same way. These keep that true."""

    def test_the_section_says_so(self):
        self.assertIn("apb's own code is checked the same way", flat(section(read("README.md"), SECTION)))

    def test_every_script_and_hook_has_a_test_file(self):
        for top in ("scripts", "hooks"):
            for name in os.listdir(os.path.join(ROOT, top)):
                if name.endswith(".py") and not name.startswith("test_"):
                    test = "test_" + name[:-3].replace("-", "_") + ".py"
                    path = os.path.join(ROOT, top, test)
                    self.assertTrue(os.path.exists(path), "%s/%s" % (top, name))
                    with open(path, encoding="utf-8") as f:
                        self.assertIn("random.Random", f.read(), "%s/%s has no random-input tests" % (top, test))

    def test_random_inputs_checked_against_a_second_implementation(self):
        text = read("scripts/test_card_times.py")
        self.assertIn("random.Random", text)
        self.assertRegex(text, r"def oracle\(")

    def test_every_child_process_has_a_time_limit_is_tested(self):
        self.assertIn("def test_every_subprocess_call_has_a_timeout", read("scripts/test_apb_watch.py"))


if __name__ == "__main__":
    unittest.main()
