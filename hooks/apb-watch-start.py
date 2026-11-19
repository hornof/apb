"""Keep the watcher (scripts/apb-watch.py) running for the orchestrator, and wake it when the watcher
finds something.

Claude Code runs this from hooks/hooks.json at the start of a session, on each prompt, before each
tool call and at each stop, with the event's JSON on stdin. It is an asyncRewake hook: it runs in the
background, and when it exits with code 2, what it printed on stderr wakes the model.

- It acts only in an apb project (one with docs/MAP.md), and only for the main session: a tool call
  from a builder or reviewer (its payload has an agent_id) starts nothing.
- No second copy. The hook that starts the watcher writes .apb/watcher.pid (under a lock, so hooks
  fired at once start one watcher). Any other hook that finds a live watcher there exits at once. A pid file whose process is gone, or is some other program, doesn't count. Nor does one whose
  Claude Code has exited: that watcher is stopped and replaced.
- The coordination issue comes from --coordination, or from a "coordination:" line in the project's
  CLAUDE.md: "coordination: #25" (this repo) or "coordination: owner/name#25". With neither, the
  watcher runs without an issue.
- Each start of the watcher writes a watcher-start line to .apb/events.jsonl, and each exit a
  watcher-exit line with the reason: found, no change, error or timeout.
- When the watcher reports something, this hook prints it on stderr and exits 2, which wakes the
  model, and frees the pid file, so the next tool call starts the watcher again.
- When the watcher reports no change, it is started again at once, until --budget runs out, so the
  watcher also runs while the orchestrator waits with no tool calls.
- When the watcher fails, the hook exits 0 without waking the model, and for --backoff seconds no
  hook starts it again.
- Every watcher run has a time limit: its --limit, plus --grace seconds. Stopping this hook (SIGTERM,
  as at Claude Code's hook timeout) stops the watcher too.

Any other error exits 0: the hook never gets in Claude's way.
"""
import argparse
import fcntl
import importlib.util
import json
import os
import re
import signal
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(os.path.dirname(HERE), "scripts", "apb-watch.py")
BUDGET = 23 * 3600  # seconds this hook keeps the watcher going; less than its timeout in hooks.json
LIMIT = 60  # minutes, the watcher's own limit for one run
GRACE = 120  # seconds a watcher run may go past its limit before it is stopped
BACKOFF = 60  # seconds after a failed run before a hook tries again
NAME = "apb-watch-start.py"
MAX = 300  # longest string kept in an events line, in characters
COORDINATION = re.compile(r"^[\s>*+-]*\**coordination:?\**:?\s*(?:([\w.-]+/[\w.-]+))?#(\d+)",
                          re.IGNORECASE | re.MULTILINE)


def parse_args(argv):
    a = argparse.ArgumentParser(description="Keep apb's watcher running for the orchestrator.")
    a.add_argument("--coordination", help="owner/name#N or #N: the issue the watcher also watches")
    a.add_argument("--watch-script", default=WATCH, help="the watcher to run (tests use a fake)")
    a.add_argument("--budget", type=float, default=BUDGET, help="seconds to keep the watcher going")
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


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return ""


def stamp():
    now = time.time()
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + ".%03dZ" % (now % 1 * 1000)


def log(folder, line):
    with open(os.path.join(folder, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(dict({"time": stamp()}, **line), ensure_ascii=False) + "\n")


# ---------------------------------------------------------------- the pid file

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
    A lock file makes the check and the write one step, so hooks fired at once start one watcher."""
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        held = holder(path)
        if held is not None:
            if alive(held.get("parent")):
                return False
            try:  # its Claude Code has exited: nobody would hear it
                os.kill(held["pid"], signal.SIGTERM)
            except OSError:
                pass
        tmp = "%s.%d" % (path, os.getpid())
        with open(tmp, "w") as f:
            json.dump(me, f)
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


# ---------------------------------------------------------------- running the watcher

def root_pid():
    """Claude Code's process, for the watcher's stuck-command check: the first ancestor of this hook
    that isn't a shell. None if it can't be found."""
    try:
        spec = importlib.util.spec_from_file_location("apb_watch", WATCH)
        watch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(watch)
        return watch.find_root(watch.parse_ps(watch.read_ps()), os.getpid())
    except Exception:
        return None


class Stopped(Exception):
    pass


def on_term(signum, frame):
    raise Stopped()


def run_once(opts, project, folder, repo, issue, root, minutes, session):
    """Run the watcher once. Returns (reason, output)."""
    cmd = [sys.executable, opts.watch_script, "--state", os.path.join(folder, "watch.json"),
           "--limit", "%g" % minutes]
    if repo:
        cmd += ["--repo", repo]
    if issue:
        cmd += ["--issue", str(issue)]
    if root:
        cmd += ["--root-pid", str(root)]
    log(folder, {"event": "watcher-start", "session": session, "pid": os.getpid(), "repo": repo,
                 "issue": issue})
    try:
        # subprocess.run kills the watcher at the timeout, and on any exception, such as Stopped.
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
    failed = os.path.join(folder, "watcher.failed")
    try:
        if time.time() - os.path.getmtime(failed) < opts.backoff:
            return 0
    except OSError:
        pass
    session = p.get("session_id")
    session = session[:MAX] if isinstance(session, str) else None
    pid_path = os.path.join(folder, "watcher.pid")
    me = os.getpid()
    if not claim(pid_path, {"pid": me, "parent": os.getppid(), "session": session}):
        return 0
    signal.signal(signal.SIGTERM, on_term)
    try:
        repo, issue = coordination(opts.coordination) if opts.coordination else \
            coordination(read(os.path.join(project, "CLAUDE.md")))
        root = root_pid()
        start = time.time()
        while True:
            left = opts.budget - (time.time() - start)
            minutes = max(min(opts.limit, left / 60), 0.01)
            reason, output = run_once(opts, project, folder, repo, issue, root, minutes, session)
            log(folder, {"event": "watcher-exit", "session": session, "reason": reason,
                         "detail": output[-MAX:] if reason == "error" else None})
            if reason == "found":
                release(pid_path, me)
                sys.stderr.write("apb's watcher (started by the plugin's hook) found something. Act on it; "
                                 "the hook starts the watcher again on your next tool call.\n" + output + "\n")
                return 2
            if reason == "error":
                with open(failed, "w") as f:
                    f.write(stamp() + "\n")
                return 0
            if time.time() - start >= opts.budget:
                return 0
    except Stopped:
        return 0
    finally:
        release(pid_path, me)


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except BaseException:
        code = 0
    sys.exit(code)
