"""Tests for version-bump.py (#124): PRs never edit the plugin version; main raises it by one per merge.

Run: python3 -m unittest discover -s scripts -t scripts

Every test works in throwaway git repos: a bare repo plays GitHub, clones play the builder's branch,
the Action's checkout and the human's own checkout. Both directions are covered:
- a PR that edits the version is caught, and one that doesn't is let through;
- a merge raises main's version by exactly one, a second run adds nothing, and two merges seconds
  apart (or a lost Action run) still end with one step per merge.
The randomised test checks the invariants against its own count of merges, not against the script.
"""
import importlib.util
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
SCRIPT = os.path.join(HERE, "version-bump.py")
spec = importlib.util.spec_from_file_location("version_bump", SCRIPT)
vb = importlib.util.module_from_spec(spec)
spec.loader.exec_module(vb)

MANIFEST = ".claude-plugin/plugin.json"
SEED = "0.3.54"
SEED_PATCH = 54
BODY = """{
  "name": "apb",
  "description": "Agentic Programming Blueprint",
  "version": "%s",
  "author": { "name": "Luke Hornof", "url": "https://hornof.org" },
  "repository": "https://github.com/hornof/apb"
}
"""
ENV = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1",
           GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@example.com",
           GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@example.com")


def git(cwd, *args):
    r = subprocess.run(["git", *args], cwd=cwd, env=ENV, capture_output=True, text=True, timeout=30)
    if r.returncode:
        raise AssertionError("git %s failed: %s" % (" ".join(args), r.stderr))
    return r.stdout.strip()


def write(cwd, path, text):
    full = os.path.join(cwd, path)
    os.makedirs(os.path.dirname(full) or cwd, exist_ok=True)
    with open(full, "w", encoding="utf-8") as f:
        f.write(text)


def version_in(cwd, rev="HEAD"):
    return json.loads(git(cwd, "show", "%s:%s" % (rev, MANIFEST)))["version"]


def patch(version):
    return int(version.split(".")[2])


class Repo:
    """A bare 'GitHub' repo plus a 'web' clone that merges PRs the way GitHub's merge button does."""

    def __init__(self, tmp):
        self.tmp = tmp
        self.origin = os.path.join(tmp, "origin.git")
        git(tmp, "init", "-q", "--bare", "-b", "main", self.origin)
        self.web = self.clone("web")
        write(self.web, MANIFEST, BODY % SEED)
        write(self.web, "README.md", "apb\n")
        git(self.web, "add", "-A")
        git(self.web, "commit", "-q", "-m", "Plugin %s" % SEED)
        git(self.web, "push", "-q", "origin", "HEAD:main")
        self.prs = 0

    def clone(self, name):
        path = os.path.join(self.tmp, name)
        git(self.tmp, "clone", "-q", self.origin, path)
        return path

    def branch(self, name, edit_version=None, edit_description=False, base="origin/main"):
        """A PR branch with one commit: a new file, and optionally a changed manifest."""
        git(self.web, "fetch", "-q", "origin")
        git(self.web, "checkout", "-q", "-B", name, base)
        write(self.web, "docs/%s.md" % name, name + "\n")
        if edit_version or edit_description:
            text = open(os.path.join(self.web, MANIFEST), encoding="utf-8").read()
            if edit_version:
                text = re.sub(r'"version": "[^"]*"', '"version": "%s"' % edit_version, text)
            if edit_description:
                text = text.replace("Agentic Programming Blueprint", "Agentic Programming Blueprint, now with more")
            write(self.web, MANIFEST, text)
        git(self.web, "add", "-A")
        git(self.web, "commit", "-q", "-m", "Work on %s" % name)
        return git(self.web, "rev-parse", "HEAD")

    def merge(self, name, **kw):
        """Open a PR from a fresh branch and merge it into main with a merge commit. Returns its number."""
        self.prs += 1
        n = 200 + self.prs
        self.branch(name, **kw)
        git(self.web, "checkout", "-q", "--detach", "origin/main")
        git(self.web, "merge", "-q", "--no-ff", "-m", "Merge pull request #%d from hornof/%s" % (n, name), name)
        git(self.web, "push", "-q", "origin", "HEAD:main")
        return n

    def main_version(self):
        git(self.web, "fetch", "-q", "origin")
        return version_in(self.web, "origin/main")

    def first_parent(self):
        """(sha, subject, version) for every commit on main's first-parent line, oldest first."""
        git(self.web, "fetch", "-q", "origin")
        out = git(self.web, "log", "--first-parent", "--reverse", "--format=%H %s", "origin/main")
        rows = []
        for line in out.splitlines():
            sha, subject = line.split(" ", 1)
            rows.append((sha, subject, version_in(self.web, sha)))
        return rows


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Repo(self._tmp.name)
        vb.BEFORE_PUSH = None

    def tearDown(self):
        vb.BEFORE_PUSH = None
        self._tmp.cleanup()

    def run_cli(self, cwd, *args):
        return subprocess.run([sys.executable, SCRIPT, *args], cwd=cwd, env=ENV, capture_output=True,
                              text=True, timeout=60)


