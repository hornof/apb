"""Pop-up check: in an apb project Claude asks the human in chat, not with pop-up buttons, except during setup.

Claude Code runs this from hooks/hooks.json as a PreToolUse hook, with the event's JSON on stdin. In an
apb project (one with docs/MAP.md) or in the apb repo itself (see hooks/apb_folder.py) it refuses:
- AskUserQuestion, the multiple-choice card;
- mcp__ccd_turn__wrap_up, the desktop app's end-of-turn box, when it has a question (`ask`) or buttons
  (`actions`). A recap-only wrap_up goes through.
The refusal tells Claude to ask the same question in its message, in the hand-off format: the message
ends with "1 thing for you to do right now: ...", so the human answers in their own words.

During /apb-setup the cards are wanted (the public/private question). Setup marks itself:

    python3 <apb plugin folder>/hooks/apb-popups.py setup-start   (step 0, after the folder check)
    python3 <apb plugin folder>/hooks/apb-popups.py setup-done    (step 7, before the hand-off)

run from the project's root. setup-start writes .apb/setup-running with the time and no session. The
first pop-up after that claims the marker for its session, and from then on only that session gets
cards. So a marker left behind by a setup that crashed lets no other session through: a claimed marker
belongs to the crashed session, and an unclaimed one is stale after 2 hours. Either way the hook fails
toward asking in chat, which is the playbook's rule anyway.

As a hook it never fails Claude: any error lets the call through.
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apb_folder import project_dir  # noqa: E402

ASK = "AskUserQuestion"
WRAP = "mcp__ccd_turn__wrap_up"
MARKER = os.path.join(".apb", "setup-running")
UNCLAIMED_MAX = 2 * 3600  # seconds an unclaimed marker stays good

HAND_OFF = ('Ask the same question in your message instead, in the hand-off format: end the message with '
            '"1 thing for you to do right now: <the question>", or "N things for you to do right now:" and a '
            'numbered list. The ask is the last thing in the message. The human answers in their own words. A decision still goes on GitHub, with its '
            'link in the message. Pop-ups are only for /apb-setup.')


def refusal(tool):
    if tool == WRAP:
        first = ("Refused: in an apb project the wrap-up box has no question or buttons (docs/playbook.md, "
                 "\"Routine asks look the same every time\"). Call it again with the recap only, no ask "
                 "and no actions. ")
    else:
        first = ("Refused: in an apb project Claude asks in chat, not with pop-up buttons (docs/playbook.md, "
                 "\"Routine asks look the same every time\"). ")
    return first + HAND_OFF


def popup(tool, tool_input):
    """True when this call would show the human a card with buttons."""
    if tool == ASK:
        return True
    if tool == WRAP and isinstance(tool_input, dict):
        ask = tool_input.get("ask")
        return (isinstance(ask, str) and bool(ask.strip())) or bool(tool_input.get("actions"))
    return False


def apb_project(p):
    """The apb project's folder, or the apb repo's, or None: CLAUDE_PROJECT_DIR, else the event's cwd.

    An apb project has docs/MAP.md. The apb plugin's own repo has none, but it follows the playbook too,
    so it counts; hooks/apb_folder.py says how it is known."""
    return project_dir(p)


def write_marker(project, data):
    folder = os.path.join(project, ".apb")
    os.makedirs(folder, exist_ok=True)
    ignore = os.path.join(folder, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w") as f:
            f.write("*\n")
    tmp = os.path.join(project, MARKER + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, os.path.join(project, MARKER))


def setup_running(project, session, now=None):
    """True when this session is running /apb-setup. Claims a fresh unclaimed marker for the session."""
    now = time.time() if now is None else now
    try:
        with open(os.path.join(project, MARKER), encoding="utf-8") as f:
            data = json.load(f)
        started = data["started"]
        owner = data.get("session")
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return False
    if not isinstance(started, (int, float)) or isinstance(started, bool):
        return False
    if owner is None:
        if now - started > UNCLAIMED_MAX or not isinstance(session, str) or not session:
            return False
        try:
            write_marker(project, {"started": started, "session": session})
        except OSError:
            pass
        return True
    return owner == session


def on_tool(p):
    """The hook's output for a PreToolUse event, or None to let the call through."""
    tool = p.get("tool_name")
    if not popup(tool, p.get("tool_input")):
        return None
    folder = os.environ.get("CLAUDE_PROJECT_DIR") or p.get("cwd")
    # Check the marker first, even before docs/MAP.md exists: step 1's card then claims it within minutes
    # of step 0, so the 2-hour limit for an unclaimed marker never cuts into a long setup.
    if isinstance(folder, str) and setup_running(folder, p.get("session_id")):
        return None
    if apb_project(p) is None:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": refusal(tool)}}


def command(args):
    project = os.getcwd()
    if args == ["setup-start"]:
        write_marker(project, {"started": time.time(), "session": None})
        print("Setup marked as running: pop-up questions allowed for this session until setup-done.")
        return 0
    if args == ["setup-done"]:
        try:
            os.remove(os.path.join(project, MARKER))
        except FileNotFoundError:
            pass
        print("Setup marked as done: questions go in chat from now on.")
        return 0
    print("Usage: apb-popups.py setup-start | setup-done   (run from the project's root)", file=sys.stderr)
    return 2


def main():
    if len(sys.argv) > 1:
        return command(sys.argv[1:])
    try:
        p = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if isinstance(p, dict) and p.get("hook_event_name") == "PreToolUse":
            out = on_tool(p)
            if out:
                sys.stdout.write(json.dumps(out))
    except BaseException:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
