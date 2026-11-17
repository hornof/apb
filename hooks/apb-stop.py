"""Stop hook: when the orchestrator's work runs out, its message must say who holds the ball.

Claude Code runs this from hooks/hooks.json when a turn of the main session ends, with the event's
JSON on stdin. It acts only in an apb project (one with docs/MAP.md), and reads .apb/events.jsonl,
which hooks/apb-events.py writes.

- The work ran out in this turn when nothing is running, and either an agent stopped since the last
  stop or the turn was not started by a prompt (a background command finishing woke it). See the
  playbook's "Helping the human keep track". What's running comes from the Stop input's
  background_tasks; without it, from the events file (agents started and not stopped, and background
  commands started in this turn).
- Then the message must start with one of the three first lines. If it doesn't, the hook blocks the
  stop once, with the reason "Say who holds the ball", and Claude Code lets Claude add the line.
- It appends a Stop line to the events file, with who holds the ball when the message says so.
- If the human holds the ball, it starts a reminder in the background: if the human has done
  nothing after REMIND_AFTER seconds (no new prompt), one desktop alert. Only on a Mac. A prompt or a
  new first line ends the wait; a stop with neither (Claude woken by a watcher or an agent) doesn't.

Any error lets the stop through: the hook never stops Claude from finishing.
"""
import calendar
import json
import os
import re
import subprocess
import sys
import time

ABANDONED = 3 * 3600  # an agent silent this long is taken to be gone; same as scripts/apb-watch.py
REMIND_AFTER = 30 * 60  # seconds the human holds the ball, with no action, before one reminder
MAX = 300  # longest string kept in a line, in characters

REASON = ("Say who holds the ball. Your work just ran out, so start the message with one of these "
          "lines (playbook, \"Helping the human keep track\"):\n"
          "- Nothing for you to do right now. Waiting on <what>, about M minutes.\n"
          "- 1 thing for you to do right now: <the thing>.\n"
          "- N things for you to do right now: followed by a numbered list of exactly N things.")

NOTHING = re.compile(r"Nothing for you to do right now\. Waiting on .+, about \d+ minutes?\.?$")
ONE = re.compile(r"1 thing for you to do right now: (.+?)\.?$")
MANY = re.compile(r"(\d+) things for you to do right now:$")
ITEM = re.compile(r"(\d+)[.)]\s+\S")


def clean(line):
    """A line without surrounding space or Markdown emphasis."""
    return line.strip().strip("*_").strip()


