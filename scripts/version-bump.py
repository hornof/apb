#!/usr/bin/env python3
"""The apb plugin's version goes up on merge, never in a PR (#124).

  version-bump.py check BASE HEAD
      Exit 1 if HEAD changes the version in .claude-plugin/plugin.json compared with its merge-base
      with BASE, 0 if not. The PR check runs `check HEAD^1 HEAD^2` on GitHub's merge ref.

  version-bump.py bump [--push REMOTE BRANCH]
      Raise the patch number by one for every change on the first-parent line that has not raised it
      yet, one commit each, oldest first. "Not raised yet" means every commit after the last one that
      changed the version. A second run finds nothing to do, and a run that missed a merge (a dropped
      Action run) catches it up.
      Without --push it commits on the current checkout's HEAD. With --push it fetches BRANCH, works in
      a temporary worktree so the caller's checkout is never touched, and pushes. If the push is
      refused because another merge landed meanwhile, it fetches again, starts over from the new tip
      and recounts, up to --retries times.

Exit codes: 0 done, 1 the check found a version edit, 2 an error (bad version, git failure).
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

MANIFEST = ".claude-plugin/plugin.json"
VERSION_FIELD = re.compile(r'("version"\s*:\s*")([^"]*)(")')
SEMVER = re.compile(r"(\d+)\.(\d+)\.(\d+)")
GIT_TIMEOUT = 60

# Test seam: called with the attempt number just before each push.
BEFORE_PUSH = None


class BumpError(Exception):
    pass


def git(cwd, *args, check=True):
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=GIT_TIMEOUT)
    if check and r.returncode:
        raise BumpError("git %s: %s" % (" ".join(args), r.stderr.strip()))
    return r


def version_at(cwd, rev):
    r = git(cwd, "show", "%s:%s" % (rev, MANIFEST), check=False)
    if r.returncode:
        raise BumpError("no %s at %s" % (MANIFEST, rev))
    try:
        return json.loads(r.stdout)["version"]
    except (ValueError, KeyError, TypeError):
        raise BumpError("%s at %s has no readable version" % (MANIFEST, rev))


def check(base, head, cwd="."):
    """(ok, message): ok is False when head changes the version compared with its merge-base."""
    mb = git(cwd, "merge-base", base, head).stdout.strip()
    before, after = version_at(cwd, mb), version_at(cwd, head)
    if before == after:
        return True, "The PR leaves the plugin version at %s." % before
    return False, ("The PR changes the plugin version in %s from %s to %s. PRs leave the version alone: "
                   "it goes up by one on main when the PR merges. Put the version line back to %s."
                   % (MANIFEST, before, after, before))


def pending(cwd, rev="HEAD"):
    """The first-parent commits after the last one that changed the version, oldest first."""
    lines = git(cwd, "rev-list", "--first-parent", "--parents", rev).stdout.split("\n")
    found = []
    for line in lines:
        shas = line.split()
        if len(shas) < 2:  # the root commit, or the end
            break
        sha, parent = shas[0], shas[1]
        if version_at(cwd, sha) != version_at(cwd, parent):
            break
        found.append(sha)
    return list(reversed(found))


def next_version(version):
    m = SEMVER.fullmatch(version)
    if not m:
        raise BumpError("the plugin version %r is not of the form X.Y.Z" % version)
    return "%s.%s.%d" % (m.group(1), m.group(2), int(m.group(3)) + 1)


def label(cwd, sha):
    """The PR a first-parent commit came from, as #N, or its short sha."""
    subject = git(cwd, "log", "-1", "--format=%s", sha).stdout.strip()
    m = re.match(r"Merge pull request #(\d+)", subject) or re.search(r"\(#(\d+)\)\s*$", subject)
    return "#" + m.group(1) if m else git(cwd, "rev-parse", "--short", sha).stdout.strip()


def commit_bumps(cwd):
    """Add one bump commit per pending change on HEAD of the checkout at cwd. Returns the new versions."""
    todo = pending(cwd)
    if not todo:
        return []
    labels = [label(cwd, sha) for sha in todo]
    path = os.path.join(cwd, MANIFEST)
    versions = []
    for what in labels:
        with open(path, encoding="utf-8") as f:
            text = f.read()
        fields = VERSION_FIELD.findall(text)
        if len(fields) != 1:
            raise BumpError("%s should hold one version field, found %d" % (MANIFEST, len(fields)))
        new = next_version(fields[0][1])
        with open(path, "w", encoding="utf-8") as f:
            f.write(VERSION_FIELD.sub(lambda m: m.group(1) + new + m.group(3), text, count=1))
        git(cwd, "commit", "-q", "-m", "Plugin %s (%s)" % (new, what), "-m",
            "Raised on merge by scripts/version-bump.py (#124): one step per change on main.", "--", MANIFEST)
        versions.append(new)
    return versions


def bump(cwd=".", remote=None, branch="main", retries=5):
    """Bump for every pending change. With remote, work on remote/branch in a temporary worktree and
    push, retrying when another push lands first. Returns the versions added (empty: nothing to do)."""
    if not remote:
        if git(cwd, "status", "--porcelain", "--", MANIFEST).stdout.strip():
            raise BumpError("%s has uncommitted changes" % MANIFEST)
        return commit_bumps(cwd)
    tracking = "refs/remotes/%s/%s" % (remote, branch)
    tmp = tempfile.mkdtemp(prefix="apb-version-bump-")
    work = os.path.join(tmp, "main")
    git(cwd, "fetch", "-q", remote, "+refs/heads/%s:%s" % (branch, tracking))
    git(cwd, "worktree", "add", "-q", "--detach", work, tracking)
    try:
        for attempt in range(1, retries + 1):
            versions = commit_bumps(work)
            if not versions:
                return []
            if BEFORE_PUSH:
                BEFORE_PUSH(attempt)
            if git(work, "push", "-q", remote, "HEAD:refs/heads/%s" % branch, check=False).returncode == 0:
                return versions
            git(work, "fetch", "-q", remote, "+refs/heads/%s:%s" % (branch, tracking))
            git(work, "reset", "-q", "--hard", tracking)
        raise BumpError("%s/%s kept moving: gave up after %d tries; run it again" % (remote, branch, retries))
    finally:
        git(cwd, "worktree", "remove", "--force", work, check=False)
        git(cwd, "worktree", "prune", check=False)
        shutil.rmtree(tmp, ignore_errors=True)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="fail if HEAD changes the plugin version")
    c.add_argument("base")
    c.add_argument("head")
    b = sub.add_parser("bump", help="raise the version once per change since the last raise")
    b.add_argument("--push", nargs=2, metavar=("REMOTE", "BRANCH"))
    b.add_argument("--retries", type=int, default=5)
    args = p.parse_args(argv)
    try:
        if args.cmd == "check":
            ok, message = check(args.base, args.head)
            print(message)
            return 0 if ok else 1
        remote, branch = args.push if args.push else (None, None)
        versions = bump(remote=remote, branch=branch or "main", retries=args.retries)
        print("Plugin version: " + (", ".join(versions) if versions else "nothing to raise"))
        return 0
    except BumpError as e:
        print("version-bump: %s" % e, file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
