"""Update the apb plugin from the orchestrator's session, check it, and print the hand-off (#162).

After a merge that raises the plugin version on main, the orchestrator used to hand the human two
commands to paste before opening a new session. It runs on the human's Mac with a working shell, so it
runs them itself:

  python3 <apb plugin folder>/scripts/plugin-update.py --next N

1. `claude plugin marketplace update apb`, then `claude plugin update apb@apb` (not run if the first
   failed). Both work from inside a running session; the update takes effect only in a new one.
2. `claude plugin list --json`: the version of apb@apb now installed.
3. main's version, read from GitHub as hooks/apb-version.py reads it.

It prints the hand-off: a line on what happened, then the one ask on the last line.
- Success (exit 0): both commands worked and the installed version equals main's. The line names the
  version installed; the ask is "open a new session and send `Start #N.`" (N is --next, the card the new
  session builds; without it, "open a new session.").
- Fallback (exit 1): a command failed, hung or wasn't found, or the installed or main version can't be
  read, or they differ. The line says what went wrong and the versions it saw; the ask hands the human
  both commands, then the new session. The human is never left on an old version without knowing it.

Every child process has a time limit: --timeout for each claude command (150 s), GH_TIMEOUT for the
GitHub read (30 s). The worst case, three claude commands and the read, is 480 s, under the 600 s
(600000 ms) Bash timeout the orchestrator runs the script with; the Bash tool's default of 2 minutes
would stop a slow run before it printed any hand-off. The startup self-check's Version line
stays the backstop for an update that was missed.
"""
import argparse
import importlib.util
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # the installed plugin
PLUGIN = "apb@apb"
MARKETPLACE = ["plugin", "marketplace", "update", "apb"]
UPDATE = ["plugin", "update", PLUGIN]
LIST = ["plugin", "list", "--json"]
TIMEOUT = 150  # seconds for each claude command
GH_TIMEOUT = 30  # seconds for the GitHub read of main's version
BASH_TIMEOUT_MS = 600000  # the Bash timeout the orchestrator runs this script with (the tool's maximum)
ASK = "1 thing for you to do right now: "


def load_version_hook():
    path = os.path.join(ROOT, "hooks", "apb-version.py")
    spec = importlib.util.spec_from_file_location("apb_version_for_update", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


version = load_version_hook()


def parse_args(argv):
    a = argparse.ArgumentParser(description="Update the apb plugin and print the hand-off.")
    a.add_argument("--next", help="the card the new session builds, for the ask's `Start #N.`")
    a.add_argument("--claude", default="claude", help="the claude command (tests use a fake)")
    a.add_argument("--gh", default="gh", help="the gh command (tests use a fake)")
    a.add_argument("--plugin-root", default=ROOT, help="the installed plugin's folder (this session's)")
    a.add_argument("--timeout", type=float, default=TIMEOUT, help="seconds for each command")
    return a.parse_args(argv)


def shown(args):
    return "`claude %s`" % " ".join(args)


def run(claude, args, timeout):
    """(stdout, None) if the command worked, else (None, why) with the first line of why."""
    try:
        out = subprocess.run([claude, *args], stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, "%s ran out of time after %g seconds" % (shown(args), timeout)
    except OSError as e:
        return None, "%s couldn't start (%s)" % (shown(args), e.strerror or type(e).__name__)
    if out.returncode == 0:
        return out.stdout, None
    first = [l.strip() for l in (out.stderr or out.stdout or "").splitlines() if l.strip()]
    return None, "%s failed (exit %d%s)" % (shown(args), out.returncode, ": " + first[0] if first else "")


def installed_version(listing):
    """apb@apb's version from `claude plugin list --json`, or None."""
    try:
        plugins = json.loads(listing)
    except (TypeError, ValueError):
        return None
    for p in plugins if isinstance(plugins, list) else []:
        if isinstance(p, dict) and p.get("id") == PLUGIN and version.parse(p.get("version")):
            return p["version"]
    return None


def start(next_card):
    card = (next_card or "").strip().lstrip("#")
    return "open a new session and send `Start #%s.`" % card if card else "open a new session."


def update(opts, seen):
    """(ok, what happened) for one update run. seen gets the versions read: running and main."""
    session = version.read_manifest(opts.plugin_root)
    seen["running"] = session.get("version") or "unknown"
    seen["main"] = main = version.main_version(version.repo_of_manifest(session), opts.gh, min(opts.timeout, GH_TIMEOUT))
    for args in (MARKETPLACE, UPDATE):
        _, why = run(opts.claude, args, opts.timeout)
        if why:
            return False, why
    listing, why = run(opts.claude, LIST, opts.timeout)
    installed = installed_version(listing) if not why else None
    if installed is None:
        return False, "the installed apb version couldn't be read (%s)" % (
            why or "no %s in %s" % (PLUGIN, shown(LIST)))
    if main is None:
        return False, "installed apb plugin %s, but main's version couldn't be read from GitHub to check it" % installed
    if installed != main:
        return False, "the installed apb plugin is %s, not main's %s" % (installed, main)
    return True, ("Updated the apb plugin to %s, main's version (this session still runs %s; the update "
                  "takes effect in a new session)." % (installed, seen["running"]))


def handoff(ok, said, next_card, running="unknown", main=None):
    """The hand-off: what happened, a blank line, then the one ask."""
    if ok:
        return "%s\n\n%s%s" % (said, ASK, start(next_card))
    return ("The apb plugin update didn't finish: %s. This session runs %s; main is at %s.\n\n"
            "%srun %s and %s, then %s" % (said, running, main or "a version GitHub didn't give",
                                         ASK, shown(MARKETPLACE), shown(UPDATE), start(next_card)))


def main(argv):
    opts = parse_args(argv)
    seen = {}
    try:
        ok, said = update(opts, seen)
    except Exception as e:  # never leave the human without the fallback
        ok, said = False, "the update script hit %s" % type(e).__name__
    sys.stdout.write(handoff(ok, said, opts.next, seen.get("running", "unknown"), seen.get("main")) + "\n")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
