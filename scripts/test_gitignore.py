"""Tests for the apb repo's own .gitignore (#122). Run from the repo root:
python3 -m unittest discover scripts

The bot token for Code sessions in this repo lives in .claude/settings.local.json, so that file
must never be committable. The other direction matters as much: no file the plugin ships may
become ignored by accident. Both checks ask git itself (`git check-ignore`, `git ls-files`), with
the user's global excludes file switched off, so only the repo's own .gitignore counts.
"""
import os
import tempfile
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Local-only files and folders: secrets, worktrees, hook state, caches.
MUST_IGNORE = [
    ".claude/settings.local.json",
    ".claude/worktrees/some-branch/README.md",
    ".apb/events.jsonl",
    "scripts/__pycache__/test_gitignore.cpython-312.pyc",
    "hooks/__pycache__/apb-stop.cpython-312.pyc",
    ".DS_Store",
    "skills/apb-build/.DS_Store",
]

# Files that must stay committable, including ones that do not exist yet but sit next to an
# ignored path (a shared .claude/settings.json is the obvious one).
MUST_TRACK = [
    ".claude-plugin/plugin.json",
    ".claude-plugin/marketplace.json",
    ".claude/settings.json",
    ".gitignore",
    "README.md",
    "hooks/hooks.json",
    "scripts/claude-account.py",
    "templates/ci-template.yml",
]


def git(*args):
    return subprocess.run(
        ["git", "-c", "core.excludesFile=/dev/null", "-C", ROOT, *args],
        capture_output=True, text=True, timeout=30)


def ignored(path):
    # --no-index: answer from the patterns alone, even if the path happens to be tracked.
    out = git("check-ignore", "--no-index", "-q", path)
    if out.returncode not in (0, 1):
        raise RuntimeError(out.stderr)
    return out.returncode == 0


class RepoGitignore(unittest.TestCase):
    def test_gitignore_is_tracked(self):
        self.assertTrue(os.path.isfile(os.path.join(ROOT, ".gitignore")))
        self.assertEqual(git("ls-files", "--error-unmatch", ".gitignore").returncode, 0,
                         ".gitignore must be committed, not just present")

    def test_token_file_is_ignored(self):
        self.assertTrue(ignored(".claude/settings.local.json"),
                        ".claude/settings.local.json holds the bot token and must be ignored")

    def test_local_files_are_ignored(self):
        for path in MUST_IGNORE:
            with self.subTest(path=path):
                self.assertTrue(ignored(path), path + " should be ignored")

    def test_shipped_files_are_not_ignored(self):
        for path in MUST_TRACK:
            with self.subTest(path=path):
                self.assertFalse(ignored(path), path + " must stay committable")

    def test_no_tracked_file_matches_an_ignore_pattern(self):
        # Every file git tracks today, checked against the patterns: none may be ignored.
        out = git("ls-files", "-ci", "--exclude-standard")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(out.stdout.split(), [], "tracked files that .gitignore now covers")

    def test_token_file_never_shows_in_status(self):
        # The issue's "done when": git status never lists the token file. Use a throwaway copy of
        # the .gitignore in a temp repo so this test never writes a token-shaped file into the repo.
        with tempfile.TemporaryDirectory() as tmp:
            subprocess.run(["git", "init", "-q", tmp], check=True, timeout=30)
            with open(os.path.join(ROOT, ".gitignore"), encoding="utf-8") as src, \
                    open(os.path.join(tmp, ".gitignore"), "w", encoding="utf-8") as dst:
                dst.write(src.read())
            os.makedirs(os.path.join(tmp, ".claude"))
            for name in ("settings.local.json", "settings.json"):
                with open(os.path.join(tmp, ".claude", name), "w", encoding="utf-8") as f:
                    f.write("{}\n")
            out = subprocess.run(
                ["git", "-c", "core.excludesFile=/dev/null", "-C", tmp, "status",
                 "--porcelain", "--untracked-files=all"],
                capture_output=True, text=True, timeout=30, check=True)
            listed = [line[3:] for line in out.stdout.splitlines()]
            self.assertNotIn(".claude/settings.local.json", listed)
            self.assertIn(".claude/settings.json", listed)


if __name__ == "__main__":
    unittest.main()