class CheckCatchesAVersionEditInAPR(Base):
    """Direction 1: a PR that edits the version is caught; one that doesn't is let through."""

    def check(self, **kw):
        head = self.repo.branch("feature", **kw)
        return vb.check("origin/main", head, cwd=self.repo.web)

    def test_a_pr_that_raises_the_version_is_caught(self):
        ok, message = self.check(edit_version="0.3.55")
        self.assertFalse(ok)
        self.assertIn("0.3.54", message)
        self.assertIn("0.3.55", message)

    def test_a_pr_that_lowers_or_reformats_the_version_is_caught(self):
        for v in ("0.3.53", "0.4.0", "1.0.0", "0.3.54-rc1"):
            with self.subTest(v=v):
                ok, _ = self.check(edit_version=v)
                self.assertFalse(ok)

    def test_a_pr_that_does_not_touch_the_manifest_passes(self):
        ok, _ = self.check()
        self.assertTrue(ok)

    def test_a_pr_that_edits_other_manifest_fields_passes(self):
        ok, _ = self.check(edit_description=True)
        self.assertTrue(ok)

    def test_an_old_branch_passes_after_main_moved_on(self):
        """Branched before two merges raised main's version: the PR itself changed nothing."""
        old = self.repo.branch("old-branch")
        self.repo.merge("a")
        self.repo.merge("b")
        vb.bump(cwd=self.repo.clone("action"), remote="origin", branch="main")
        git(self.repo.web, "fetch", "-q", "origin")
        self.assertEqual(self.repo.main_version(), "0.3.56")
        ok, message = vb.check("origin/main", old, cwd=self.repo.web)
        self.assertTrue(ok, message)

    def test_the_cli_exits_1_on_a_version_edit_and_0_otherwise(self):
        bad = self.repo.branch("bad", edit_version="0.3.99")
        r = self.run_cli(self.repo.web, "check", "origin/main", bad)
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("version", (r.stdout + r.stderr).lower())
        good = self.repo.branch("good")
        r = self.run_cli(self.repo.web, "check", "origin/main", good)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_the_check_works_on_githubs_merge_ref(self):
        """The Action checks out refs/pull/N/merge and compares its two parents."""
        bad = self.repo.branch("bad", edit_version="0.3.99")
        git(self.repo.web, "checkout", "-q", "--detach", "origin/main")
        git(self.repo.web, "merge", "-q", "--no-ff", "-m", "merge ref", bad)
        r = self.run_cli(self.repo.web, "check", "HEAD^1", "HEAD^2")
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)


