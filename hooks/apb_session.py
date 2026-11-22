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
- A reopened (resumed) session gets no number (#146). Reopening an old session starts a new Claude Code
  process, so it used to look like the newest session: it took the watcher and the next number. Now the
  hooks ask with assign=False for a resumed session: it keeps the name it had, and an unnamed one stays
  unnamed. mark_resumed() records, in .apb/resumed.json, each session that Claude Code's SessionStart
  said was resumed (its `source` is "resume"), since later hook events don't carry the source;
  is_resumed() reads it. active_before() tells whether one session's first line in .apb/events.jsonl
  comes before another's: a session first active before the watcher's holder is a reopened one too.

hooks/apb-watch-start.py writes the name into .apb/watcher-owner.json, so `apb-watch.py --list` shows
which session holds the watcher; hooks/apb-selfcheck.py prints it at the top of its block.
A lock file makes the check and the write one step, so hooks fired at once never share a number.
"""
import fcntl
import json
import os
import time

SESSIONS = "sessions.json"
RESUMED = "resumed.json"  # the sessions SessionStart said were resumed (#146)
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


def apb_folder(folder):
    os.makedirs(folder, exist_ok=True)
    ignore = os.path.join(folder, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w") as f:
            f.write("*\n")


def session_name(folder, session, version, now=None, assign=True):
    """This session's name, given the first time it is asked for and kept. folder: the project's .apb
    folder. None when there is no session id. With assign=False (a reopened session, #146) only a name
    already given comes back: an unnamed session gets None, and nothing is written."""
    if not isinstance(session, str) or not session:
        return None
    if not assign:
        known = read_sessions(folder)["sessions"].get(session)
        return known["name"] if isinstance(known, dict) and isinstance(known.get("name"), str) else None
    apb_folder(folder)
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


# ---------------------------------------------------------------- reopened sessions (#146)

def read_resumed(folder):
    try:
        with open(os.path.join(folder, RESUMED), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def is_resumed(folder, session):
    """True if SessionStart said this session was resumed (mark_resumed)."""
    return isinstance(session, str) and bool(session) and session in read_resumed(folder)


def mark_resumed(folder, session):
    """Record that this session was resumed. Kept for the last KEEP sessions marked."""
    if not isinstance(session, str) or not session:
        return
    apb_folder(folder)
    with open(os.path.join(folder, RESUMED + ".lock"), "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        data = read_resumed(folder)
        data.pop(session, None)
        data[session] = time.time()
        data = dict(list(data.items())[-KEEP:])
        path = os.path.join(folder, RESUMED)
        tmp = "%s.%d" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)


def active_before(folder, mine, theirs):
    """True if session mine has a line in .apb/events.jsonl before session theirs's first line (or
    theirs has none): mine was active in this folder first, so it is an older session reopened. A
    session with no line is never before anyone. The file is appended in time order, so the first
    of the two to appear decides; the read stops there."""
    if not isinstance(mine, str) or not mine or mine == theirs:
        return False
    keys = {json.dumps(mine): mine}
    if isinstance(theirs, str) and theirs:
        keys[json.dumps(theirs)] = theirs
    try:
        with open(os.path.join(folder, "events.jsonl"), encoding="utf-8", errors="replace") as f:
            for line in f:
                if not any(k in line for k in keys):
                    continue
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                s = e.get("session") if isinstance(e, dict) else None
                if s in keys.values():
                    return s == mine
    except OSError:
        pass
    return False
