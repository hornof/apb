"""Finds earlier apb projects that already hold the token of Claude's GitHub account.

Usage, from the new project's root:
    python3 <apb plugin folder>/scripts/claude-account-sources.py [--root <folder> ...] [--project <folder>]

apb-setup step 5 runs it before asking about Claude's GitHub account. It looks at each folder directly
inside every root (by default, the folder that holds the project) and lists the ones that are apb
projects (they have docs/MAP.md) whose .claude/settings.local.json has a GH_TOKEN in its env block.
The project being set up never counts.

It prints one line per project, oldest first by the project's first commit (folders without commits
come last):
    <account login>\t<project folder>\t<first commit date>
The login comes from GIT_AUTHOR_NAME in the same env block, which claude-account.py set to the
account's login; "unknown account" when it is missing. The first line is the project setup proposes
to copy from: the oldest is the least likely to be changed or removed. If no project qualifies, it
prints nothing to stdout and says so on stderr.

It never prints the token, never asks GitHub anything, and never writes a file.
"""
import argparse
import collections
import datetime
import json
import os
import subprocess
import sys

TIME_LIMIT = 30  # seconds for each git call
SETTINGS = os.path.join(".claude", "settings.local.json")
MAP = os.path.join("docs", "MAP.md")

Source = collections.namedtuple("Source", "folder login first_commit")


def run(args):
    return subprocess.run(args, capture_output=True, text=True, timeout=TIME_LIMIT)


def env_block(folder):
    """The env block of the folder's settings.local.json; {} when it is missing or unreadable."""
    try:
        with open(os.path.join(folder, SETTINGS), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    env = data.get("env") if isinstance(data, dict) else None
    return env if isinstance(env, dict) else {}


def first_commit(folder, run):
    """The time of the folder's first commit, in seconds; None when it has none or git fails."""
    try:
        done = run(["git", "-C", folder, "log", "--reverse", "--format=%ct"])
    except (OSError, subprocess.TimeoutExpired):
        return None
    if done.returncode != 0:
        return None
    lines = done.stdout.split()
    try:
        return int(lines[0]) if lines else None
    except ValueError:
        return None


def find(roots, project, run=run):
    """The earlier projects that hold a token, oldest first."""
    here = os.path.realpath(project)
    seen = set()
    found = []
    for root in roots:
        try:
            names = sorted(os.listdir(root))
        except OSError:
            continue
        for name in names:
            folder = os.path.realpath(os.path.join(root, name))
            if folder in seen or folder == here or not os.path.isdir(folder):
                continue
            seen.add(folder)
            if not os.path.isfile(os.path.join(folder, MAP)):
                continue
            env = env_block(folder)
            if not env.get("GH_TOKEN"):
                continue
            login = env.get("GIT_AUTHOR_NAME") or None
            found.append(Source(folder, login, first_commit(folder, run)))
    found.sort(key=lambda s: (s.first_commit is None, s.first_commit or 0, s.folder))
    return found


def main(argv, run=run):
    parser = argparse.ArgumentParser(description="List earlier apb projects that hold Claude's GitHub token.")
    parser.add_argument("--project", default=".", help="the project being set up (default: this folder)")
    parser.add_argument("--root", action="append",
                        help="a folder whose subfolders are projects (default: the project's parent); "
                             "give it more than once for several")
    args = parser.parse_args(argv)
    roots = args.root or [os.path.dirname(os.path.realpath(args.project))]
    found = find(roots, args.project, run)
    if not found:
        print("No earlier apb project in %s holds a Claude account token." % ", ".join(roots), file=sys.stderr)
        return 0
    for s in found:
        date = ("no commits" if s.first_commit is None
                else datetime.datetime.fromtimestamp(s.first_commit).strftime("%Y-%m-%d"))
        print("%s\t%s\t%s" % (s.login or "unknown account", s.folder, date))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