class AMergeRaisesTheVersionByOne(Base):
    """Direction 2: each merge into main raises the version by exactly one."""

    def action(self):
        return vb.bump(cwd=self.repo.clone("action-%d" % random.randrange(10 ** 9)), remote="origin",
                       branch="main")

    def test_one_merge_raises_it_by_one(self):
        n = self.repo.merge("feature")
        self.assertEqual(self.action(), ["0.3.55"])
        self.assertEqual(self.repo.main_version(), "0.3.55")
        rows = self.repo.first_parent()
        self.assertIn("#%d" % n, rows[-1][1])
        self.assertIn("0.3.55", rows[-1][1])

    def test_the_bump_changes_only_the_version_line(self):
        self.repo.merge("feature")
        self.action()
        git(self.repo.web, "fetch", "-q", "origin")
        diff = git(self.repo.web, "diff", "--numstat", "origin/main^", "origin/main")
        self.assertEqual(diff.split(), ["1", "1", MANIFEST])
        after = git(self.repo.web, "show", "origin/main:" + MANIFEST)
        self.assertEqual(after, (BODY % "0.3.55").strip())

    def test_a_second_run_adds_nothing(self):
        self.repo.merge("feature")
        self.action()
        self.assertEqual(self.action(), [])
        self.assertEqual(self.repo.main_version(), "0.3.55")

    def test_no_merge_no_bump(self):
        self.assertEqual(self.action(), [])
        self.assertEqual(self.repo.main_version(), SEED)

    def test_two_merges_before_a_run_give_two_steps_in_merge_order(self):
        a = self.repo.merge("a")
        b = self.repo.merge("b")
        self.assertEqual(self.action(), ["0.3.55", "0.3.56"])
        subjects = [s for _, s, _ in self.repo.first_parent()[-2:]]
        self.assertIn("#%d" % a, subjects[0])
        self.assertIn("#%d" % b, subjects[1])

    def test_a_merged_pr_that_bumped_by_hand_is_not_bumped_again(self):
        """Merges from before this change set the version themselves; they are the starting point."""
        self.repo.merge("legacy", edit_version="0.3.55")
        self.assertEqual(self.action(), [])
        self.repo.merge("new")
        self.assertEqual(self.action(), ["0.3.56"])

    def test_a_squash_merge_counts_too(self):
        git(self.repo.web, "fetch", "-q", "origin")
        git(self.repo.web, "checkout", "-q", "--detach", "origin/main")
        write(self.repo.web, "squashed.md", "x\n")
        git(self.repo.web, "add", "-A")
        git(self.repo.web, "commit", "-q", "-m", "Squashed change (#301)")
        git(self.repo.web, "push", "-q", "origin", "HEAD:main")
        self.assertEqual(self.action(), ["0.3.55"])
        self.assertIn("#301", self.repo.first_parent()[-1][1])

    def test_the_humans_checkout_is_left_alone(self):
        """--push works in a temporary worktree: the caller's branch, files and worktree list don't change."""
        mine = self.repo.clone("mine")
        git(mine, "checkout", "-q", "-b", "my-branch")
        write(mine, "wip.txt", "uncommitted\n")
        head = git(mine, "rev-parse", "HEAD")
        worktrees = git(mine, "worktree", "list")
        self.repo.merge("feature")
        self.assertEqual(vb.bump(cwd=mine, remote="origin", branch="main"), ["0.3.55"])
        self.assertEqual(git(mine, "rev-parse", "HEAD"), head)
        self.assertEqual(git(mine, "rev-parse", "--abbrev-ref", "HEAD"), "my-branch")
        self.assertEqual(open(os.path.join(mine, "wip.txt")).read(), "uncommitted\n")
        self.assertEqual(git(mine, "worktree", "list"), worktrees)
        self.assertEqual(self.repo.main_version(), "0.3.55")

    def test_the_cli_bumps_and_pushes(self):
        self.repo.merge("feature")
        r = self.run_cli(self.repo.clone("cli"), "bump", "--push", "origin", "main")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0.3.55", r.stdout)
        self.assertEqual(self.repo.main_version(), "0.3.55")

    def test_a_refused_push_stops_at_once_with_gits_reason(self):
        """A branch rule or a read-only token isn't a race: no retries, and the reason is kept."""
        self.repo.merge("feature")
        hook = os.path.join(self.repo.origin, "hooks", "pre-receive")
        write(self.repo.origin, "hooks/pre-receive", "#!/bin/sh\necho 'protected branch: no bots' >&2\nexit 1\n")
        os.chmod(hook, 0o755)
        attempts = []
        vb.BEFORE_PUSH = attempts.append
        with self.assertRaises(vb.BumpError) as caught:
            vb.bump(cwd=self.repo.clone("run"), remote="origin", branch="main")
        self.assertEqual(attempts, [1])
        self.assertIn("protected branch: no bots", str(caught.exception))
        self.assertNotIn("kept moving", str(caught.exception))
        self.assertEqual(self.repo.main_version(), SEED)

    def test_bump_without_push_refuses_an_edited_manifest(self):
        mine = self.repo.clone("mine")
        git(mine, "fetch", "-q", "origin")
        self.repo.merge("feature")
        git(mine, "pull", "-q", "origin", "main")
        head = git(mine, "rev-parse", "HEAD")
        text = open(os.path.join(mine, MANIFEST), encoding="utf-8").read()
        write(mine, MANIFEST, text.replace("Agentic", "Edited"))
        with self.assertRaises(vb.BumpError):
            vb.bump(cwd=mine)
        self.assertEqual(git(mine, "rev-parse", "HEAD"), head)
        git(mine, "checkout", "--", MANIFEST)
        self.assertEqual(vb.bump(cwd=mine), ["0.3.55"])

    def test_a_bad_version_stops_with_an_error_and_pushes_nothing(self):
        git(self.repo.web, "fetch", "-q", "origin")
        git(self.repo.web, "checkout", "-q", "--detach", "origin/main")
        write(self.repo.web, MANIFEST, BODY % "0.3")
        git(self.repo.web, "commit", "-q", "-am", "Odd version")
        git(self.repo.web, "push", "-q", "origin", "HEAD:main")
        self.repo.merge("feature")
        before = git(self.repo.web, "rev-parse", "origin/main")
        r = self.run_cli(self.repo.clone("cli"), "bump", "--push", "origin", "main")
        self.assertEqual(r.returncode, 2, r.stdout + r.stderr)
        git(self.repo.web, "fetch", "-q", "origin")
        self.assertEqual(git(self.repo.web, "rev-parse", "origin/main"), before)


