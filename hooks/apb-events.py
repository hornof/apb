"""Append one JSON line per Claude Code event to .apb/events.jsonl in an apb project.

Claude Code runs this from hooks/hooks.json with the event's JSON on stdin. It writes only in an apb
project (one with docs/MAP.md). It never blocks Claude: it prints nothing, catches every error and
always exits 0.
"""
import json
import os
import re
import sys
import time

MAX = 300  # longest string kept in a line, in characters

# A prompt Claude Code sends itself, such as a background agent reporting back, starts with a tag.
AUTOMATIC = re.compile(r"\s*<[a-z][\w-]*>")
# A routine reply from the human: "go", "resolved", "merged", or one or more decision letters
# ("b", "(b)", "9a", "Decision 9: b", "1a, 2b"). scripts/card-times.py counts the other messages.
_CHOICE = r"(?:(?:decision\s*)?\d+\s*[:.)-]?\s*)?\(?[a-z]\)?"
ROUTINE = re.compile(r"(?:go|resolved|merged|%s(?:(?:\s*(?:,|&|\band\b)\s*|\s+)%s)*)" % (_CHOICE, _CHOICE))


def routine(text):
    """True if text is a routine reply: "go", a decision letter, "resolved" or "merged"."""
    if not isinstance(text, str):
        return False
    flat = " ".join(re.sub(r"[*_`]", "", text).lower().split()).rstrip(".!")
    return bool(ROUTINE.fullmatch(flat))


def cut(value):
    return value[:MAX] if isinstance(value, str) else value


def record(p):
    """The line to write for payload p, or None if the event isn't logged."""
    event = p.get("hook_event_name")
    if not isinstance(event, str):
        return None
    now = time.time()
    line = {"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + ".%03dZ" % (now % 1 * 1000),
            "event": event, "session": cut(p.get("session_id"))}
    for key in ["agent_id", "agent_type"]:
        if key in p:
            line[key] = cut(p[key])
    if event in ("PostToolUse", "PostToolUseFailure"):
        if p.get("tool_name") != "Bash":
            return None
        tool_input = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else {}
        line["command"] = cut(tool_input.get("command"))
        line["exit"] = None
        if event == "PostToolUse":
            if tool_input.get("run_in_background"):
                line["background"] = True
            else:
                line["exit"] = 0
        else:
            match = re.match(r"Exit code (\d+)", p.get("error") if isinstance(p.get("error"), str) else "")
            if match:
                line["exit"] = int(match.group(1))
            if p.get("is_interrupt"):
                line["interrupted"] = True
        if isinstance(p.get("duration_ms"), (int, float)):
            line["duration_ms"] = p["duration_ms"]
    elif event in ("TaskCreated", "TaskCompleted"):
        line["task_id"] = cut(p.get("task_id"))
        line["task_subject"] = cut(p.get("task_subject"))
    elif event == "Notification":
        line["notification_type"] = cut(p.get("notification_type"))
    elif event == "UserPromptSubmit" and isinstance(p.get("prompt"), str):
        # Never the text: only whether Claude Code sent it, or whether it is a routine reply.
        if AUTOMATIC.match(p["prompt"]):
            line["automatic"] = True
        else:
            line["routine"] = routine(p["prompt"])
    return line


def main():
    p = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    if not isinstance(p, dict):
        return
    project = os.environ.get("CLAUDE_PROJECT_DIR") or p.get("cwd")
    if not isinstance(project, str) or not os.path.isfile(os.path.join(project, "docs", "MAP.md")):
        return
    line = record(p)
    if line is None:
        return
    folder = os.path.join(project, ".apb")
    os.makedirs(folder, exist_ok=True)  # several hooks can run at once; none may fail here
    ignore = os.path.join(folder, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w") as f:
            f.write("*\n")
    with open(os.path.join(folder, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
