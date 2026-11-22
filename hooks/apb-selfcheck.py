"""The orchestrator's startup self-check (issue #133): one block, ✅ or ❌ per line, at session start.

Starting a new orchestrator session kept bringing old problems back (no watcher, posting as the human,
cards not moved, the Stop hook not running), and the human found each one later. Now the session's
first message starts with this block, and each ❌ comes up before other work:

  apb startup self-check · apb orch 4 · 0.3.59 · Nov 21 7:20 PM
  ✅ Watcher: hornof/apb #25 (pid 61155), held by this session
  ✅ GitHub account: gh posts as hornof-claude, not hornof
  ✅ Board: next to build: #130 ...; In progress: #133 (branch issue-133-startup-self-check)
  ✅ Hooks: Stop, prompt, tool-call and session-start hooks registered; this check ran from one
  ✅ Version: plugin 0.3.59, same as main; checkout on main, up to date

The lines, in order (CHECKS):
1. Watcher: the watchers hooks/apb-watch-start.py keeps for this folder are running (the same records
   `scripts/apb-watch.py --list` shows, matched by their state file in this folder's .apb), and
   .apb/watcher-owner.json names this session's Claude Code (one of this process's ancestors). With no
   owner file, or no ancestors to compare, the line is ✅ and says which session holds it is unknown. The watcher hook runs at the same session
   start, so this line waits up to --wait seconds for it.
2. GitHub account: `gh api user` with the session's environment is not the human's own login (gh
   without GH_TOKEN / GITHUB_TOKEN; if gh has no login of its own, the repo's owner).
3. Board: on the repo's project board, the top card of Todo is open and not blocked by an open issue;
   every card In progress has a branch (a local or remote branch whose name holds its number) or an
   open PR that closes it.
4. Hooks: the folder is one where apb's hooks act (hooks/apb_folder.py, #132); the installed plugin's
   hooks.json registers the watcher on session start, prompts and tool calls, and the Stop hook; no
   settings file turns hooks off (disableAllHooks) or turns the apb plugin off (enabledPlugins).
5. Version: hooks/apb-version.py's checks (#128): the plugin is as new as main's, and, in the apb repo,
   the checkout is on main and not behind origin/main.
To add a line: write a function check_x(ctx) that returns (ok, text) and add ("Label", check_x) to
CHECKS. The checks run side by side; one that raises shows ❌ with the error.

The session's name (issue #133's comment, hooks/apb_session.py) heads the block. The hook can't rename
the session itself (a hook can't call the app's tools), so it tells Claude to: with the desktop app's
mcp__ccd_session_mgmt__set_session_title, or else by asking the human in one line.

When it runs: Claude Code runs it from hooks/hooks.json at SessionStart (not async, so the block is in
Claude's context before its first message; 30 seconds at most, like the other hooks Claude Code waits for), in the main session (no agent_id), in an apb project or
the apb repo, and not in a linked worktree (build and review sessions; the orchestrator works in the
main checkout, #123). Not after a compaction. It prints the block as SessionStart additionalContext.
By hand: `python3 <apb plugin>/hooks/apb-selfcheck.py --print [--project <folder>]` prints the block and
exits 1 if any line is ❌.

Every git and gh call has a time limit (--timeout). Any error in the hook exits 0 with no output: the
hook never gets in Claude's way.
"""
import argparse
import importlib.util
import json
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import apb_session  # noqa: E402
from apb_folder import is_apb_folder, is_apb_repo  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)  # the installed plugin
TIMEOUT = 8  # seconds for each gh or git call; with WAIT, inside hooks.json's 30
WAIT = 10  # seconds to wait for the watcher the same session start launches
PAGES = 5  # board pages read, 100 cards each
TOKENS = ("GH_TOKEN", "GITHUB_TOKEN")  # gh reads these ahead of its own stored login
RENAME_TOOL = "mcp__ccd_session_mgmt__set_session_title"
REQUIRED_HOOKS = [("SessionStart", "apb-watch-start.py", "session-start"),
                  ("UserPromptSubmit", "apb-watch-start.py", "prompt"),
                  ("PreToolUse", "apb-watch-start.py", "tool-call"),
                  ("Stop", "apb-stop.py", "Stop")]


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_watch():
    spec = importlib.util.spec_from_file_location("apb_watch", os.path.join(ROOT, "scripts", "apb-watch.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Context:
    """What the checks need: project (folder), plugin_root, gh, timeout, wait, roots (the pids one of
    which is this session's Claude Code: this process's ancestors; empty if unknown), home, hook (True
    when run from the hook)."""

    def __init__(self, **kw):
        self.__dict__.update(kw)
        self.folder = os.path.join(self.project, ".apb")


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return ""


def read_json(path):
    try:
        return json.loads(read(path))
    except ValueError:
        return None


def gh_json(ctx, args, env=None):
    """gh's output as JSON, or None if it failed, timed out or isn't JSON."""
    try:
        out = subprocess.run([ctx.gh] + args, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                             timeout=ctx.timeout, env=env)
        return json.loads(out.stdout) if out.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def own_repo(ctx):
    return load("apb_watch_start", "apb-watch-start.py").own_repo(ctx.project)


# ---------------------------------------------------------------- 1. the watcher

def watcher_state(ctx, watch, expected):
    """(ok, text, settled) for the watchers now. settled is False while the holder is unknown (no owner
    file, or this process's ancestors can't be read): the line is ✅, since the watchers run, but the
    check waits a while longer for the owner file."""
    try:
        procs = watch.parse_ps(watch.read_ps())
    except Exception:
        procs = {}
    records = [r for r in watch.live_watchers(watch.registry_dir(), procs)
               if r.get("pid") in procs and "apb-watch" in procs[r["pid"]].get("command", "")]
    running, missing = [], []
    for w in expected:
        what = "%s%s" % (w["repo"] or own_repo(ctx) or "this repo", " #%d" % w["issue"] if w["issue"] else "")
        found = [r for r in records if os.path.realpath(str(r.get("state"))) == os.path.realpath(w["state"])]
        if found:
            running.append("%s (pid %d)" % (what, found[0]["pid"]))
        else:
            missing.append(what)
    if missing:
        return False, ("no watcher for %s is running (`apb-watch.py --list`). The plugin's hook starts it on "
                       "the next tool call; if it still isn't running, look for watcher-exit lines in "
                       ".apb/events.jsonl." % " or ".join(missing)), True
    owner = read_json(os.path.join(ctx.folder, "watcher-owner.json"))
    if not isinstance(owner, dict) or "root" not in owner:
        return True, "%s running; no owner file, so which session holds it is unknown" % " and ".join(running), False
    if not ctx.roots:
        return True, ("%s running; this session's processes can't be read, so which session holds it is unknown"
                      % " and ".join(running)), False
    if owner.get("root") not in ctx.roots:
        who = owner.get("name") or "session %s" % owner.get("session")
        return False, ("%s runs, but another session holds it: \"%s\". Only the newest orchestrator session "
                       "holds the watcher; if this is the newest, close the other one." % (" and ".join(running), who)), True
    return True, "%s, held by this session" % " and ".join(running), True


def check_watcher(ctx):
    start = load("apb_watch_start", "apb-watch-start.py")
    watch = load_watch()
    repo, issue = start.coordination(read(os.path.join(ctx.project, "CLAUDE.md")))
    if repo and (start.own_repo(ctx.project) or "").lower() == repo.lower():
        repo = None
    expected = start.watchers(ctx.folder, repo, issue)
    deadline = time.time() + ctx.wait
    while True:
        ok, text, settled = watcher_state(ctx, watch, expected)
        if (ok and settled) or time.time() >= deadline:
            return ok, text
        time.sleep(0.5)


# ---------------------------------------------------------------- 2. the GitHub account

def check_account(ctx):
    active = gh_json(ctx, ["api", "user"])
    active = active.get("login") if isinstance(active, dict) else None
    if not active:
        return False, "couldn't read which account gh uses (`gh api user`)."
    plain = {k: v for k, v in os.environ.items() if k not in TOKENS}
    human = gh_json(ctx, ["api", "user"], env=plain)
    human = human.get("login") if isinstance(human, dict) else None
    if not human:
        human = (own_repo(ctx) or "").split("/")[0] or None
    if human and active.lower() == human.lower():
        return False, ("gh posts as %s, the human's own account. Claude's token (GH_TOKEN in "
                       ".claude/settings.local.json, set by apb-setup) isn't in this session: set it, then open "
                       "a new session." % active)
    return True, "gh posts as %s%s" % (active, ", not %s" % human if human else "")


# ---------------------------------------------------------------- 3. the board

BOARD = """query($owner: String!, $name: String!, $after: String) {
  repository(owner: $owner, name: $name) { projectsV2(first: 1) { nodes { title url
    items(first: 100, after: $after, orderBy: {field: POSITION, direction: ASC}) {
      pageInfo { hasNextPage endCursor }
      nodes { fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
        content { ... on Issue { number title state url
          blockedBy(first: 20) { nodes { number state } }
          closedByPullRequestsReferences(first: 10, includeClosedPrs: false) { nodes { number state } } } } } } } } } }"""


def board_cards(ctx, repo):
    """[(status, issue)] in the board's order, or None if there is no board or it can't be read."""
    owner, name = repo.split("/", 1)
    cards, after, end = [], None, time.time() + 2 * ctx.timeout
    for _ in range(PAGES):
        if after and time.time() >= end:
            return cards  # out of time: the first pages hold the columns' tops
        args = ["api", "graphql", "-f", "owner=" + owner, "-f", "name=" + name, "-f", "query=" + BOARD]
        if after:
            args += ["-f", "after=" + after]
        got = gh_json(ctx, args)
        try:
            projects = got["data"]["repository"]["projectsV2"]["nodes"]
        except (TypeError, KeyError):
            return None
        if not projects:
            return cards if after else None
        items = projects[0]["items"]
        for item in items["nodes"]:
            content = item.get("content") or {}
            if "number" in content:
                cards.append((((item.get("fieldValueByName") or {}).get("name") or "").lower(), content))
        if not items["pageInfo"]["hasNextPage"]:
            return cards
        after = items["pageInfo"]["endCursor"]
    return cards


def branches(ctx):
    try:
        out = subprocess.run(["git", "-C", ctx.project, "for-each-ref", "--format=%(refname:short)",
                              "refs/heads", "refs/remotes"], stdin=subprocess.DEVNULL, capture_output=True,
                             text=True, timeout=ctx.timeout)
    except (OSError, subprocess.SubprocessError):
        return []
    return out.stdout.split() if out.returncode == 0 else []


def branch_of(number, names):
    hit = [b for b in names if re.search(r"(?<!\d)%d(?!\d)" % number, b)]
    return hit[0] if hit else None


def check_board(ctx):
    repo = own_repo(ctx)
    if not repo:
        return False, "couldn't tell this folder's GitHub repo (git remote origin)."
    cards = board_cards(ctx, repo)
    if cards is None:
        return False, "couldn't read the board of %s (no board linked, or gh failed)." % repo
    todo = [c for s, c in cards if s == "todo"]
    doing = [c for s, c in cards if s == "in progress"]
    problems, said = [], []
    if todo:
        top = todo[0]
        blockers = [b["number"] for b in (top.get("blockedBy") or {}).get("nodes") or [] if b.get("state") == "OPEN"]
        if top.get("state") != "OPEN":
            problems.append("the top of Todo, #%d, is closed: move it to Done" % top["number"])
        elif blockers:
            problems.append("the top of Todo, #%d, is blocked by %s: put a card that can be built on top"
                            % (top["number"], ", ".join("#%d" % b for b in blockers)))
        said.append("next to build: #%d %s" % (top["number"], top.get("title", "")))
    else:
        said.append("Todo is empty")
    names = branches(ctx)
    held = []
    for c in doing:
        prs = [p["number"] for p in (c.get("closedByPullRequestsReferences") or {}).get("nodes") or []
               if p.get("state") == "OPEN"]
        b = branch_of(c["number"], names)
        if b:
            held.append("#%d (branch %s)" % (c["number"], b))
        elif prs:
            held.append("#%d (PR #%d)" % (c["number"], prs[0]))
        else:
            problems.append("#%d is In progress with no branch or open PR" % c["number"])
    if problems:
        return False, "; ".join(problems) + "."
    said.append("In progress: %s" % (", ".join(held) if held else "none"))
    return True, "; ".join(said)


# ---------------------------------------------------------------- 4. the hooks

def check_hooks(ctx):
    problems = []
    if not is_apb_folder(ctx.project):
        problems.append("apb's hooks don't act in this folder (no docs/MAP.md, and not the apb repo)")
    hooks = read_json(os.path.join(ctx.plugin_root, "hooks", "hooks.json"))
    hooks = hooks.get("hooks") if isinstance(hooks, dict) else None
    if not isinstance(hooks, dict):
        problems.append("the plugin's hooks/hooks.json can't be read")
        hooks = {}
    names = []
    for event, script, what in REQUIRED_HOOKS:
        args = [a for group in hooks.get(event) or [] for h in group.get("hooks") or [] for a in h.get("args") or []]
        if not any(a.endswith("/" + script) or a == script for a in args) or \
                not os.path.isfile(os.path.join(ctx.plugin_root, "hooks", script)):
            problems.append("no %s hook (%s at %s)" % (what, script, event))
        else:
            names.append(what)
    for path in (os.path.join(ctx.home, ".claude", "settings.json"),
                 os.path.join(ctx.project, ".claude", "settings.json"),
                 os.path.join(ctx.project, ".claude", "settings.local.json")):
        s = read_json(path)
        if not isinstance(s, dict):
            continue
        if s.get("disableAllHooks") is True:
            problems.append("disableAllHooks is on in %s" % path)
        plugins = s.get("enabledPlugins")
        if isinstance(plugins, dict):
            for key, on in plugins.items():
                if key.split("@")[0] == "apb" and on is False:
                    problems.append("%s is turned off in %s (enabledPlugins)" % (key, path))
    if problems:
        return False, "; ".join(problems) + "."
    return True, "%s hooks registered%s" % (", ".join(sorted(names, key=lambda n: n != "Stop")),
                                           "; this check ran from the session-start hook" if ctx.hook else "")


# ---------------------------------------------------------------- 5. the version

def check_version(ctx):
    version = load("apb_version", "apb-version.py")
    installed, main, note = version.plugin_check(ctx.plugin_root, ctx.gh, ctx.timeout)
    checkout = version.checkout_note(ctx.project, ctx.timeout)
    problems = [n for n in (note, checkout) if n]
    if main is None:
        problems.insert(0, "couldn't read main's plugin version from GitHub (this session runs %s)" % installed)
    if problems:
        return False, " ".join(problems)
    text = "plugin %s, same as main" % installed
    if is_apb_repo(ctx.project):
        text += "; checkout on main, up to date"
    return True, text


# ---------------------------------------------------------------- the block

CHECKS = [("Watcher", check_watcher),
          ("GitHub account", check_account),
          ("Board", check_board),
          ("Hooks", check_hooks),
          ("Version", check_version)]


def run_checks(ctx, checks=None):
    """[(label, ok, text)] in the order of checks, run side by side."""
    checks = CHECKS if checks is None else checks

    def one(check):
        label, fn = check
        try:
            ok, text = fn(ctx)
        except Exception as e:
            ok, text = False, "the check failed: %s: %s" % (type(e).__name__, e)
        return label, bool(ok), text

    with ThreadPoolExecutor(max_workers=max(len(checks), 1)) as pool:
        return list(pool.map(one, checks))


def block(name, results):
    head = "apb startup self-check" + (" · " + name if name else "")
    return "\n".join([head] + ["%s %s: %s" % ("✅" if ok else "❌", label, text) for label, ok, text in results])


def instructions(name, results):
    red = [label for label, ok, _ in results if not ok]
    lines = ["apb startup self-check (the plugin's SessionStart hook, issue #133). Put the block below, as is, "
             "at the top of your first message to the human, before anything else, whatever that message asks."]
    if red:
        lines.append("Then raise each ❌ line (%s), with what to do about it, before any other work."
                     % ", ".join(red))
    else:
        lines.append("Every line is ✅.")
    lines.append("If the version check also gave a note at session start, it is the Version line: say it once.")
    if name:
        lines.append("This session's name is `%s`. Rename the session to it: if you have the tool %s (the "
                     "Claude desktop app; load it with ToolSearch if it is deferred), call it with session_id "
                     "\"self\" and that title. Without the tool (the CLI), ask the human in one line to rename the "
                     "session to it." % (name, RENAME_TOOL))
    return "\n".join(lines) + "\n\n" + block(name, results)


# ---------------------------------------------------------------- running it

def parse_args(argv):
    a = argparse.ArgumentParser(description="apb's orchestrator startup self-check.")
    a.add_argument("--print", action="store_true", help="run by hand: no payload, print the block")
    a.add_argument("--project", help="with --print: the folder to check (default: here)")
    a.add_argument("--gh", default="gh", help="the gh command (tests use a fake)")
    a.add_argument("--plugin-root", default=ROOT, help="the installed plugin's folder")
    a.add_argument("--home", default=os.path.expanduser("~"), help="the folder holding .claude/settings.json")
    a.add_argument("--root-pid", type=int, help="Claude Code's process; default: found from this script's parents")
    a.add_argument("--timeout", type=float, default=TIMEOUT, help="seconds for each gh or git call")
    a.add_argument("--wait", type=float, default=WAIT, help="seconds to wait for the watcher")
    return a.parse_args(argv)


def ancestors():
    """This process's ancestors, nearest first: Claude Code is one of them (run by hand, a wrapper such
    as `timeout` may sit in between, so the check doesn't stop at the first one that isn't a shell)."""
    try:
        watch = load_watch()
        procs = watch.parse_ps(watch.read_ps())
    except Exception:
        return []
    found, pid = [], procs.get(os.getpid(), {}).get("ppid")
    while pid in procs and pid not in found and pid > 1:
        found.append(pid)
        pid = procs[pid]["ppid"]
    return found


def installed_version(root):
    m = read_json(os.path.join(root, ".claude-plugin", "plugin.json"))
    return m.get("version") if isinstance(m, dict) else None


def context(opts, project, hook):
    roots = [opts.root_pid] if opts.root_pid is not None else ancestors()
    return Context(project=project, plugin_root=opts.plugin_root, gh=opts.gh, timeout=opts.timeout,
                   wait=opts.wait, roots=roots, home=opts.home, hook=hook)


def main(argv):
    opts = parse_args(argv)
    if opts.print:
        ctx = context(opts, os.path.abspath(opts.project or os.getcwd()), False)
        results = run_checks(ctx)
        owner = read_json(os.path.join(ctx.folder, "watcher-owner.json"))
        name = owner.get("name") if isinstance(owner, dict) and owner.get("root") in ctx.roots else None
        print(block(name, results))
        return 0 if all(ok for _, ok, _ in results) else 1
    p = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    if not isinstance(p, dict) or p.get("agent_id") or p.get("source") == "compact":
        return 0
    project = os.environ.get("CLAUDE_PROJECT_DIR") or p.get("cwd")
    if not is_apb_folder(project):
        return 0
    if load("apb_watch_start", "apb-watch-start.py").linked_worktree(project):
        return 0  # a build or review session: only the orchestrator's checkout runs the check
    ctx = context(opts, project, True)
    name = apb_session.session_name(ctx.folder, p.get("session_id"), installed_version(opts.plugin_root))
    results = run_checks(ctx)
    sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                                        "additionalContext": instructions(name, results)}},
                                ensure_ascii=False) + "\n")
    return 0


if __name__ == "__main__":
    try:
        code = main(sys.argv[1:])
    except BaseException:
        code = 0 if "--print" not in sys.argv else 2
    sys.exit(code)