class MergesSecondsApart(Base):
    """#115 and #116 merged 15 s apart on 2026-11-21. Each merge starts its own Action run."""

    def test_a_merge_landing_while_a_run_pushes_still_gives_one_step_each(self):
        a = self.repo.merge("a")
        landed = []

        def other_merge_lands(attempt):
            if not landed:
                landed.append(self.repo.merge("b"))

        vb.BEFORE_PUSH = other_merge_lands
        self.assertEqual(vb.bump(cwd=self.repo.clone("run-a"), remote="origin", branch="main"),
                         ["0.3.55", "0.3.56"])
        vb.BEFORE_PUSH = None
        # The run that the second merge started finds nothing left to do.
        self.assertEqual(vb.bump(cwd=self.repo.clone("run-b"), remote="origin", branch="main"), [])
        rows = self.repo.first_parent()
        bumps = [(s, v) for _, s, v in rows if s.startswith("Plugin 0.3.5") and v != SEED]
        self.assertEqual([v for _, v in bumps], ["0.3.55", "0.3.56"])
        self.assertIn("#%d" % a, bumps[0][0])
        self.assertIn("#%d" % landed[0], bumps[1][0])

    def test_two_runs_at_the_same_time_end_two_up(self):
        self.repo.merge("a")
        self.repo.merge("b")
        procs = [subprocess.Popen([sys.executable, SCRIPT, "bump", "--push", "origin", "main"],
                                  cwd=self.repo.clone("p%d" % i), env=ENV, stdout=subprocess.PIPE,
                                  stderr=subprocess.PIPE, text=True) for i in range(2)]
        for p in procs:
            out, err = p.communicate(timeout=60)
            self.assertEqual(p.returncode, 0, out + err)
        self.assertEqual(self.repo.main_version(), "0.3.56")

    def test_a_lost_run_is_caught_up_by_the_next(self):
        """GitHub keeps one pending run per concurrency group and drops older pending ones."""
        self.repo.merge("a")
        self.repo.merge("b")  # its run was dropped
        self.repo.merge("c")
        self.assertEqual(vb.bump(cwd=self.repo.clone("run-c"), remote="origin", branch="main"),
                         ["0.3.55", "0.3.56", "0.3.57"])

    def test_it_gives_up_after_the_retry_limit(self):
        self.repo.merge("a")

        def always_race(attempt):
            self.repo.merge("race-%d" % attempt)

        vb.BEFORE_PUSH = always_race
        with self.assertRaises(vb.BumpError):
            vb.bump(cwd=self.repo.clone("run"), remote="origin", branch="main", retries=3)


