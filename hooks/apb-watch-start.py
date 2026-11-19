"""Keep the watchers (scripts/apb-watch.py) running for the orchestrator, and wake it when a watcher
finds something.

Claude Code runs this from hooks/hooks.json at the start of a session, on each prompt, before each
tool call and at each stop, with the event's JSON on stdin. It is an asyncRewake hook: it runs in the
background, and when it exits with code 2, what it printed on stderr wakes the model.

- It acts only in an apb project (one with docs/MAP.md), and only for the main session: a tool call
  from a builder or reviewer (its payload has an agent_id) starts nothing.
- The coordination issue comes from --coordination, or from a "coordination:" line in the project's
  CLAUDE.md: "coordination: #25" (this repo) or "coordination: owner/name#25" (another repo).
- One or two watchers:
  - the project watcher, on the project's own repo: its PRs, stuck commands and quiet agents. When
    the coordination issue is in this repo, it also watches that issue.
  - the coordination watcher, only when the coordination issue is in another repo: that repo's PRs
    and the issue. It leaves stuck commands and quiet agents to the project watcher, so they wake
    the orchestrator once.
  With no coordination line, the project watcher runs without an issue.
- No second copy of either. Each watcher has its own pid file in .apb/ (watcher.pid,
  watcher-coordination.pid), written under a lock, so hooks fired at once start each watcher once.
  A hook takes every pid file that is free and runs those watchers side by side; a hook that finds
  both taken exits at once. A pid file whose process is gone, or is some other program, doesn't
  count. Nor does one whose Claude Code has exited: that hook is stopped and replaced.
- Each start of a watcher writes a watcher-start line to .apb/events.jsonl, and each exit a
  watcher-exit line with the reason: found, no change, error or timeout. Both name the watcher.
- When either watcher reports something, this hook stops its other watcher, frees its pid files,
  prints the report on stderr and exits 2, which wakes the model. The next tool call starts both again.
- When a watcher reports no change, it is started again at once, until --budget runs out, so the
  watchers also run while the orchestrator waits with no tool calls.
- When a watcher fails, it is not started again for --backoff seconds, and the failure wakes no one.
  The other watcher goes on.
- Every watcher run has a time limit: its --limit, plus --grace seconds. Stopping this hook (SIGTERM,
  as at Claude Code's hook timeout) stops its watchers too.

Any other error exits 0: the hook never gets in Claude's way.
"""
import argparse
import fcntl
import importlib.util
import json
import os
import queue
import re
import signal
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(os.path.dirname(HERE), "scripts", "apb-watch.py")
BUDGET = 23 * 3600  # seconds this hook keeps the watchers going; less than its timeout in hooks.json
LIMIT = 60  # minutes, the watcher's own limit for one run
GRACE = 120  # seconds a watcher run may go past its limit before it is stopped
BACKOFF = 60  # seconds after a failed run before a hook tries that watcher again
NAME = "apb-watch-start.py"
MAX = 300  # longest string kept in an events line, in characters
NO_EVENTS = "no-events-file"  # given to the coordination watcher, so it checks no agents
COORDINATION = re.compile(r"^[\s>*+-]*\**coordination:?\**:?\s*(?:([\w.-]+/[\w.-]+))?#(\d+)",
                          re.IGNORECASE | re.MULTILINE)


def parse_args(argv):
    a = argparse.ArgumentParser(description="Keep apb's watchers running for the orchestrator.")
    a.add_argument("--coordination", help="owner/name#N or #N: the coordination issue")
    a.add_argument("--watch-script", default=WATCH, help="the watcher to run (tests use a fake)")
    a.add_argument("--budget", type=float, default=BUDGET, help="seconds to keep the watchers going")
    a.add_argument("--limit", type=float, default=LIMIT, help="minutes, the watcher's limit for one run")
    a.add_argument("--grace", type=float, default=GRACE, help="seconds a run may go past its limit")
    a.add_argument("--backoff", type=float, default=BACKOFF, help="seconds to wait after a failed run")
    return a.parse_args(argv)