def first_line(message):
    """(holder, todo) if the message starts with one of the three lines, else None.
    holder is "claude" or "human"; todo is what the human has to do, for the reminder."""
    if not isinstance(message, str):
        return None
    lines = [clean(l) for l in message.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    if not lines:
        return None
    head = lines[0]
    if NOTHING.match(head):
        return ("claude", None)
    m = ONE.match(head)
    if m:
        return ("human", m.group(1).strip())
    m = MANY.match(head)
    if not m or int(m.group(1)) < 2:
        return None
    n = int(m.group(1))
    numbers = []
    for line in lines[1:]:
        if not line and not numbers:
            continue
        item = ITEM.match(line)
        if not item:
            break
        numbers.append(int(item.group(1)))
    if numbers != list(range(1, n + 1)):
        return None
    return ("human", "%d things" % n)


def parse_time(text):
    return calendar.timegm(time.strptime(text[:19], "%Y-%m-%dT%H:%M:%S"))


def read_events(project, start=0):
    """The file's valid lines, from byte start on."""
    events = []
    try:
        with open(os.path.join(project, ".apb", "events.jsonl"), "rb") as f:
            f.seek(start)
            raw = f.read()
    except OSError:
        return events
    for line in raw.decode("utf-8", "replace").splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if isinstance(e, dict) and isinstance(e.get("event"), str):
            events.append(e)
    return events


def running_agents(events, now):
    """Agents in any session that started and haven't stopped, and aren't silent for ABANDONED."""
    last = {}
    for e in events:
        a = e.get("agent_id")
        if not isinstance(a, str):
            continue
        if e["event"] == "SubagentStop":
            last.pop(a, None)
            continue
        try:
            last[a] = parse_time(e["time"])
        except (KeyError, TypeError, ValueError):
            last[a] = now
    return [a for a, t in last.items() if now - t <= ABANDONED]


def this_turn(events, session):
    """This session's lines since its last stop that went through."""
    start = 0
    for i, e in enumerate(events):
        if e.get("session") == session and e["event"] == "Stop" and not e.get("blocked"):
            start = i + 1
    return [e for e in events[start:] if e.get("session") == session]


def work_ran_out(events, session, now, tasks=None):
    """tasks: the Stop input's background_tasks, the work Claude Code still has in flight. When
    Claude Code doesn't send it, the events file says what's running instead."""
    turn = this_turn(events, session)
    if isinstance(tasks, list):
        if tasks:
            return False
    elif running_agents(events, now) or any(
            e["event"] == "PostToolUse" and e.get("background") and "agent_id" not in e for e in turn):
        return False
    if any(e["event"] == "SubagentStop" for e in turn):
        return True
    prompts_logged = any(e["event"] == "UserPromptSubmit" and e.get("session") == session
                         for e in events)
    return prompts_logged and not any(e["event"] == "UserPromptSubmit" for e in turn)


def holder_now(events, session):
    """The last (holder, todo) this session's stops recorded, or (None, None)."""
    for e in reversed(events):
        if e.get("session") == session and e["event"] == "Stop" and e.get("holder"):
            return e["holder"], e.get("todo")
    return None, None


def last_message(p):
    message = p.get("last_assistant_message")
    if isinstance(message, str):
        return message
    path = p.get("transcript_path")
    if not isinstance(path, str):
        return None
    text = None
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    e = json.loads(line)
                    content = e["message"]["content"] if e.get("type") == "assistant" else None
                except (ValueError, KeyError, TypeError, AttributeError):
                    continue
                if isinstance(content, list):
                    parts = [c.get("text") for c in content
                             if isinstance(c, dict) and c.get("type") == "text" and isinstance(c.get("text"), str)]
                    if parts:
                        text = "\n".join(parts)
    except OSError:
        return None
    return text


def append(project, line):
    """Append line to the events file. Returns the file's size right after it."""
    folder = os.path.join(project, ".apb")
    os.makedirs(folder, exist_ok=True)
    ignore = os.path.join(folder, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w") as f:
            f.write("*\n")
    with open(os.path.join(folder, "events.jsonl"), "ab") as f:
        f.write((json.dumps(line, ensure_ascii=False) + "\n").encode("utf-8"))
        return f.tell()


def handle(p, project, now):
    """Decide on one stop and log it.
    Returns {"block": bool, "remind": what or None, "end": the events file's size after the line}."""
    session = p.get("session_id")
    if not isinstance(session, str):
        return {"block": False, "remind": None, "end": 0}
    events = read_events(project)
    said = first_line(last_message(p))
    block = (said is None and not p.get("stop_hook_active")
             and work_ran_out(events, session, now, p.get("background_tasks")))
    line = {"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + ".%03dZ" % (now % 1 * 1000),
            "event": "Stop", "session": session[:MAX]}
    if block:
        line["blocked"] = True
    elif said:
        line["holder"] = said[0]
        if said[1]:
            line["todo"] = said[1][:MAX]
    end = append(project, line)
    if block:
        return {"block": True, "remind": None, "end": end}
    if said:
        holder, todo = said
    elif any(e["event"] == "UserPromptSubmit" for e in this_turn(events, session)):
        holder, todo = holder_now(events, session)  # the prompt ended the last wait; start a new one
    else:
        holder, todo = None, None  # woken with no new line: the running wait carries on
    return {"block": False, "remind": todo if holder == "human" else None, "end": end}


def start_reminder(project, session, todo, start):
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "remind", project, session, todo, str(start)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)


def human_acted(project, session, start):
    """True if, after byte start, this session has a prompt or a stop with a new first line.
    A stop with no first line (Claude woken by a watcher or an agent) leaves the wait running."""
    return any(e.get("session") == session
               and (e["event"] == "UserPromptSubmit" or (e["event"] == "Stop" and e.get("holder")))
               for e in read_events(project, start))


def remind(project, session, todo, start):
    """Wait REMIND_AFTER seconds; if the human did nothing in that time, alert once."""
    wait = float(os.environ.get("APB_REMIND_AFTER_SECONDS", REMIND_AFTER))
    end = time.time() + wait
    while time.time() < end:
        time.sleep(min(5.0, max(0.0, end - time.time())))
        if human_acted(project, session, start):
            return
    try:
        subprocess.run(["osascript", "-e", "on run argv", "-e",
                        'display notification (item 1 of argv) with title "Claude Code" sound name "Glass"',
                        "-e", "end run", "Reminder, your turn: " + todo[:200]],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=10)
    except (OSError, subprocess.SubprocessError):
        pass


def main():
    if len(sys.argv) == 6 and sys.argv[1] == "remind":
        remind(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]))
        return
    p = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    if not isinstance(p, dict):
        return
    project = os.environ.get("CLAUDE_PROJECT_DIR") or p.get("cwd")
    if not isinstance(project, str) or not os.path.isfile(os.path.join(project, "docs", "MAP.md")):
        return
    result = handle(p, project, time.time())
    if result["block"]:
        sys.stdout.write(json.dumps({"decision": "block", "reason": REASON}))
        return
    if result["remind"]:
        start_reminder(project, p["session_id"], result["remind"], result["end"])


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
