"""The orchestrator session's name (issue #133): `apb orch N · <plugin version> · <start time PT>`,
for example `apb orch 4 · 0.3.59 · Nov 21 7:20 PM`.

Orchestrator sessions were all titled from their first message, so they were hard to tell apart. Each
one now gets a name:
- N goes up by one per orchestrator, counted from the project's .apb folder. .apb/sessions.json keeps
  each named session's number and name. Sessions from before names existed count too: each session
  that started a watcher (a watcher-start line in .apb/events.jsonl, which only an orchestrator's hook
  writes) is one orchestrator. A new session takes one more than the highest of the two counts, so the
  highest N is the newest session, the one that holds the watcher (#123).
- The version is the session's apb plugin version; the time is when the session was first named (at
  session start), in US Pacific time.
- A session keeps its name: asking again, after a resume, gives the same one.

hooks/apb-watch-start.py writes the name into .apb/watcher-owner.json, so `apb-watch.py --list` shows
which session holds the watcher; hooks/apb-selfcheck.py prints it at the top of its block.
A lock file makes the check and the write one step, so hooks fired at once never share a number.
"""
import fcntl
import json
import os
import time

SESSIONS = "sessions.json"
KEEP = 200  # sessions kept in the file; the numbers already given stay counted
PT = "America/Los_Angeles"
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def pt_clock(t):
    """'Nov 21 7:20 PM': epoch seconds t in US Pacific time."""
    import datetime
    from zoneinfo import ZoneInfo
    local = datetime.datetime.fromtimestamp(t, ZoneInfo(PT))
    return "%s %d %d:%02d %s" % (MONTHS[local.month - 1], local.day, local.hour % 12 or 12, local.minute,
                                 "AM" if local.hour < 12 else "PM")


def format_name(n, version, t):
    return "apb orch %d · %s · %s" % (n, version or "?", pt_clock(t))


def earlier_orchestrators(folder, session):
    """The sessions other than this one that started a watcher, from .apb/events.jsonl."""
    seen = set()
    try:
        with open(os.path.join(folder, "events.jsonl"), encoding="utf-8") as f:
            for line in f:
                if '"watcher-start"' not in line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                s = e.get("session") if isinstance(e, dict) else None
                if e.get("event") == "watcher-start" and isinstance(s, str) and s and s != session:
                    seen.add(s)
    except (OSError, UnicodeDecodeError):
        pass
    return len(seen)


def read_sessions(folder):
    try:
        with open(os.path.join(folder, SESSIONS), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return {"top": 0, "sessions": {}}
    if not isinstance(data, dict) or not isinstance(data.get("sessions"), dict):
        return {"top": 0, "sessions": {}}
    top = data.get("top")
    return {"top": top if isinstance(top, int) else 0, "sessions": data["sessions"]}


def session_name(folder, session, version, now=None):
    """This session's name, given the first time it is asked for and kept. folder: the project's .apb
    folder. None when there is no session id."""
    if not isinstance(session, str) or not session:
        return None
    os.makedirs(folder, exist_ok=True)
    ignore = os.path.join(folder, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w") as f:
            f.write("*\n")
    with open(os.path.join(folder, SESSIONS + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_sessions(folder)
        known = data["sessions"].get(session)
        if isinstance(known, dict) and isinstance(known.get("name"), str):
            return known["name"]
        numbers = [v.get("n") for v in data["sessions"].values() if isinstance(v, dict)]
        top = max([data["top"]] + [n for n in numbers if isinstance(n, int)])
        n = max(top, earlier_orchestrators(folder, session)) + 1
        name = format_name(n, version, time.time() if now is None else now)
        data["sessions"][session] = {"n": n, "name": name}
        data["sessions"] = dict(list(data["sessions"].items())[-KEEP:])
        data["top"] = n
        path = os.path.join(folder, SESSIONS)
        tmp = "%s.%d" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)
        return name
