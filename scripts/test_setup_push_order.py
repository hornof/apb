"""Tests for the order of pushes in apb-setup. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts -p test_setup_push_order.py

The initial commit is the one direct push to main a project ever gets: there is no branch to open
a PR from yet. The deny list in `.claude/settings.json` forbids pushing to main, and Claude Code
applies it as soon as the file exists in the folder. So setup pushes the initial commit first,
puts the deny list and every later setup change on a branch, and lands that branch through a PR
the human merges on GitHub. The human never runs a git command, and at the end Claude checks
that a direct push to main is refused.
"""
import json
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = "skills/apb-setup/SKILL.md"
SETTINGS_TEMPLATE = "templates/claude-settings-template.json"
CHECK_PUSH = "git push origin main"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def flat(text):
    return " ".join(text.replace("*", "").split())


def steps():
    """Top-level numbered steps of apb-setup, keyed by their label ("1", "1a", "4", ...)."""
    parts = re.split(r"^(\d+[a-z]?)\. ", read(SETUP), flags=re.M)
    return {parts[i]: flat(parts[i + 1]) for i in range(1, len(parts), 2)}


def substep(step, letter):
    """A lettered part (`c.`) of a numbered step, up to the next lettered part."""
    body = steps()[step]
    m = re.search(r"(?:^| )%s\. (.*?)(?= [a-z]\. |$)" % letter, body)
    return m.group(1)


def initial_commit_files():
    body = steps()["1"]
    start = body.index("Push an initial commit")
    end = body.index("Fill in every other placeholder", start)
    return body[start:end]


def deny_matches(command, rules):
    """True if a Claude Code `Bash(...)` deny rule matches the command (trailing ` *` = any args)."""
    for rule in rules:
        pattern = rule[len("Bash("):-1]
        if pattern.endswith(" *"):
            prefix = pattern[:-2]
            if command == prefix or command.startswith(prefix + " "):
                return True
        elif command == pattern:
            return True
    return False


class InitialCommitComesFirst(unittest.TestCase):
    def test_initial_commit_is_pushed_straight_to_main(self):
        self.assertIn("Push an initial commit straight to main", steps()["1"])

    def test_it_is_the_only_direct_push(self):
        self.assertIn("the only direct push to main", steps()["1"])

    def test_initial_commit_leaves_the_deny_list_out(self):
        files = initial_commit_files()
        self.assertNotIn("claude-settings-template", files)
        self.assertNotIn(".claude/settings.json", files)
        # Step 1 says so in words, and why.
        body = steps()["1"]
        self.assertRegex(body, r"`\.claude/settings\.json`[^.]*not in (it|the initial commit)")

    def test_push_is_written_before_the_deny_list_is_copied(self):
        text = read(SETUP)
        self.assertLess(text.index("Push an initial commit"),
                        text.index("Copy `templates/claude-settings-template.json`"))


class LaterChangesGoOnABranch(unittest.TestCase):
    def test_step_1_starts_the_setup_branch_after_the_push(self):
        body = steps()["1"]
        self.assertIn("`setup` branch", body)
        self.assertLess(body.index("Push an initial commit"), body.index("`setup` branch"))

    def test_deny_list_is_committed_on_the_setup_branch_not_main(self):
        body = substep("4", "c")
        self.assertIn("claude-settings-template.json", body)
        self.assertIn("commit it on the `setup` branch", body)
        self.assertNotRegex(body, r"commit it(,| so| and push)")

    def test_step_4c_says_why_the_initial_push_comes_first(self):
        body = substep("4", "c")
        self.assertIn("as soon as the file exists", body)
        self.assertIn("refused", body)


class SetupLandsThroughAPr(unittest.TestCase):
    def last_step(self):
        s = steps()
        return s[max(s, key=lambda k: (int(re.match(r"\d+", k).group()), k))]

    def test_last_step_lands_the_setup_branch(self):
        body = self.last_step()
        for words in ("`setup` branch", "gh pr create", "the human merges it on GitHub",
                      "git pull"):
            self.assertIn(words, body)

    def test_last_step_checks_a_direct_push_is_refused(self):
        body = self.last_step()
        self.assertIn("`%s`" % CHECK_PUSH, body)
        self.assertIn("must be refused", body)
        # The check runs only after main is pulled, so if the deny list fails the push is a no-op.
        self.assertLess(body.index("git pull"), body.index("`%s`" % CHECK_PUSH))
        self.assertIn("Everything up-to-date", body)

    def test_check_command_is_one_the_template_denies(self):
        # Independent oracle: the command the skill uses for the check must hit a deny rule.
        rules = json.loads(read(SETTINGS_TEMPLATE))["permissions"]["deny"]
        self.assertTrue(deny_matches(CHECK_PUSH, rules), rules)

    def test_deny_matcher_is_not_vacuous(self):
        rules = json.loads(read(SETTINGS_TEMPLATE))["permissions"]["deny"]
        self.assertFalse(deny_matches("git push -u origin setup", rules))
        self.assertFalse(deny_matches("git push origin mainline", rules))
        self.assertTrue(deny_matches("git push --force origin x", rules))


class HumanRunsNoGitCommand(unittest.TestCase):
    def test_skill_says_the_human_never_runs_git(self):
        self.assertIn("The human never runs a git command during setup",
                      flat(read(SETUP)))

    def test_no_sentence_hands_a_git_command_to_the_human(self):
        for sentence in re.split(r"(?<=[.!?])\s", flat(read(SETUP))):
            if re.search(r"`git (push|commit|pull|merge)", sentence):
                self.assertNotRegex(sentence.lower(),
                                    r"(ask|tell) the human to run|the human runs `git",
                                    sentence)


class Timeless(unittest.TestCase):
    def test_no_years_project_or_people_names(self):
        names = re.compile(r"\b(sketch-gallery|tahoe|vail|luke|sam|inktober)\b")
        text = read(SETUP)
        self.assertIsNone(re.search(r"\b20\d\d\b", text))
        self.assertEqual(names.findall(text.lower()), [])


if __name__ == "__main__":
    unittest.main()
