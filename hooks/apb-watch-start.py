"""Keep the watchers (scripts/apb-watch.py) running for the orchestrator, and wake it when a watcher
finds something.

Claude Code runs this from hooks/hooks.json at the start of a session, on each prompt, before each
tool call and at each stop, with the event's JSON on stdin. It is an asyncRewake hook: it runs in the
background, and when it exits with code 2, what it printed on stderr wakes the model.

- It acts only in an apb project (one with docs/MAP.md) or in the apb repo itself, and only for the
  main session: a tool call from a builder or reviewer (its payload has an agent_id) starts nothing.
  The apb repo has no docs/MAP.md; it is the folder whose .claude-plugin/plugin.json names the plugin
  "apb" and that holds this hook at hooks/apb-watch-start.py (hooks/apb_folder.py, which every hook
  shares). There the hook runs the project watcher on the folder's own repo, as in a project.
- The coordination issue comes from --coordination, or from a "coordination:" line in the project's
  CLAUDE.md: "coordination: #25" (this repo) or "coordination: owner/name#25" (another repo).
- One or two watchers:
  - the project watcher, on the project's own repo: its PRs, stuck commands and quiet agents. When
    the coordination issue is in this repo, it also watches that issue.
  - the coordination watcher, only when the coordination issue is in another repo: that repo's PRs
    and the issue. A line that names the project's own repo (its git origin) counts as "#N". It leaves stuck commands and quiet agents to the project watcher, so they wake
    the orchestrator once.
  With no coordination line, the project watcher runs without an issue.
  The project watcher also follows the cards in .apb/follow.json (`apb-watch.py --follow N`, #165), so
  the orchestrator never runs a second watcher of its own, and one PR event wakes it once.
- Only the orchestrator's session (#123). Build and review sessions run in linked git worktrees (apb-build
  gives each builder its own; Claude Code's own isolation makes them under .claude/worktrees/), while
  the orchestrator works in the repo's main checkout. So a session whose folder is a linked worktree
  (git's git dir differs from its common dir) starts nothing. That is git's own record, not a folder
  name: a worktree anywhere counts, and a main checkout counts as one wherever it sits.
- The newest session in a folder takes the watchers over. After a plugin update the human opens a new
  orchestrator session while the old one is often still open. A hook whose Claude Code started later
  than the holder's stops the holder's hook (and so its watchers), waits up to TAKEOVER_WAIT seconds
  for it to exit, and takes the pid file. If it hasn't exited, the hook takes nothing and logs a
  watcher-takeover-refused line (#170); the next hook tries again. A failed ps never counts as "exited". It also writes .apb/watcher-owner.json, naming its Claude Code process, when that
  started, and the session's name, `apb orch N · <version> · <start time PT>` (hooks/apb_session.py,
  #133), which `apb-watch.py --list` shows. An older session's hook starts nothing while that process is open, even after the newer
  hook has exited (after a wake, a failure or the budget) and freed the pid files, so it never takes
  the watchers back. Once the newer session is closed, the older one may run them again. Hooks of the same session never take over from each other (same Claude Code process).
  Start times come from one ps snapshot, to the second; a tie takes nothing over.
- A reopened session takes nothing over (#146). Reopening (resuming) an old session starts a new
  Claude Code process, so by start time it looks like the newest. So a session that is resumed never
  takes the watchers from an open session that holds them, even while that session's hook is between
  runs, and gets no orchestrator number: the owner file carries the name it already had, or none. It
  still starts the watchers when no open session holds them. A session counts as resumed when its
  SessionStart said so (`source` "resume", recorded in .apb/resumed.json, since later events don't
  carry it), or when its first line in .apb/events.jsonl comes before the holder's (hooks/apb_session.py).
- No second copy of either. Each watcher has its own pid file in .apb/ (watcher.pid,
  watcher-coordination.pid), written under a lock, so hooks fired at once start each watcher once.
  A hook takes every pid file that is free and runs those watchers side by side; a hook that finds
  both taken exits at once. A pid file whose process is gone, or is some other program, doesn't
  count. Nor does one whose Claude Code has exited: that hook is stopped and replaced.
- Each start of a watcher writes a watcher-start line to .apb/events.jsonl, and each exit a
  watcher-exit line with the reason: found, no change, error or timeout. Both name the watcher.
- When either watcher reports something, this hook stops its other watcher, frees its pid files,
  prints the report on stderr and exits 2, which wakes the model. The next tool call starts both again.
  When main's apb plugin is newer than this session's and the report has a merge (or main is at a
  version this session hasn't been told), the report ends with a line saying so (apb-version.py, #128).
- When a watcher reports no change, it is started again at once, until --budget runs out, so the
  watchers also run while the orchestrator waits with no tool calls.
- The hook passes no --poll, so its watchers read GitHub at the watcher's own pace: every 2 minutes
  while the repo is active, every 15 minutes once it has been quiet for an hour (scripts/apb-watch.py).
- Lifetime: an idle session's watchers live for BUDGET, 23 hours, on purpose. Quiet, a watcher costs a
  few points an hour, and an unattended run (apb-loop) has no human messages for hours but still needs
  waking on a merge. `apb-watch.py --list` shows each running watcher and its age.
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

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from apb_folder import is_apb_folder, is_apb_repo  # noqa: E402,F401  (tests call is_apb_repo here)
import apb_session  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
WATCH = os.path.join(os.path.dirname(HERE), "scripts", "apb-watch.py")
BUDGET = 23 * 3600  # seconds this hook keeps the watchers going; less than its timeout in hooks.json
LIMIT = 60  # minutes, the watcher's own limit for one run
GRACE = 120  # seconds a watcher run may go past its limit before it is stopped
BACKOFF = 60  # seconds after a failed run before a hook tries that watcher again
NAME = "apb-watch-start.py"
MAX = 300  # longest string kept in an events line, in characters
OWNER_SLACK = 3  # seconds two readings of one process's start time may differ by
TAKEOVER_WAIT = 10  # seconds to wait for a replaced hook to exit before refusing the takeover (#170)
PS = "ps"  # the ps command (tests use a broken one)
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
    a.add_argument("--gh", default="gh", help="the gh command for the version check (tests use a fake)")
    return a.parse_args(argv)


def coordination(text):
    """(repo or None, issue number or None) from 'owner/name#N' or '#N', or from a CLAUDE.md text."""
    m = COORDINATION.search(text or "")
    if not m:
        m = re.fullmatch(r"\s*(?:([\w.-]+/[\w.-]+))?#(\d+)\s*", text or "")
    return (m.group(1), int(m.group(2))) if m else (None, None)


def repo_of(url):
    """'owner/name' from a git remote URL (https, ssh or scp style), or None."""
    m = re.search(r"[:/]([\w.-]+)/([\w.-]+?)(?:\.git)?/?\s*$", url or "")
    return "%s/%s" % (m.group(1), m.group(2)) if m else None


def own_repo(project):
    """The project's own repo, 'owner/name', from its origin remote; None if git can't tell."""
    try:
        out = subprocess.run(["git", "-C", project, "remote", "get-url", "origin"], capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return repo_of(out.stdout) if out.returncode == 0 else None



def watchers(folder, repo, issue):
    """The watchers to keep running, from the coordination issue's repo and number."""
    def one(name, suffix, repo, issue, agents):
        w = {"name": name, "repo": repo, "issue": issue, "agents": agents,
             "pid": os.path.join(folder, "watcher%s.pid" % suffix),
             "state": os.path.join(folder, "watch%s.json" % suffix),
             "failed": os.path.join(folder, "watcher%s.failed" % suffix)}
        if agents:  # the project watcher also follows the cards (#165)
            w["follow"] = os.path.join(folder, "follow.json")
        return w
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
    """The process's command line; "" if ps says it has exited (no such process, or a zombie); None if
    ps itself failed, so nobody can tell (#170)."""
    try:
        out = subprocess.run([PS, "-p", str(pid), "-o", "stat=,command=", "-ww"], capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    parts = out.stdout.strip().split(None, 1)
    if len(parts) >= 2 and not parts[0].startswith("Z"):
        return parts[1]
    if parts and parts[0].startswith("Z"):
        return ""
    # ps -p exits 1, saying nothing, when no process has that pid; anything else is ps failing.
    return "" if out.returncode == 1 and not out.stderr.strip() else None


def holder(path):
    """What the pid file says, if it names a live copy of this hook; else None."""
    try:
        with open(path) as f:
            held = json.load(f)
    except (OSError, ValueError):
        return None
    if not isinstance(held, dict) or not alive(held.get("pid")):
        return None
    command = command_of(held["pid"])
    if command is not None and NAME not in command:  # ps can't tell: it still holds the file
        return None
    return held


def linked_worktree(folder):
    """True if folder is a linked git worktree (made by `git worktree add`), not a repo's main
    checkout. Anything git can't answer counts as no worktree, so the watcher runs as before."""
    try:
        out = subprocess.run(["git", "-C", folder, "rev-parse", "--absolute-git-dir", "--path-format=absolute",
                              "--git-common-dir"], capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError, TypeError, ValueError):
        return False
    lines = out.stdout.split("\n")
    if out.returncode != 0 or len(lines) < 2 or not lines[0] or not lines[1]:
        return False
    return os.path.realpath(lines[0]) != os.path.realpath(lines[1])


def start_times():
    """{pid: when it started, in epoch seconds} for every process, from one ps snapshot (to the second);
    {} if ps can't be read."""
    try:
        watch = load_watch()
        now = time.time()
        return {pid: now - p["elapsed"] for pid, p in watch.parse_ps(watch.read_ps()).items()}
    except Exception:
        return {}


def started_later(mine, theirs):
    """True if process mine started after process theirs, by one ps snapshot (to the second)."""
    times = start_times()
    return mine in times and theirs in times and times[mine] > times[theirs] + 0.5


def newer_owner(path, root, resumed=lambda session: False):
    """True if the owner file names another session's Claude Code that is still open and started no
    earlier than this one (root), or that is open at all when resumed(its session) says this session
    was reopened (#146): then this session leaves the watchers to it, even while its hook is between
    runs and the pid files are free. A pid now used by another process doesn't count."""
    try:
        with open(path) as f:
            owner = json.load(f)
        pid, started = owner["root"], float(owner["started"])
    except (OSError, ValueError, TypeError, KeyError):
        return False
    if not isinstance(pid, int) or pid == root:
        return False
    times = start_times()
    if pid not in times or abs(times[pid] - started) > OWNER_SLACK or not alive(pid):
        return False  # closed, or its pid reused
    return root not in times or times[root] <= times[pid] + 0.5 or bool(resumed(owner.get("session")))


def plugin_version():
    try:
        with open(os.path.join(os.path.dirname(HERE), ".claude-plugin", "plugin.json"), encoding="utf-8") as f:
            return json.load(f).get("version")
    except (OSError, ValueError, AttributeError):
        return None


def own(path, root, session, assign=True):
    """Write the owner file: this session now runs the watchers. It carries the session's name
    (hooks/apb_session.py, #133), so `apb-watch.py --list` can say which session holds them. A reopened
    session (assign False, #146) is given no number: its name is the one it had, or None."""
    started = start_times().get(root)
    if started is None:
        return
    try:
        name = apb_session.session_name(os.path.dirname(path), session, plugin_version(), assign=assign)
    except Exception:
        name = None
    tmp = "%s.%d" % (path, os.getpid())
    with open(tmp, "w") as f:
        json.dump({"root": root, "started": started, "session": session, "name": name}, f, ensure_ascii=False)
    os.replace(tmp, path)


def gone(pid, seconds):
    end = time.time() + seconds
    while time.time() < end:
        if not alive(pid) or command_of(pid) == "":
            return True
        time.sleep(0.05)
    return False


def claim(path, me, takes_over=lambda held: False):
    """Write the pid file if no live watcher holds it, or if takes_over(holder) says this session
    replaces the holder's. Returns True if this hook now holds it.
    me() gives what to write; it is called only when this hook takes the file.
    A lock file makes the check and the write one step, so hooks fired at once start one watcher."""
    with open(path + ".lock", "w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        held = holder(path)
        if held is not None:
            if held.get("pid") == os.getpid():
                return False
            if alive(held.get("parent")) and not takes_over(held):
                return False
            refused = lambda: log(os.path.dirname(path), {"event": "watcher-takeover-refused", "pid": os.getpid(),
                                                          "from": held.get("session"), "holder": held["pid"]})
            if command_of(held["pid"]) is None:  # ps can't say what it is: it may be a reused pid, so no signal
                refused()
                return False
            try:  # its Claude Code has exited (nobody would hear it), or a newer session takes over
                os.kill(held["pid"], signal.SIGTERM)
            except OSError:
                pass
            if not gone(held["pid"], TAKEOVER_WAIT):  # it stops its watchers and frees its pid file first
                refused()
                return False  # two watchers must never run at once; the next hook tries again
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


def version_note(opts, project, session, output):
    """The plugin-version line for a wake (hooks/apb-version.py, issue #128), or ""."""
    try:
        spec = importlib.util.spec_from_file_location("apb_version", os.path.join(HERE, "apb-version.py"))
        version = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(version)
        note = version.wake_note(project, session, output, gh=opts.gh)
    except Exception:
        return ""
    return "\n" + note + "\n" if note else ""


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


def watcher_argv(w, folder, root, minutes, script=WATCH):
    """The command that runs watcher w once."""
    cmd = [sys.executable, script, "--state", w["state"], "--limit", "%g" % minutes]
    if w["repo"]:
        cmd += ["--repo", w["repo"]]
    if w["issue"]:
        cmd += ["--issue", str(w["issue"])]
    if w.get("follow"):
        cmd += ["--follow-file", w["follow"]]
    if not w["agents"]:
        cmd += ["--events", os.path.join(folder, NO_EVENTS)]
    elif root:
        cmd += ["--root-pid", str(root)]
    return cmd


def run_once(opts, project, folder, w, root, minutes, session):
    """Run watcher w once. Returns (reason, output)."""
    cmd = watcher_argv(w, folder, root, minutes, opts.watch_script)
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
    if not is_apb_folder(project) or linked_worktree(project):
        return 0  # a build or review session's worktree: only the orchestrator's session watches
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
    if repo and (own_repo(project) or "").lower() == repo.lower():
        repo = None  # the project's own repo: one watcher, as with "#N"
    if p.get("source") == "resume":
        apb_session.mark_resumed(folder, session)  # later events don't carry the source (#146)
    me = os.getpid()
    found = {}
    reopened = {}

    def marked():
        if "marked" not in reopened:
            reopened["marked"] = p.get("source") == "resume" or apb_session.is_resumed(folder, session)
        return reopened["marked"]

    def resumed(other):
        """True if this session was reopened, so it leaves an open holder (other: its session) alone."""
        return marked() or apb_session.active_before(folder, session, other)

    def mine():
        # The parent is Claude Code, not the hook's direct parent: that may be a shell that outlives it.
        if "root" not in found:
            found["root"] = root_pid()
        return {"pid": me, "parent": found["root"] or os.getppid(), "session": session}

    def takes_over(other):
        """True if this session is newer than the one holding the watcher (#123)."""
        mine()
        root = found["root"]
        if root is None or not isinstance(other.get("parent"), int):
            return False
        if started_later(root, other["parent"]) and not resumed(other.get("session")):
            found.setdefault("from", other.get("session"))
            return True
        return False

    owner = os.path.join(folder, "watcher-owner.json")
    mine()
    if newer_owner(owner, found["root"], resumed):
        return 0  # a newer session runs the watchers (or this one was reopened); its hook may be between runs
    held = [w for w in watchers(folder, repo, issue)
            if not backing_off(w["failed"], opts.backoff) and claim(w["pid"], mine, takes_over)]
    if not held:
        return 0
    if found["root"] is not None:
        own(owner, found["root"], session, assign=not marked())
    if "from" in found:
        log(folder, {"event": "watcher-takeover", "session": session, "from": found["from"], "pid": me})
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
                                 "the hook starts the watchers again on your next tool call.\n%s\n%s"
                                 % (w["name"], output, version_note(opts, project, session, output)))
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