def coordination(text):
    """(repo or None, issue number or None) from 'owner/name#N' or '#N', or from a CLAUDE.md text."""
    m = COORDINATION.search(text or "")
    if not m:
        m = re.fullmatch(r"\s*(?:([\w.-]+/[\w.-]+))?#(\d+)\s*", text or "")
    return (m.group(1), int(m.group(2))) if m else (None, None)


def watchers(folder, repo, issue):
    """The watchers to keep running, from the coordination issue's repo and number."""
    def one(name, suffix, repo, issue, agents):
        return {"name": name, "repo": repo, "issue": issue, "agents": agents,
                "pid": os.path.join(folder, "watcher%s.pid" % suffix),
                "state": os.path.join(folder, "watch%s.json" % suffix),
                "failed": os.path.join(folder, "watcher%s.failed" % suffix)}
    if repo and issue:
        return [one("project", "", None, None, True), one("coordination", "-coordination", repo, issue, False)]
    return [one("project", "", None, issue, True)]


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return ""


def stamp():
    now = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + ".%03dZ" % (now % 1 * 1000)


LOG_LOCK = threading.Lock()


def log(folder, line):
    with LOG_LOCK, open(os.path.join(folder, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(dict({"time": stamp()}, **line), ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- the pid files

def alive(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def command_of(pid):
    out = subprocess.run(["ps", "-p", str(pid), "-o", "stat=,command=", "-ww"], capture_output=True,
                         text=True, timeout=10)
    parts = out.stdout.strip().split(None, 1)
    if len(parts) < 2 or parts[0].startswith("Z"):
        return ""
    return parts[1]


def holder(path):
    """What the pid file says, if it names a live copy of this hook; else None."""
    try:
        with open(path) as f:
            held = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(held, dict) or not alive(held.get("pid")) or NAME not in command_of(held["pid"]):
        return None
    return held


def claim(path, me):
    """Write the pid file if no live watcher holds it. Returns True if this hook now holds it.
    me() gives what to write; it is called only when this hook takes the file.
    A lock file makes the check and the write one step, so hooks fired at once start one watcher."""
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        held = holder(path)
        if held is not None:
            if held.get("pid") == os.getpid():
                return False
            if alive(held.get("parent")):
                return False
            try:  # its Claude Code has exited: nobody would hear it
                os.kill(held["pid"], signal.SIGTERM)
            except OSError:
                pass
        tmp = "%s.%d" % (path, os.getpid())
        with open(tmp, "w") as f:
            json.dump(me(), f)
        os.replace(tmp, path)
        return True


def release(path, pid):
    try:
        with open(path) as f:
            if json.load(f).get("pid") != pid:
                return
        os.remove(path)
    except (OSError, ValueError, AttributeError):
        pass


def backing_off(path, seconds):
    try:
        return time.time() - os.path.getmtime(path) < seconds
    except OSError:
        return False


# ---------------------------------------------------------------- running the watchers

def load_watch():
    spec = importlib.util.spec_from_file_location("apb_watch", WATCH)
    watch = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(watch)
    return watch


def root_pid():
    """Claude Code's process, for the watcher's stuck-command check: the first ancestor of this hook
    that isn't a shell. None if it can't be found."""
    try:
        watch = load_watch()
        return watch.find_root(watch.parse_ps(watch.read_ps()), os.getpid())
    except Exception:
        return None


def stop_children():
    """Stop the watchers this hook started (its child processes)."""
    try:
        watch = load_watch()
        procs = watch.parse_ps(watch.read_ps())
    except Exception:
        return
    for pid, p in procs.items():
        if p["ppid"] == os.getpid():
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass


class Stopped(Exception):
    pass


def on_term(signum, frame):
    raise Stopped()


def run_once(opts, project, folder, w, root, minutes, session):
    """Run watcher w once. Returns (reason, output)."""
    cmd = [sys.executable, opts.watch_script, "--state", w["state"], "--limit", "%g" % minutes]
    if w["repo"]:
        cmd += ["--repo", w["repo"]]
    if w["issue"]:
        cmd += ["--issue", str(w["issue"])]
    if not w["agents"]:
        cmd += ["--events", os.path.join(folder, NO_EVENTS)]
    elif root:
        cmd += ["--root-pid", str(root)]
    log(folder, {"event": "watcher-start", "watcher": w["name"], "session": session, "pid": os.getpid(),
                 "repo": w["repo"], "issue": w["issue"]})
    try:
        # subprocess.run kills the watcher at the timeout.
        done = subprocess.run(cmd, cwd=project, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                              timeout=minutes * 60 + opts.grace)
    except subprocess.TimeoutExpired:
        return "timeout", ""
    out = done.stdout or ""
    if done.returncode != 0:
        return "error", (done.stderr or out).strip()
    body = [l for l in out.strip().splitlines()[1:] if l.strip()]
    if not body or all(l.startswith(("No change in", "GitHub could not be read")) for l in body):
        return "no change", out.strip()
    return "found", out.strip()


def keep_running(opts, project, folder, w, root, session, start, stopping, done):
    """Run watcher w again and again until it finds something, fails, or the budget runs out. Puts
    (w, reason, output) on done when it stops."""
    try:
        while True:
            left = opts.budget - (time.time() - start)
            minutes = max(min(opts.limit, left / 60), 0.01)
            reason, output = run_once(opts, project, folder, w, root, minutes, session)
            if stopping.is_set():  # this hook stopped the watcher itself
                return
            log(folder, {"event": "watcher-exit", "watcher": w["name"], "session": session, "reason": reason,
                         "detail": output[-MAX:] if reason == "error" else None})
            if reason in ("found", "error"):
                done.put((w, reason, output))
                return
            if time.time() - start >= opts.budget:
                done.put((w, "budget", ""))
                return
    except Exception as e:
        done.put((w, "error", str(e)))


def main(argv):
    opts = parse_args(argv)
    p = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    if not isinstance(p, dict) or p.get("agent_id"):
        return 0
    project = os.environ.get("CLAUDE_PROJECT_DIR") or p.get("cwd")
    if not isinstance(project, str) or not os.path.isfile(os.path.join(project, "docs", "MAP.md")):
        return 0
    folder = os.path.join(project, ".apb")
    os.makedirs(folder, exist_ok=True)
    ignore = os.path.join(folder, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w") as f:
            f.write("*\n")
    session = p.get("session_id")
    session = session[:MAX] if isinstance(session, str) else None
    repo, issue = coordination(opts.coordination) if opts.coordination else \
        coordination(read(os.path.join(project, "CLAUDE.md")))
    me = os.getpid()
    found = {}

    def mine():
        # The parent is Claude Code, not the hook's direct parent: that may be a shell that outlives it.
        if "root" not in found:
            found["root"] = root_pid()
        return {"pid": me, "parent": found["root"] or os.getppid(), "session": session}

    held = [w for w in watchers(folder, repo, issue)
            if not backing_off(w["failed"], opts.backoff) and claim(w["pid"], mine)]
    if not held:
        return 0
    stopping, done = threading.Event(), queue.Queue()
    signal.signal(signal.SIGTERM, on_term)
    try:
        start = time.time()
        for w in held:
            threading.Thread(target=keep_running, daemon=True,
                             args=(opts, project, folder, w, found.get("root"), session, start, stopping,
                                   done)).start()
        running = len(held)
        while running:
            try:
                w, reason, output = done.get(timeout=0.5)
            except queue.Empty:
                continue
            running -= 1
            release(w["pid"], me)
            if reason == "found":
                stopping.set()
                stop_children()
                sys.stderr.write("apb's %s watcher (started by the plugin's hook) found something. Act on it; "
                                 "the hook starts the watchers again on your next tool call.\n%s\n"
                                 % (w["name"], output))
                return 2
            if reason == "error":
                with open(w["failed"], "w") as f:
                    f.write(stamp() + "\n")
        return 0
    except Stopped:
        stopping.set()
        stop_children()
        return 0
    finally:
        for w in held:
            release(w["pid"], me)


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except BaseException:
        code = 0
    sys.exit(code)
