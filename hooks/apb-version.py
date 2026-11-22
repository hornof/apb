"""Say when the apb plugin or the apb checkout is older than main (issue #128).

A Code session runs the plugin version installed when it started; a plugin update takes effect only in
a new session. Nothing told anyone when main moved past it, so a session could run all day on old
hooks. This hook says so.

- At session start (SessionStart in hooks/hooks.json, not async, so the note is in Claude's context
  before its first message), in an apb project or the apb repo (hooks/apb_folder.py):
  - the plugin: compare the installed version (this plugin's own .claude-plugin/plugin.json) with
    main's, read from GitHub with `gh api` in the repo the installed manifest names. When main is
    newer, the note gives both versions, the two update commands and "then open a new session".
  - the checkout, in the apb repo only: when it is on another branch than main (or a detached HEAD),
    the note names the branch and gives `git switch main` and `git pull`; when main is behind
    origin/main, the note says by how many commits and gives `git pull`. The hook runs
    `git fetch origin main` to know, which moves only origin/main; it never switches or pulls, since
    the checkout may hold uncommitted work. A linked worktree is left alone: Claude Code makes those
    on their own branches on purpose, and `git switch main` fails in one.
  The hook prints the note as SessionStart additionalContext, with the instruction to say it in the
  first message. On the current version and an up-to-date main it prints nothing.
- At a watcher wake (hooks/apb-watch-start.py calls wake_note): when the report has a merge and main's
  plugin is newer, the wake carries the same plugin note; without a merge it carries the note only
  for a main version this session hasn't been told yet (the version bump lands just after a merge).
  .apb/version-said.json keeps, per session, the last main version said.

Every git and gh call has a time limit (--timeout). Anything that can't be read says nothing, and any
error exits 0: the hook never gets in Claude's way.
"""
import argparse
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apb_folder import is_apb_repo, project_dir  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # the installed plugin
MANIFEST = os.path.join(".claude-plugin", "plugin.json")
DEFAULT_REPO = "hornof/apb"
TIMEOUT = 10  # seconds for each gh or git call
SAID = "version-said.json"
KEEP = 50  # sessions kept in the said file
UPDATE = ("`claude plugin marketplace update apb`", "`claude plugin update apb@apb`")
MERGE = re.compile(r"\bPR #\d+ merged\b")


def parse_args(argv):
    a = argparse.ArgumentParser(description="Say when the apb plugin or checkout is older than main.")
    a.add_argument("--gh", default="gh", help="the gh command (tests use a fake)")
    a.add_argument("--plugin-root", default=ROOT, help="the installed plugin's folder")
    a.add_argument("--timeout", type=float, default=TIMEOUT, help="seconds for each gh or git call")
    return a.parse_args(argv)


# ---------------------------------------------------------------- versions

def parse(v):
    """The dotted numbers of a version string, as a tuple of ints; None if it isn't one."""
    if not isinstance(v, str) or not re.fullmatch(r"\d+(\.\d+)*", v.strip()):
        return None
    return tuple(int(x) for x in v.strip().split("."))


def newer(a, b):
    """True if version a is newer than version b. Unreadable versions are never newer."""
    pa, pb = parse(a), parse(b)
    if pa is None or pb is None:
        return False
    n = max(len(pa), len(pb))
    return pa + (0,) * (n - len(pa)) > pb + (0,) * (n - len(pb))


def read_manifest(root):
    try:
        with open(os.path.join(root, MANIFEST), encoding="utf-8") as f:
            m = json.load(f)
    except (OSError, ValueError):
        return {}
    return m if isinstance(m, dict) else {}


def repo_of_manifest(m):
    """'owner/name' from the manifest's repository field; the apb repo if it names none."""
    url = m.get("repository") if isinstance(m, dict) else None
    if isinstance(url, dict):
        url = url.get("url")
    found = re.search(r"github\.com[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?\s*$", url or "")
    return "%s/%s" % found.groups() if found else DEFAULT_REPO