class RandomHistories(Base):
    """Random merges, lost runs, hand bumps and races. Invariants, checked against the test's own count:
    - after a final run, main's patch number = seed + merges that didn't set the version themselves;
    - every bump commit raises the version by exactly one over its first parent;
    - no version appears twice as the result of a change on main's first-parent line."""

    def test_random_histories_keep_the_invariants(self):
        for seed in range(6):
            with self.subTest(seed=seed):
                rng = random.Random(seed)
                self._tmp.cleanup()
                self._tmp = tempfile.TemporaryDirectory()
                self.repo = Repo(self._tmp.name)
                counted = 0
                hand = 0
                runner = 0
                for step in range(rng.randint(3, 7)):
                    kind = rng.random()
                    if kind < 0.15:
                        # A PR that sets the version by hand, to one above main (the old habit),
                        # merged after main caught up. (Merged while a bump is still pending, it
                        # would hide the merges before it: the PR check makes that a red X first.)
                        runner += 1
                        vb.bump(cwd=self.repo.clone("r%d" % runner), remote="origin", branch="main")
                        target = "0.3.%d" % (patch(self.repo.main_version()) + 1)
                        self.repo.merge("hand-%d" % step, edit_version=target)
                        hand += 1
                    else:
                        self.repo.merge("pr-%d" % step, edit_description=rng.random() < 0.2)
                        counted += 1
                    r = rng.random()
                    if r < 0.6:
                        runner += 1
                        race = rng.random() < 0.3
                        landed = []
                        if race:
                            def other(attempt, landed=landed, step=step):
                                if not landed:
                                    landed.append(self.repo.merge("race-%d" % step))
                            vb.BEFORE_PUSH = other
                        vb.bump(cwd=self.repo.clone("r%d" % runner), remote="origin", branch="main")
                        vb.BEFORE_PUSH = None
                        counted += len(landed)
                    # else: this merge's run was lost; a later run must catch up.
                runner += 1
                vb.bump(cwd=self.repo.clone("r%d" % runner), remote="origin", branch="main")
                rows = self.repo.first_parent()
                self.assertEqual(patch(rows[-1][2]), SEED_PATCH + counted + hand)
                changes = []
                for (_, _, before), (_, subject, after) in zip(rows, rows[1:]):
                    if before != after:
                        changes.append(after)
                        if subject.startswith("Plugin "):
                            self.assertEqual(patch(after), patch(before) + 1, subject)
                self.assertEqual(len(changes), len(set(changes)))
                self.assertEqual(len(changes), counted + hand)


class WorkflowAndDocs(unittest.TestCase):
    """The Action runs the script, and no instruction left in the plugin tells a builder to bump."""

    WORKFLOW = os.path.join(ROOT, ".github", "workflows", "plugin-version.yml")

    def text(self, path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def test_the_workflow_bumps_on_push_to_main_and_checks_prs(self):
        w = self.text(self.WORKFLOW)
        self.assertRegex(w, r"push:\s*\n\s*branches:\s*\[main\]")
        self.assertRegex(w, r"pull_request:")
        self.assertIn("contents: write", w)
        self.assertIn("version-bump.py bump --push origin main", w)
        self.assertRegex(w, r"cancel-in-progress:\s*false")
        self.assertIn("fetch-depth: 0", w)

    def test_the_pr_check_runs_mains_copy_of_the_script(self):
        """A PR can't pass the check by editing the script it is checked with."""
        w = self.text(self.WORKFLOW)
        self.assertIn("git show HEAD^1:scripts/version-bump.py", w)
        self.assertIn("check HEAD^1 HEAD^2", w)

    def test_the_playbook_says_prs_leave_the_version_alone(self):
        p = self.text(os.path.join(ROOT, "docs", "playbook.md"))
        self.assertIn("never edits the plugin version", p)
        self.assertIn("scripts/version-bump.py bump --push origin main", p)

    def test_no_instruction_tells_a_builder_to_bump_the_version(self):
        bump = re.compile(r"bump[^.\n]*(version|plugin\.json)|plugin\.json[^.\n]* to \d+\.\d+\.\d+"
                          r"|version[^.\n]* to \d+\.\d+\.\d+", re.I)
        offenders = []
        for top in ("skills", "agents", "docs", "templates", "hooks", "README.md"):
            path = os.path.join(ROOT, top)
            files = [path] if os.path.isfile(path) else [
                os.path.join(d, f) for d, _, fs in os.walk(path) for f in fs
                if f.endswith((".md", ".py", ".json", ".yml"))]
            for f in files:
                for i, line in enumerate(self.text(f).splitlines(), 1):
                    if bump.search(line) and "never" not in line.lower():
                        offenders.append("%s:%d: %s" % (os.path.relpath(f, ROOT), i, line.strip()[:120]))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
