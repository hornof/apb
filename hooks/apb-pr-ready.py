"""Marking a PR ready moves its card to the top of In review, the moment it happens (#156).

Since #131 a builder opens its PR as a draft when it starts, and the card stays In progress; it goes to
In review only when the builder marks the PR ready (`gh pr ready`). The orchestrator used to make that
move by hand, and moved cards at draft-open instead. Now this hook makes it, as one step with the
`gh pr ready` that needs it (playbook, "Where two actions must always happen together, one command
does both"):

- After a Bash call whose command runs `gh pr ready` and whose output has gh's own success line,
  `Pull request <owner>/<name>#<N> is marked as "ready for review"`, it runs scripts/board-move.py's
  pr-ready step on that PR: each card the PR closes goes to the top of In review.
- After `gh pr ready --undo` (`... is converted to "draft"`), it runs the pr-draft step: each card goes
  back to the top of In progress.
- Anything else, opening a draft (`gh pr create --draft`) included, moves nothing. gh's line names the
  repo and the PR, so the hook doesn't guess them from the folder.

board-move.py checks GitHub first: it refuses a draft (pr-ready), a PR ready for review (pr-draft), a PR
that isn't open, and one that closes no card. The hook says what it did, or why not, in a quiet note to
the session that ran gh (PostToolUse additionalContext). A PR marked ready on GitHub's site is the
orchestrator's to move by hand, and the end-of-turn check (scripts/consistency-check.py) catches a card
left in the wrong column either way.

Claude Code runs it from hooks/hooks.json after each Bash call, in builders' sessions and agents too,
in an apb project or the apb repo (hooks/apb_folder.py). Its gh calls share BUDGET seconds, inside
hooks.json's timeout. Any error exits 0: the hook never gets in Claude's way.
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apb_folder import project_dir  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
BUDGET = 20  # seconds for all of the move's gh calls; hooks.json gives the hook 30
TIME_LIMIT = 8  # seconds for one gh call
COMMAND = re.compile(r"(?:^|[;&|(\s])gh\s+pr\s+ready(?![\w-])")
DONE = re.compile(r'Pull request ([\w.-]+/[\w.-]+)#(\d+) is (marked as "ready for review"|converted to "draft")')
MOVE = "python3 <apb plugin folder>/scripts/board-move.py"


def load_board_move():
    spec = importlib.util.spec_from_file_location("board_move", os.path.join(os.path.dirname(HERE), "scripts",
                                                                             "board-move.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def output(response):
    """The text a Bash call printed, from its tool_response (a dict of stdout and stderr, or a string)."""
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        return "\n".join(v for k, v in response.items() if k in ("stdout", "stderr", "output") and isinstance(v, str))
    return ""


def moves(p):
    """[(step, repo, PR number)] for payload p: "pr-ready" or "pr-draft" for each PR gh says it changed."""
    if not isinstance(p, dict) or p.get("hook_event_name") != "PostToolUse" or p.get("tool_name") != "Bash":
        return []
    command = (p.get("tool_input") or {}).get("command") if isinstance(p.get("tool_input"), dict) else None
    if not isinstance(command, str) or not COMMAND.search(command):
        return []
    found = []
    for repo, number, what in DONE.findall(output(p.get("tool_response"))):
        step = ("pr-ready", repo, int(number)) if what.startswith("marked") else ("pr-draft", repo, int(number))
        if step not in found:
            found.append(step)
    return found


def move(step, repo, number, gh="gh", budget=BUDGET):
    """Make the move and return the note for Claude."""
    bm = load_board_move()
    ask = bm.asker(gh, budget, TIME_LIMIT)
    try:
        # The hook words its own note: board-move.py's pr-ready text tells the orchestrator to run
        # apb-review, and this note usually lands in the builder's session (review fix 1 on #160).
        draft = step == "pr-draft"
        column = "In progress" if draft else "In review"
        cards = bm.move_pr_cards(ask, repo, number, draft, column)
        return ("apb's hook (#156) moved %s to the top of %s: PR #%d is %s."
                % (", ".join("#%d" % c for c in cards), column, number,
                   "a draft again" if draft else "marked ready for review"))
    except bm.Refused as e:
        return "apb's hook (#156) didn't move a card for PR #%d: %s." % (number, e)
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
        why = (getattr(e, "stderr", None) or str(e) or type(e).__name__).strip()[:200]
        return ("apb's hook (#156) couldn't move the card for PR #%d (%s). The orchestrator moves it: %s %s %d --repo %s"
                % (number, why, MOVE, step, number, repo))


def main(argv):
    a = argparse.ArgumentParser(description="Move a card when its PR is marked ready (or a draft again).")
    a.add_argument("--gh", default="gh", help="the gh command (tests use a fake)")
    a.add_argument("--budget", type=float, default=BUDGET, help="seconds for all the gh calls")
    opts = a.parse_args(argv)
    p = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    found = moves(p)
    if not found or not project_dir(p):
        return 0
    share = opts.budget / len(found)  # one command can mark several PRs; together they fit the budget
    notes = [move(step, repo, number, opts.gh, share) for step, repo, number in found]
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                        "additionalContext": "\n".join(notes)}},
                                ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except BaseException:
        pass
    sys.exit(0)
