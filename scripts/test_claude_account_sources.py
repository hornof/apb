"""Tests for claude-account-sources.py. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts -p test_claude_account_sources.py

apb-setup step 5 asks one question when an earlier apb project already holds the token of Claude's
GitHub account: "Use <account>, copying its token from <project>?", naming the oldest such project.
The script finds those projects. The rules checked here, on random folder trees too:
- a folder counts only when it is an apb project (it has docs/MAP.md) and its
  .claude/settings.local.json has a GH_TOKEN in its env block; the project being set up never counts;
- the list runs oldest first, by the project's first commit; the first line is the suggestion;
- the token never appears in anything the script prints.
The oracle for "oldest" is a plain pairwise check, not the script's sort.
"""
import importlib.util
import io
import json
import os
import random
import shutil
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("sources", os.path.join(HERE, "claude-account-sources.py"))
src = importlib.util.module_from_spec(spec)
spec.loader.exec_module(src)

TOKEN = "ghp_" + "Z9y8X7w6V5" * 3 + "u4T3s2"


class FakeGit:
    """Answers `git -C <folder> log --reverse --format=%ct` with the folder's first-commit time, or
    fails for a folder with no commits (time None)."""

    def __init__(self, times):
        self.times = {os.path.realpath(k): v for k, v in times.items()}
        self.calls = []

    def __call__(self, args):
        self.calls.append(list(args))
        assert args[0] == "git" and args[1] == "-C", args
        folder = os.path.realpath(args[2])
        t = self.times.get(folder)
        if t is None:
            return subprocess.CompletedProcess(args, 128, "", "fatal: no commits")
        return subprocess.CompletedProcess(args, 0, "%d\n%d\n" % (t, t + 100), "")


class Tree:
    def __init__(self, test):
        self.root = tempfile.mkdtemp()
        test.addCleanup(shutil.rmtree, self.root)
        self.times = {}

    def project(self, name, token=TOKEN, login="someone-claude", map_file=True, time=1000,
                settings=True, raw=None):
        d = os.path.join(self.root, name)
        os.makedirs(os.path.join(d, ".claude"), exist_ok=True)
        if map_file:
            os.makedirs(os.path.join(d, "docs"), exist_ok=True)
            open(os.path.join(d, "docs", "MAP.md"), "w").close()
        if settings:
            env = {}
            if token is not None:
                env["GH_TOKEN"] = token
            if login is not None:
                env["GIT_AUTHOR_NAME"] = login
            with open(os.path.join(d, ".claude", "settings.local.json"), "w") as f:
                f.write(raw if raw is not None else json.dumps({"env": env, "permissions": {}}))
        self.times[d] = time
        return d


def run_main(argv, git):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = src.main(argv, run=git)
    return code, out.getvalue(), err.getvalue()


class Finds(unittest.TestCase):
    def test_finds_the_oldest_first(self):
        t = Tree(self)
        new = t.project("tahoe", time=2000)
        old = t.project("sketch", time=1000)
        here = t.project("setup-test", time=3000)
        found = src.find([t.root], here, FakeGit(t.times))
        self.assertEqual([c.folder for c in found], [os.path.realpath(old), os.path.realpath(new)])
        self.assertEqual(found[0].login, "someone-claude")

    def test_skips_the_project_being_set_up(self):
        t = Tree(self)
        here = t.project("setup-test", time=1)
        self.assertEqual(src.find([t.root], here, FakeGit(t.times)), [])

    def test_skips_a_folder_without_docs_map(self):
        t = Tree(self)
        t.project("not-apb", map_file=False)
        self.assertEqual(src.find([t.root], os.path.join(t.root, "x"), FakeGit(t.times)), [])

    def test_skips_settings_without_a_token(self):
        t = Tree(self)
        t.project("empty", token=None)
        t.project("blank", token="")
        t.project("none", settings=False)
        t.project("broken", raw="{not json")
        t.project("list", raw="[1, 2]")
        t.project("env-list", raw='{"env": [1]}')
        self.assertEqual(src.find([t.root], os.path.join(t.root, "x"), FakeGit(t.times)), [])

    def test_unknown_login(self):
        t = Tree(self)
        t.project("p", login=None)
        found = src.find([t.root], os.path.join(t.root, "x"), FakeGit(t.times))
        self.assertEqual(found[0].login, None)
        code, out, _ = run_main(["--root", t.root, "--project", os.path.join(t.root, "x")], FakeGit(t.times))
        self.assertIn("unknown account", out)

    def test_a_folder_with_no_commits_comes_last(self):
        t = Tree(self)
        a = t.project("a", time=None)
        b = t.project("b", time=5000)
        found = src.find([t.root], os.path.join(t.root, "x"), FakeGit(t.times))
        self.assertEqual([c.folder for c in found], [os.path.realpath(b), os.path.realpath(a)])

    def test_same_folder_under_two_roots_counts_once(self):
        t = Tree(self)
        t.project("a")
        found = src.find([t.root, t.root + "/"], os.path.join(t.root, "x"), FakeGit(t.times))
        self.assertEqual(len(found), 1)

    def test_missing_root_is_skipped(self):
        self.assertEqual(src.find(["/no/such/folder"], "/tmp", FakeGit({})), [])


