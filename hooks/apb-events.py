"""Append one JSON line per Claude Code event to .apb/events.jsonl in an apb project.

Claude Code runs this from hooks/hooks.json with the event's JSON on stdin. It writes only in an apb
project (one with docs/MAP.md) or in the apb repo itself (see hooks/apb_folder.py). It creates .apb/
and the events file on its first write. It never blocks Claude: it prints nothing, catches every error and
always exits 0.

A Bash call is logged as its command and exit status. An agent launched with the Agent tool is logged
as an AgentLaunch line: the agent's id, its type, and the cards its description names (else the first
line of its brief), so scripts/consistency-check.py knows which card an agent's isolated worktree,
on the branch worktree-agent-<id>, is building (#159). No other tool call is logged.
"""
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apb_folder import project_dir  # noqa: E402

MAX = 300  # longest string kept in a line, in characters

# A prompt Claude Code sends itself starts with a tag, such as <task-notification>, or, when a
# builder or reviewer hands back its report, with a header line and then an <agent-message> tag.
AUTOMATIC = re.compile(r"\s*(?:<[a-z][\w-]*>|Another Claude session sent a message:\s*<agent-message[\s>])")
# A routine reply from the human: "go", "resolved", "merged", or one or more decision letters
# ("b", "(b)", "9a", "Decision 9: b", "1a, 2b"). scripts/card-times.py counts the other messages.
_CHOICE = r"(?:(?:decision\s*)?\d+\s*[:.)-]?\s*)?\(?[a-z]\)?"
ROUTINE = re.compile(r"(?:go|resolved|merged|%s(?:(?:\s*(?:,|&|\band\b)\s*|\s+)%s)*)" % (_CHOICE, _CHOICE))
# A card an agent's description or brief names: #159, apb#159, hornof/apb#159.
CARD = re.compile(r"(?<![&\d])#(\d+)\b")
AGENT_ID = re.compile(r"\bagentId:\s*([0-9a-z]+)")
MAX_CARDS = 5


def routine(text):
    """True if text is a routine reply: "go", a decision letter, "resolved" or "merged"."""
    if not isinstance(text, str):
        return False
    flat = " ".join(re.sub(r"[*_`]", "", text).lower().split()).rstrip(".!")
    return bool(ROUTINE.fullmatch(flat))


def cut(value):
    return value[:MAX] if isinstance(value, str) else value


def launched_id(response):
    """The launched agent's id from the Agent tool's response: its agentId field, or the "agentId: <id>"
    line of its text. None if it has neither."""
    if isinstance(response, dict) and isinstance(response.get("agentId"), str):
        return response["agentId"]
    if isinstance(response, dict):
        response = response.get("content")
    if isinstance(response, list):
        response = " ".join(c.get("text") for c in response if isinstance(c, dict) and isinstance(c.get("text"), str))
    m = AGENT_ID.search(response) if isinstance(response, str) else None
    return m.group(1) if m else None


def cards(tool_input):
    """The cards an agent was launched on: the numbers its description names, else those on the first
    line of its brief. At most MAX_CARDS, once each, in order."""
    found = []
    for text in (tool_input.get("description"), (tool_input.get("prompt") or "").split("\n", 1)[0]):
        if isinstance(text, str):
            for n in CARD.findall(text):
                if int(n) not in found:
                    found.append(int(n))
        if found:
            break
    return found[:MAX_CARDS]


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
    if event == "PostToolUse" and p.get("tool_name") in ("Agent", "Task"):
        tool_input = p.get("tool_input") if isinstance(p.get("tool_input"), dict) else {}
        agent = launched_id(p.get("tool_response"))
        if not agent:
            return None
        line.update({"event": "AgentLaunch", "launched": cut(agent), "cards": cards(tool_input)})
        if isinstance(tool_input.get("subagent_type"), str):
            line["launched_type"] = cut(tool_input["subagent_type"])
        return line
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
    project = project_dir(p)
    if project is None:
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
