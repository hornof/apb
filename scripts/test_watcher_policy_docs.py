"""Tests for issue #131: the watcher's job is written down once, apart from how each watcher runs.
Run from the repo root:
python3 -m unittest discover -s scripts -t scripts -p test_watcher_policy_docs.py

The playbook's "What every watcher does" holds the policy, which names no single implementation,
then lists each implementation with where it runs and its known limits. On #132 a builder worked
in a local worktree for about 15 minutes with nothing on GitHub, so every watcher came back quiet.
The fix is a policy, not a listed limit: a builder pushes its branch and opens a draft PR as soon
as it starts, and marks it ready for review when the build is done. agents/builder.md, the
apb-build skill and the playbook all say the same.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTION = "What every watcher does"
IMPLEMENTATIONS = "**How each watcher runs today.**"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def section(text, name):
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(name), text, re.S | re.M)
    return m.group(1) if m else ""


def flat(text):
    return " ".join(text.replace("*", "").split())


def parts():
    """(policy, implementations) of the playbook's watcher section."""
    body = section(read("docs/playbook.md"), SECTION)
    if IMPLEMENTATIONS not in body:
        return body, ""
    policy, impl = body.split(IMPLEMENTATIONS, 1)
    return policy, impl


class TestPolicy(unittest.TestCase):
    def test_section_exists_once(self):
        text = read("docs/playbook.md")
        self.assertEqual(len(re.findall(r"^## %s$" % re.escape(SECTION), text, re.M)), 1)
        self.assertIn(IMPLEMENTATIONS, section(text, SECTION))

    def test_policy_says_what_every_watcher_does(self):
        policy = flat(parts()[0])
        for words in ("PRs opened, closed or merged", "comments", "reviews", "new issues",
                      "board moves", "one line", "heartbeat", "2 minutes", "15 minutes",
                      "every hour", "any change", "GitHub API budget"):
            self.assertIn(words, policy)

    def test_policy_names_no_implementation(self):
        # A broken watcher is swapped without touching the policy, so the policy names none.
        policy = parts()[0]
        for name in ("apb-watch", "hook", "Cowork", "Mac", "cloud", "orchestrator", "advisor",
                     "Claude Code", "background job"):
            self.assertNotIn(name, policy, name)

    def test_policy_makes_builds_visible_from_the_start(self):
        policy = flat(parts()[0])
        self.assertIn("draft PR", policy)
        self.assertIn("issue-<N>-", policy)
        self.assertIn("ready for review", policy)
        self.assertIn("#132", policy)


class TestImplementations(unittest.TestCase):
    def items(self):
        impl = parts()[1]
        return [flat(b) for b in re.split(r"^- ", impl, flags=re.M)[1:]]

    def test_each_current_watcher_is_listed_with_where_and_limits(self):
        items = self.items()
        orch = [b for b in items if b.startswith("The orchestrator's watcher")]
        adv = [b for b in items if b.startswith("The advisor's watcher")]
        self.assertEqual(len(orch), 1)
        self.assertEqual(len(adv), 1)
        for b in orch + adv:
            self.assertIn("Runs:", b)
            self.assertIn("Known limits:", b)
        self.assertIn("Mac", orch[0])
        self.assertIn("hooks/apb-watch-start.py", orch[0])
        self.assertIn("scripts/apb-watch.py", orch[0])
        self.assertIn("Cowork", adv[0])
        self.assertIn("cloud chat", adv[0])
        self.assertIn("Broken since Nov 21", adv[0])
        self.assertIn("timed messages", adv[0])

    def test_orchestrator_detail_points_back(self):
        # "While Claude waits" is the orchestrator's implementation; it points to the policy.
        waits = flat(section(read("docs/playbook.md"), "While Claude waits"))
        self.assertIn('"What every watcher does"', waits)


class TestDraftPrRule(unittest.TestCase):
    def test_builder_opens_a_draft_pr_first(self):
        text = flat(read("agents/builder.md"))
        self.assertIn("gh pr create --draft", text)
        self.assertIn("gh pr ready", text)
        self.assertIn("issue-<N>-", text)
        self.assertIn("before you write any test or code", text)
        # The old wording that put the PR at the end is gone.
        self.assertNotIn("before you open the PR", text)
        self.assertNotIn("Before you open the PR", text)
        self.assertNotIn("Push when the work is ready for review", text)

    def test_apb_build_moves_the_card_when_the_pr_is_ready(self):
        text = flat(read("skills/apb-build/SKILL.md"))
        self.assertIn("draft PR", text)
        self.assertIn("When the builder marks the PR ready for review, move the card", text)
        self.assertIn("Before the builder marks the PR ready for review", text)
        self.assertNotIn("When the PR is open, move the card", text)
        self.assertNotIn("Before the builder opens the PR", text)

    def test_playbook_says_the_same(self):
        text = read("docs/playbook.md")
        steps = flat(section(text, "The steps"))
        self.assertIn("opens a draft PR as soon as it starts", steps)
        self.assertNotIn("then opens one small PR", steps)
        who = flat(section(text, "Who does what"))
        self.assertIn("opens a draft PR when it starts", who)


if __name__ == "__main__":
    unittest.main()