class Output(unittest.TestCase):
    def test_prints_one_line_each_oldest_first_and_never_the_token(self):
        t = Tree(self)
        t.project("new", time=2000, login="b-claude")
        t.project("old", time=1000, login="a-claude")
        code, out, err = run_main(["--root", t.root, "--project", os.path.join(t.root, "x")],
                                  FakeGit(t.times))
        self.assertEqual(code, 0)
        lines = out.strip().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0].split("\t")[:2], ["a-claude", os.path.realpath(os.path.join(t.root, "old"))])
        self.assertEqual(lines[1].split("\t")[0], "b-claude")
        self.assertNotIn(TOKEN, out + err)

    def test_says_so_when_there_are_none(self):
        t = Tree(self)
        code, out, err = run_main(["--root", t.root, "--project", os.path.join(t.root, "x")], FakeGit({}))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")
        self.assertIn("No earlier apb project", err)

    def test_default_root_is_the_projects_parent(self):
        t = Tree(self)
        t.project("old", time=1)
        here = t.project("here", time=9)
        cwd = os.getcwd()
        os.chdir(here)
        self.addCleanup(os.chdir, cwd)
        code, out, _ = run_main([], FakeGit(t.times))
        self.assertEqual(out.split("\t")[1], os.path.realpath(os.path.join(t.root, "old")))


class RandomTrees(unittest.TestCase):
    """Random folder trees: the set found is exactly the folders that qualify, and the first one has
    no qualifying folder older than it, by a pairwise check."""

    def test_random(self):
        rng = random.Random(114)
        for _ in range(150):
            t = Tree(self)
            qualifying = {}
            names = ["p%d" % i for i in range(rng.randint(0, 8))]
            here_name = rng.choice(names) if names and rng.random() < 0.5 else None
            for name in names:
                kind = rng.choice(["ok", "ok", "ok", "nomap", "notoken", "nosettings", "broken"])
                time = None if rng.random() < 0.15 else rng.randint(0, 50)  # ties on purpose
                tok = "ghp_" + "".join(rng.choice("abcXYZ019") for _ in range(36))
                d = t.project(name, token=None if kind == "notoken" else tok, map_file=kind != "nomap",
                              settings=kind != "nosettings", raw="{" if kind == "broken" else None,
                              time=time, login=rng.choice(["a-claude", "b-claude", None]))
                if kind == "ok" and name != here_name:
                    qualifying[os.path.realpath(d)] = (time, tok)
            here = os.path.join(t.root, here_name or "elsewhere")
            git = FakeGit(t.times)
            found = src.find([t.root], here, git)
            folders = [c.folder for c in found]
            self.assertEqual(set(folders), set(qualifying))
            self.assertEqual(len(folders), len(set(folders)))
            if found:
                first = found[0].folder
                t0 = qualifying[first][0]
                for other, (time, _) in qualifying.items():
                    if t0 is None:
                        self.assertIsNone(time, "a folder with commits came after one without")
                    elif time is not None:
                        self.assertLessEqual(t0, time, "a newer project came first")
            # Every adjacent pair is in order too.
            for a, b in zip(found, found[1:]):
                ta, tb = qualifying[a.folder][0], qualifying[b.folder][0]
                if ta is not None and tb is not None:
                    self.assertLessEqual(ta, tb)
                self.assertFalse(ta is None and tb is not None)
            code, out, err = run_main(["--root", t.root, "--project", here], git)
            for _, tok in qualifying.values():
                self.assertNotIn(tok, out + err)


class RealGit(unittest.TestCase):
    """With real git repos, the first commit's date decides, not the latest commit's."""

    def test_first_commit_decides(self):
        if shutil.which("git") is None:
            self.skipTest("git isn't installed")
        t = Tree(self)
        a = t.project("a")
        b = t.project("b")

        def commit(folder, date):
            env = dict(os.environ, GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date,
                       GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
                       GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")
            subprocess.run(["git", "-C", folder, "commit", "-q", "--allow-empty", "-m", date],
                           env=env, check=True, timeout=30)

        for d in (a, b):
            subprocess.run(["git", "init", "-q", d], check=True, timeout=30)
        commit(a, "2026-01-01T00:00:00Z")
        commit(a, "2026-12-01T00:00:00Z")  # a's latest commit is the newest of all
        commit(b, "2026-06-01T00:00:00Z")
        found = src.find([t.root], os.path.join(t.root, "x"))
        self.assertEqual([c.folder for c in found], [os.path.realpath(a), os.path.realpath(b)])


class Docs(unittest.TestCase):
    def test_readme_lists_the_script(self):
        with open(os.path.join(HERE, "..", "README.md"), encoding="utf-8") as f:
            self.assertIn("`scripts/claude-account-sources.py`", f.read())


if __name__ == "__main__":
    unittest.main()