def main_version(repo, gh, timeout):
    """main's plugin version on GitHub, or None if it can't be read."""
    try:
        out = subprocess.run([gh, "api", "-H", "Accept: application/vnd.github.raw+json",
                              "repos/%s/contents/.claude-plugin/plugin.json?ref=main" % repo],
                             stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
        if out.returncode != 0:
            return None
        m = json.loads(out.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    v = m.get("version") if isinstance(m, dict) else None
    return v if parse(v) else None


def plugin_check(root, gh, timeout):
    """(installed, main, note): note is the plugin line when main is newer, else None."""
    m = read_manifest(root)
    installed = m.get("version")
    main = main_version(repo_of_manifest(m), gh, timeout)
    if not newer(main, installed):
        return installed, main, None
    note = ("This session runs apb plugin %s; main is at %s. To update, run %s and %s, then open a new "
            "session." % (installed, main, UPDATE[0], UPDATE[1]))
    return installed, main, note


# ---------------------------------------------------------------- the apb checkout

def git(folder, timeout, *args):
    """git's output, stripped, or None if it failed."""
    try:
        out = subprocess.run(["git", "-C", folder, *args], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=timeout, env=dict(os.environ, GIT_TERMINAL_PROMPT="0"))
    except (OSError, subprocess.SubprocessError):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def checkout_note(folder, timeout):
    """The checkout line for the apb repo when it is off main or main is behind origin/main, else None."""
    if not is_apb_repo(folder):
        return None
    git_dir, common = git(folder, timeout, "rev-parse", "--absolute-git-dir"), \
        git(folder, timeout, "rev-parse", "--path-format=absolute", "--git-common-dir")
    if git_dir is None or common is None:
        return None
    if os.path.realpath(git_dir) != os.path.realpath(common):
        return None  # a linked worktree
    if git(folder, timeout, "remote", "get-url", "origin") is None:
        return None
    branch = git(folder, timeout, "symbolic-ref", "--short", "-q", "HEAD")
    if branch != "main":
        where = "on branch `%s`" % branch if branch else "on a detached HEAD"
        return ("This apb checkout is %s, not `main`. To switch: `git switch main`, then `git pull` "
                "(commit or set aside any work first)." % where)
    git(folder, timeout, "fetch", "--quiet", "origin", "main")  # moves origin/main only
    behind = git(folder, timeout, "rev-list", "--count", "main..refs/remotes/origin/main")
    if not behind or not behind.isdigit() or int(behind) == 0:
        return None
    n = int(behind)
    return ("This apb checkout's `main` is %d commit%s behind `origin/main`. To update: `git pull`."
            % (n, "" if n == 1 else "s"))


# ---------------------------------------------------------------- what was said

def said_path(project):
    return os.path.join(project, ".apb", SAID)


def read_said(project):
    try:
        with open(said_path(project), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def record_said(project, session, main):
    """Remember that this session was told main is at version main."""
    if not isinstance(session, str) or not session:
        return
    try:
        folder = os.path.join(project, ".apb")
        os.makedirs(folder, exist_ok=True)
        ignore = os.path.join(folder, ".gitignore")
        if not os.path.exists(ignore):
            with open(ignore, "w") as f:
                f.write("*\n")
        data = read_said(project)
        data.pop(session, None)
        data[session] = main
        data = dict(list(data.items())[-KEEP:])
        tmp = "%s.%d" % (said_path(project), os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f)
        os.replace(tmp, said_path(project))
    except OSError:
        pass


def wake_note(project, session, report, gh="gh", root=ROOT, timeout=TIMEOUT):
    """The plugin line to add to a watcher's wake, or "": when main's plugin is newer and the report has
    a merge, or main is at a version this session hasn't been told."""
    try:
        _, main, note = plugin_check(root, gh, timeout)
        if not note:
            return ""
        if not MERGE.search(report or "") and read_said(project).get(session) == main:
            return ""
        record_said(project, session, main)
        return "apb version check: say this in one line in your next message to the human: " + note
    except Exception:
        return ""


# ---------------------------------------------------------------- the hook

def main(argv):
    opts = parse_args(argv)
    p = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    if not isinstance(p, dict) or p.get("agent_id"):
        return 0
    project = project_dir(p)
    if not project:
        return 0
    _, main, plugin = plugin_check(opts.plugin_root, opts.gh, opts.timeout)
    checkout = checkout_note(project, opts.timeout)
    notes = [n for n in (plugin, checkout) if n]
    if not notes:
        return 0
    if plugin:
        record_said(project, p.get("session_id"), main)
    text = ("apb version check (the plugin's SessionStart hook). Say each line below, one line each, at the "
            "top of your first message to the human. Don't run the commands yourself: the human updates "
            "the plugin, and the checkout may hold uncommitted work.\n" + "\n".join("- " + n for n in notes))
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                        "additionalContext": text}}) + "\n")
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except BaseException:
        code = 0
    sys.exit(code)
