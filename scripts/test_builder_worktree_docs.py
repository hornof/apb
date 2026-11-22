"""Tests for issue #142: a builder stays in its worktree. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts

While building #130, a builder's first test run went to the session's main checkout, because a
builder's shell starts there, and so it tested main's code instead of the PR's. Step 3 of apb-build
gave each builder a worktree but said nothing about keeping it there. The rule now sits in step 3:
how the builder stays in its worktree, and that the orchestrator checks a builder's test results ran
in it before trusting them.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILL = "skills/apb-build/SKILL.md"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def flat(text):
    return " ".join(text.replace("*", "").split())


def step(text, n):
    """Step n of a numbered list, with its sub-bullets, up to the next step."""
    m = re.search(r"^%d\. (.*?)(?=^\d+\. |\Z)" % n, text, re.S | re.M)
    return m.group(1) if m else ""


class Step3(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.text = read(SKILL)
        cls.step3 = flat(step(cls.text, 3))

    def test_step_3_is_the_parallel_step(self):
        self.assertTrue(self.step3.startswith("Build independent issues in parallel."), self.step3[:80])

    def test_it_says_why_a_builder_leaves_its_worktree(self):
        for words in ("shell starts in the session's main checkout", "main"):
            self.assertIn(words, self.step3, words)

    def test_first_way_is_the_agent_tools_worktree_isolation(self):
        self.assertIn("worktree isolation", self.step3)
        self.assertIn('isolation: "worktree"', self.step3)
        self.assertIn("its shell starts in the worktree", self.step3)

    def test_an_isolated_builder_still_starts_from_fresh_main(self):
        # Note 3 on PR #158: isolation may branch the worktree from the session checkout's commit.
        self.assertIn("may start from the session checkout's current commit rather than fresh main", self.step3)
        self.assertIn("still branches off freshly fetched `origin/main` itself", self.step3)

    def test_second_way_is_a_brief_that_pins_every_command(self):
        for words in ("git worktree add", "the brief", "every command names the worktree with a folder option or "
                      "an absolute path", "`git -C <worktree>`",
                      "`python3 -m unittest discover -s <worktree>/scripts -t <worktree>/scripts`",
                      "a script that changes folder itself", "`agents/builder.md`", "so no command runs in the main checkout"):
            self.assertIn(words, self.step3, words)

    def test_the_brief_never_offers_cd_into_the_worktree(self):
        # Decision 4 on PR #158: `cd <worktree> &&` is out; agents/builder.md pins commands with folder options.
        self.assertNotIn("every command starts with `cd", self.step3)

    def test_it_checks_test_results_ran_in_the_worktree_before_trusting_them(self):
        self.assertIn("Before you trust a builder's test results, check that they ran in its worktree",
                      self.step3)
        self.assertIn("run them again in the worktree", self.step3)
        self.assertIn("the command names the worktree's path, or its output does", self.step3)

    def test_the_check_passes_for_an_isolated_builder(self):
        # Fix 1 on PR #158: an isolated builder's commands name no folder and unittest output names no path.
        self.assertIn("or the builder was launched with worktree isolation and the command names no other folder",
                      self.step3)

    def test_step_3_never_offers_cd_into_the_worktree(self):
        self.assertNotIn("cd <worktree>", self.step3)

    def test_it_names_the_main_checkout_not_an_undefined_default_folder(self):
        # Fix 2 on PR #158.
        self.assertNotIn("default folder", self.step3)

    def test_the_rule_is_only_in_step_3(self):
        # Steps 1 and 2 are edited by other cards; the rule stays in step 3 so the PRs merge cleanly.
        for n in (1, 2, 4):
            self.assertNotIn("so no command runs in the main checkout", flat(step(self.text, n)), "step %d" % n)


if __name__ == "__main__":
    unittest.main()
