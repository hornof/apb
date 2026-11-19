"""Tests for how the docs describe Claude's own GitHub account. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts

apb-setup has one step that gives Claude Code its own GitHub account: what the human makes, how the
token is handed over and stored, how commits show the account, and how the account gets into every
repo and the board. The project CLAUDE.md template, the playbook, the map template and the README
agree with it, and no file in the repo holds a token.
"""
import os
import re
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def flat(text):
    return " ".join(text.replace("*", "").split())


def setup_step(text):
    """The apb-setup step about Claude's GitHub account: from its number to the next top-level step."""
    m = re.search(r"^(\d+)\. \*\*Claude's GitHub account\.\*\*(.*?)(?=^\d+[a-z]?\. |^Do NOT|\Z)", text, re.S | re.M)
    return (int(m.group(1)), m.group(2)) if m else (None, "")


class SetupStep(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.skill = read("skills/apb-setup/SKILL.md")
        cls.number, body = setup_step(cls.skill)
        cls.text = flat(body)

    def test_exists(self):
        self.assertIsNotNone(self.number, "apb-setup has no step called \"Claude's GitHub account.\"")

    def test_comes_after_the_repo_and_the_board(self):
        # With GH_TOKEN set, `gh repo create` would make the repo under Claude's account.
        self.assertGreater(self.number, 3)

    def test_says_what_the_human_makes(self):
        for words in ("machine account", "two-factor", "classic", "fine-grained", "user-owned"):
            self.assertIn(words, self.text, words)

    def test_names_the_four_scopes(self):
        for scope in ("`repo`", "`project`", "`read:org`", "`workflow`"):
            self.assertIn(scope, self.text, scope)

    def test_says_why_workflow(self):
        self.assertIn(".github/workflows/", self.text)

    def test_admin_settings_run_as_the_human(self):
        for words in ("admin rights", "branch protection", "Pages", "secrets"):
            self.assertIn(words, self.text, words)

    def test_renewal_reaches_every_copy(self):
        self.assertIn("every project folder that uses the account", self.text)

    def test_refuses_the_humans_own_token(self):
        self.assertIn("own token", self.text)

    def test_says_why_read_org(self):
        for words in ("unknown owner type", "gh project"):
            self.assertIn(words, self.text, words)

    def test_token_goes_in_a_file_never_the_chat(self):
        for words in ("text file", "never in the chat", "claude-account.py"):
            self.assertIn(words, self.text, words)

    def test_where_the_token_lives(self):
        for words in (".claude/settings.local.json", "git-ignored", "GH_TOKEN", "gh help environment"):
            self.assertIn(words, self.text, words)

    def test_git_author(self):
        for words in ("GIT_AUTHOR_NAME", "GIT_AUTHOR_EMAIL", "GIT_COMMITTER_NAME", "GIT_COMMITTER_EMAIL",
                      "users.noreply.github.com"):
            self.assertIn(words, self.text, words)

    def test_access_to_every_repo_and_the_board(self):
        for words in ("every repo", "env -u GH_TOKEN", "collaborators", "repository_invitations",
                      "updateProjectV2Collaborators", "WRITER"):
            self.assertIn(words, self.text, words)

    def test_how_to_check(self):
        for words in ("gh auth status", "(GH_TOKEN)", "docs/MAP.md"):
            self.assertIn(words, self.text, words)

    def test_one_account_for_all_roles(self):
        for words in ("orchestrator", "builder", "reviewer", "advisor", "signature"):
            self.assertIn(words, self.text, words)

    def test_reuse_and_renewal(self):
        for words in ("earlier project", "expires"):
            self.assertIn(words, self.text, words)

    def test_steps_are_numbered_in_order(self):
        numbers = [int(n) for n in re.findall(r"^(\d+)\. ", self.skill, re.M)]
        self.assertEqual(numbers, list(range(numbers[0], numbers[0] + len(numbers))))

    def test_description_names_it(self):
        front = self.skill.split("---")[1]
        self.assertIn("Claude's GitHub account", front)


class OtherDocs(unittest.TestCase):
    def test_claude_template_signature_rule(self):
        text = flat(read("templates/CLAUDE-template.md"))
        self.assertNotIn("Skip this once Claude has its own GitHub account", text)
        self.assertIn("— Claude (orchestrator)", text)
        self.assertIn("its own GitHub account", text)

    def test_playbook_says_who_github_shows(self):
        text = flat(read("docs/playbook.md"))
        self.assertIn("Claude's own GitHub account", text)
        self.assertNotIn("GitHub doesn't let you approve your own PR, so", text)

    def test_apb_tdd_points_to_the_human_for_admin_settings(self):
        text = flat(read("skills/apb-tdd/SKILL.md"))
        self.assertIn("env -u GH_TOKEN", text)
        self.assertIn("apb-setup step 5", text)

    def test_script_docstrings_no_longer_say_claude_always_uses_the_humans_login(self):
        for rel in ("scripts/decision-check.py", "scripts/card-times.py"):
            text = flat(read(rel))
            self.assertNotIn("since Claude uses the human's login", text, rel)
            self.assertIn("since Claude may use the human's login (apb-setup step 5)", text, rel)

    def test_map_template_has_a_row(self):
        self.assertIn("Claude's GitHub account", read("templates/MAP-template.md"))

    def test_readme_lists_the_script(self):
        self.assertIn("`scripts/claude-account.py`", read("README.md"))


class NoTokenInTheRepo(unittest.TestCase):
    def test_no_tracked_file_holds_a_token(self):
        files = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True,
                               timeout=30, check=True).stdout.split()
        pattern = re.compile(r"\b(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}\b|\bgithub_pat_[A-Za-z0-9_]{60,}")
        for rel in files:
            if rel == "scripts/test_claude_account.py":
                continue  # holds a made-up token on purpose
            try:
                text = read(rel)
            except (UnicodeDecodeError, FileNotFoundError):
                continue
            self.assertIsNone(pattern.search(text), rel)


if __name__ == "__main__":
    unittest.main()
