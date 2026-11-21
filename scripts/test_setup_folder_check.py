"""Tests for apb-setup's folder check (step 0). Run from the repo root:
python3 -m unittest discover -s scripts -t scripts -p test_setup_folder_check.py

The project's `.claude/settings.json` (deny list) and `.claude/settings.local.json` (Claude's
GitHub account and git author) apply only to a session opened in the project folder. A setup run
from another folder commits as the human and can't check that the deny list refuses a push to
main. So setup starts by comparing the session's folder with the project folder and, if they
differ, stops with one instruction and does nothing else. With the session in the right folder,
step 7's check is one command in the same session.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = "skills/apb-setup/SKILL.md"
INSTRUCTION = ("open a new Code session with the folder set to `<project folder>` "
               "and run `/apb-setup` there")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def flat(text):
    return " ".join(text.replace("*", "").split())


def steps():
    parts = re.split(r"^(\d+[a-z]?)\. ", read(SETUP), flags=re.M)
    return {parts[i]: flat(parts[i + 1]) for i in range(1, len(parts), 2)}


def first_step_label():
    return re.search(r"^(\d+[a-z]?)\. ", read(SETUP), flags=re.M).group(1)


class FolderCheckComesFirst(unittest.TestCase):
    def test_step_0_is_the_first_step(self):
        self.assertEqual(first_step_label(), "0")

    def test_step_0_opens_with_the_folder_check(self):
        body = steps()["0"]
        self.assertTrue(body.startswith("Right folder"), body[:80])
        self.assertIn("project folder", body)
        # The check comes before the prerequisites within step 0.
        self.assertLess(body.index("project folder"), body.index("gh auth status"))

    def test_check_is_written_before_any_github_or_git_action(self):
        # The intro names `gh repo create` as an example; the steps start at "0. ".
        full = read(SETUP)
        text = full[re.search(r"^0\. ", full, flags=re.M).start():]
        check = text.index("Right folder")
        for action in ("gh auth status", "gh repo create", "git switch", "Push an initial commit",
                       "Projects board"):
            self.assertLess(check, text.index(action), action)

    def test_check_compares_resolved_folders(self):
        # /tmp and /private/tmp are the same folder on a Mac; compare real paths.
        self.assertIn("pwd -P", steps()["0"])


class WrongFolderStops(unittest.TestCase):
    def test_gives_the_one_line_instruction(self):
        self.assertIn(INSTRUCTION, steps()["0"])

    def test_instruction_is_one_line_in_the_source(self):
        line = next(l for l in read(SETUP).splitlines() if "run `/apb-setup` there" in l)
        self.assertIn("open a new Code session", line)

    def test_says_stop_and_do_nothing_else(self):
        body = steps()["0"]
        self.assertIn("stop", body)
        self.assertIn("Do nothing else first", body)
        for thing in ("repo", "board", "commit"):
            self.assertRegex(body, r"Do nothing else first[^.]*\b%s" % thing, thing)

    def test_says_why(self):
        body = steps()["0"]
        for words in (".claude/settings.json", ".claude/settings.local.json", "deny list"):
            self.assertIn(words, body)


class OneLineFolderQuestion(unittest.TestCase):
    """Issue #114 (comment): the folder check is asked in one line, with the folder in bold; the
    rest is explained only if the answer is no."""

    QUESTION = "Setting up in **<project folder>** — is that right?"

    def test_question_is_one_line_with_the_folder_in_bold(self):
        line = next((l for l in read(SETUP).splitlines() if "is that right?" in l), "")
        self.assertIn(self.QUESTION, line)

    def test_rest_comes_only_after_a_no(self):
        body = steps()["0"]
        q = body.index("is that right?")
        self.assertRegex(body[q:], r"Only if the answer is no")
        self.assertLess(q, body.index("open a new Code session"))

    def test_folders_are_compared_whatever_the_answer(self):
        # A yes must not skip the comparison: a human who named another folder and says yes
        # would otherwise get setup in the wrong session.
        body = steps()["0"]
        self.assertRegex(body[body.index("is that right?"):],
                         r"whatever the answer, compare the project folder with this session's working folder")


def substep(step, letter):
    body = steps()[step]
    m = re.search(r"(?:^| )%s\. (.*?)(?= [a-z]\. |$)" % letter, body)
    return m.group(1)


class SetupCommitsAreByTheAccount(unittest.TestCase):
    """Changes for the `setup` branch wait for step 5e, so every `setup` commit has the account
    as its author (or the human, if they said no to the account)."""

    def test_step_1_holds_setup_changes_until_step_5e(self):
        body = steps()["1"]
        self.assertIn("made but not committed until step 5e shows the account", body)
        self.assertIn("said no to it", body)
        self.assertRegex(body, r"commit them all at once")

    def test_step_4c_does_not_commit_before_5e(self):
        body = substep("4", "c")
        self.assertRegex(body, r"commit it on the `setup` branch[^.]*after step 5e")

    def test_step_5e_commits_the_held_changes(self):
        body = substep("5", "e")
        self.assertRegex(body, r"commit the `setup` branch changes")
        # The commit comes after the author check in 5e.
        self.assertLess(body.index("git var GIT_AUTHOR_IDENT"),
                        body.index("commit the `setup` branch changes"))

    def test_no_step_before_5e_commits(self):
        # Steps 1b to 4 change files for the branch but never tell Claude to commit them now.
        s = steps()
        for label in ("1a", "1b", "2", "3", "4"):
            body = s[label]
            for m in re.finditer(r"(\w+ )?\bcommit(s|ted)?\b[^.]*", body):
                self.assertRegex(m.group(0), r"after step 5e|until step 5e|initial commit|"
                                 r"no placeholder is ever committed",
                                 (label, m.group(0)))

    def test_no_account_commits_right_away(self):
        body = steps()["5"]
        self.assertRegex(body, r"says no[^.]*commits the `setup` branch changes")


class Step7IsOneCommandHere(unittest.TestCase):
    def test_check_runs_in_this_session(self):
        body = steps()["7"]
        self.assertRegex(body, r"`git push origin main`[^.]*in this session")
        self.assertIn("one command", body)

    def test_no_second_claude_for_the_check(self):
        text = read(SETUP).lower()
        for words in ("headless", "claude -p", "claude --print", "--print"):
            self.assertNotIn(words, text, words)


if __name__ == "__main__":
    unittest.main()
