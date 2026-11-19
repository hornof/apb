"""Stop hook: when the orchestrator's work runs out, its message must say who holds the ball.

Claude Code runs this from hooks/hooks.json when a turn of the main session ends, with the event's
JSON on stdin. It acts only in an apb project (one with docs/MAP.md), and reads .apb/events.jsonl,
which hooks/apb-events.py writes.

- The work ran out in this turn when nothing is running, and either an agent stopped since the last
  stop or the turn was not started by a prompt (a background command finishing woke it). See the
  playbook's "Helping the human keep track". What's running comes from the Stop input's
  background_tasks; without it, from the events file (agents started and not stopped, and background
  commands started in this turn).
- Then the message must start with one of the four first lines. If it doesn't, the hook sends the
  message back once, with a note to Claude (additionalContext) that starts "Say who holds the ball",
  and Claude goes on to add the line. "All clear: ..." is logged as holder human, todo "all clear".
  One exception: a wake-up that changed nothing for the human. When no prompt and no agent's stop
  started the turn (a watcher or a background command woke it; the SubagentStop with agent_type ""
  that Claude Code's own helper logs after each stop is not an agent's), an earlier stop in this session
  already said who holds the ball, and the message is empty or one word, the stop goes through and
  the ball stays where it was.
- On any stop, if the message states a GitHub fact (a sentence that gives a PR, issue or card with a
  number, a link to one, or a count such as "13 PRs", a state: open, merged, closed, done, in review,
  on the board or in a column), outside quotes and outside a plan ("once …", "after …"), and the
  orchestrator itself ran no lookup that
  finished without error in this turn, the hook sends the message back once, with a note that starts
  "You stated a GitHub fact without checking it this turn." A lookup is a gh or git command, or a run
  of scripts/map-artifact.py or scripts/progress-page.py, which call gh themselves. It checks only
  that some lookup ran in that turn, not which one, and not whether the fact is true. When both
  checks fail, one send-back carries both notes.
- On any stop whose first line hands something to the human, if an agent's report said it departed
  from its file (a Departure line, which hooks/apb-brief.py writes) and no DepartureResolved line came
  after it, the hook sends the message back, on a retry too, with a note that quotes the report and
  says how to resolve it. After MAX_DEPARTURE_SEND_BACKS in one turn it lets the stop through.
- On any stop, if the message asks the human for a decision (a "1 thing" or "N things" line that names
  one, "answer decision N", "N decisions for you", "which do you want", "your call", a question that
  names one, or lettered options) and has no link to a GitHub PR or issue outside a quoted line, the
  hook sends the message back once, with a note that starts "A decision was asked without a GitHub
  link." A line that only mentions or quotes a decision asks nothing. Under an "N things" first line,
  each numbered item that names a decision needs the link on its own line. Every decision lives on
  GitHub; the hand-off only points at it.
- On any stop whose first line hands something to the human, the hook looks up what the "1 thing" line,
  or each numbered item under "N things", asks the human to act on: each decision thread it links
  (…/pull/N#discussion_rM), and, when the line says "merge", each PR it links, or else each "PR #N" it
  names, in the project's own repo. If a thread is already resolved, or a PR to merge is already merged
  or closed, it sends the message back once, with a note that starts "A hand-off asks the human for
  something that is already done." It looks up at most MAX_DONE of them.
- On any stop whose first line hands something to the human, if the message links an open PR in the
  apb repo (the repository in the plugin's manifest) that touches templates/, hooks/, agents/, a
  skill's SKILL.md or docs/playbook.md, and that PR's description has no "Projects:" line ("Projects: none",
  or "Projects:" with links to the projects' PRs that apply the change), the hook sends the message
  back once, with a note that starts "An apb PR in this hand-off changes how projects run." It looks
  each PR up with gh (at most MAX_PRS).
- Both checks share one gh time budget (PR_BUDGET seconds, at most PR_TIME_LIMIT for one call) and look
  each PR up once. Whatever can't be looked up in time is let through.
- It appends a Stop line to the events file, with who holds the ball when the message says so, the
  unchecked facts ("unchecked"), "decision-without-link", what was already done ("already-done") and the
  PRs missing their Projects line ("projects-line-missing") when those were why it sent the message back.
- If the human holds the ball, other than after "All clear", it starts a reminder in the background: if the human has done
  nothing after REMIND_AFTER seconds (no new prompt), one desktop alert. Only on a Mac. A prompt or a
  new first line ends the wait; a stop with neither (Claude woken by a watcher or an agent) doesn't.
  The reminder outlives the hook, so it is given a deadline (the wait plus LIFETIME_MARGIN) and
  ends there, with any alert it started.

Any error lets the stop through: the hook never stops Claude from finishing.
"""
import calendar
import json
import os
import re
import signal
import subprocess
import sys
import time

ABANDONED = 3 * 3600  # an agent silent this long is taken to be gone; same as scripts/apb-watch.py
REMIND_AFTER = 30 * 60  # seconds the human holds the ball, with no action, before one reminder
LIFETIME_MARGIN = 60  # seconds the reminder may run past its wait before its deadline ends it
MAX = 300  # longest string kept in a line, in characters

REASON = ("Say who holds the ball. Your work just ran out, so start the message with one of these "
          "lines (playbook, \"Helping the human keep track\"):\n"
          "- Nothing for you to do right now. Waiting on <what>, ETA <time>. (A clock time, such as "
          "7:10 PM; run date first.)\n"
          "- 1 thing for you to do right now: <the thing>.\n"
          "- N things for you to do right now: followed by a numbered list of exactly N things.\n"
          "- All clear: <boards> empty, no open PRs, as of <time>. (Once, when nothing is open on any "
          "board or repo you watch; nothing under it.)")

DEPARTED = ("An agent's report says it departed from its file, and that isn't resolved: %s. Put it right "
            "before the human sees anything (for example, launch the agent again with the task only). Then "
            "run, from the project's root: python3 <apb plugin folder>/hooks/apb-brief.py resolve "
            "\"<what you did>\". Until then, don't hand anything to the human.")
MAX_DEPARTURE_SEND_BACKS = 3  # in one turn; then the stop goes through, so Claude can never loop forever

UNCHECKED = ("You stated a GitHub fact without checking it this turn. Look it up with gh or git "
             "(for example gh pr view, gh issue list, git log), then restate it or drop it: ")

# A GitHub fact: a sentence that gives a PR, issue or card with a number (REF) a state (STATE). The
# hook checks only that some lookup (LOOKUP) ran in the turn, not which one, and not whether the fact
# is true.
REF = re.compile("|".join([
    r"\bPRs?\s*#?\d+",  # PR #44, PRs #44, PR 44
    r"(?i:\b(?:pull requests?|issues?|cards?)\s*#?\d+)",  # pull request 12, issue #70, card #3
    r"[\w.-]*#\d+\b",  # #70, apb#75
    r"/(?:pull|issues)/\d+",  # a link to a PR or an issue
    # A count: 13 PRs, 5 cards, 4 open issues
    r"(?i:\b\d+\s+(?:(?:open|merged|closed)\s+)?(?:PRs?|pull requests?|issues?|cards?)\b)",
]))
STATE = re.compile(r"(?i:\b(?:merged|closed|reopened)\b"  # PR #44 merged, you merged PR #44
                   r"|\b(?:is|are|was|were|now|still|stays|remains)\s+(?:(?:now|still|already)\s+)?"
                   r"(?:open|done|in\s+(?:\w+\s+)?review|on\s+the\s+board"
                   r"|in\s+(?:the\s+)?[\w ]{1,30}?\s+column)\b"
                   r"|\bopen\s+(?:PRs?|pull requests?|issues?|cards?)\b)")  # the open PR #44
SENTENCE_END = re.compile(r"(?<=[.!?;])\s+")
# A plan: from "after", "once", "when", "if", "until" or "before" to the next comma. A state in it
# ("once PR #80 is merged, I'll rebase") says nothing about now.
PLAN = re.compile(r"(?i:\b(?:after|once|when|if|until|before)\b[^,;]*)")
MAX_FACTS = 5  # facts named in the note and the log
# Quoted words: in quotation marks or backticks, or in single quotes that aren't an apostrophe.
QUOTE = re.compile(r"\"[^\"\n]*\"|“[^”\n]*”|`[^`\n]*`|(?<!\w)'[^'\n]*'(?!\w)")
# A command that looks GitHub up: gh, git, or apb's map or progress-page script, which call gh
# themselves. First in the command, or after a separator or a timeout.
LOOKUP = re.compile(r"(?:^|[;&|(])\s*(?:timeout\s+\S+\s+)?"
                    r"(?:gh|git|(?:(?:\S*/)?python3?\s+)?(?:\S*/)?(?:map-artifact|progress-page)\.py)(?:\s|$)")

NO_LINK = ("A decision was asked without a GitHub link. Put it on the PR or the card, then hand off "
           "with the link.")
# A line that names a decision. It asks one when it is the human's thing to do: the "1 thing" or
# "N things" line, or an item under "N things".
DECIDE = re.compile(r"(?i:\b(?:decide|decisions?|your call|choose|pick(?!\s+(?:\w+\s+)?up\b))\b)")
# "No decisions", "0 decisions", "zero decisions": says there is nothing to decide.
NO_DECISION = re.compile(r"(?i:\b(?:no|0|zero)\s+decisions?\b)")
THINGS_LINE = re.compile(r"(?i:\b(?:1 thing|\d+ things) for you to do right now\b)")
# Words that ask the human for a decision on any line.
ASK = re.compile("|".join([
    r"\banswer\s+\[?decisions?\s+\d+",  # answer decision 3, answer [decision 3 on PR #69](...)
    r"\b(?:\d+|a|one|two|three|four|five)\s+decisions?\s+for\s+you\b",  # 2 decisions for you
    r"\bwhich(?:\s+\w+)?\s+(?:do|would|will)\s+you\s+(?:want|prefer|like|pick|choose)\b",  # which do you want
    r"\byour\s+call\b",
]), re.I)
# A question that names a decision: "decide …?", "should we pick the grid?"
QUESTION = re.compile(r"[^.!?]*\?")
ASK_WORD = re.compile(r"(?i:\b(?:decide|decisions?|choose|pick(?!\s+(?:\w+\s+)?up\b))\b)")
# An option in a lettered list, for the human to pick from: "(a) …", "a) …", "- **(b)** …".
OPTION = re.compile(r"[-*+\s]*\(?([a-z])\)\s*\S")
# A link to a PR or an issue, on a line that isn't quoted.
LINK = re.compile(r"github\.com/[\w.-]+/[\w.-]+/(?:pull|issues)/\d+")
QUOTED = re.compile(r"\s*>")

PROJECTS = ("An apb PR in this hand-off changes how projects run (it touches templates/, hooks/, "
            "agents/, a skill's SKILL.md or docs/playbook.md), and its description has no \"Projects:\" line: %s. "
            "Apply the change to every project in use, then add one line to the PR's description: "
            "\"Projects: <links to those projects' PRs>\", or \"Projects: none\" when no project needs a "
            "change. Then hand off.")
# A path in the apb repo whose change reaches projects: a template, a hook, an agent's file, a skill's
# instructions or the playbook.
CHANGES_PROJECTS = re.compile(r"(?:templates|hooks|agents)/(?:[^/]+/)*[^/]+|skills/[^/]+/SKILL\.md|docs/playbook\.md")
# "Projects: none" (a reason may follow), or "Projects:" with at least one link to a PR.
PROJECTS_LINE = re.compile(r"Projects:\s*(.*)")
PROJECTS_NONE = re.compile(r"(?i:none)\b")
PR_LINK = re.compile(r"github\.com/[\w.-]+/[\w.-]+/pull/\d+|\b[\w.-]+/[\w.-]+#\d+")
MAX_PRS = 3  # apb PRs looked up in one stop
PR_TIME_LIMIT = 4  # seconds for one gh call
PR_BUDGET = 6  # seconds for all the stop's gh calls, both checks together; the Stop hook has 10 in all

DONE = ("A hand-off asks the human for something that is already done: %s. Look it up again, then hand "
        "off only what is left.")
# What a hand-off line asks the human to act on: a decision thread it links, or, when it says "merge",
# the PRs it links, else the PRs it names ("merge PR #44", in the project's own repo).
THREAD_LINK = re.compile(r"(?<![\w.-])github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)(?:/files)?#discussion_r(\d+)")
PULL_LINK = re.compile(r"(?<![\w.-])github\.com/([\w.-]+/[\w.-]+)/pull/(\d+)")
BARE_PR = re.compile(r"\bPR\s*#(\d+)\b")
MERGE = re.compile(r"(?i:\bmerge\b)")
MAX_DONE = 3  # threads and PRs looked up in one stop for this check
THREADS_QUERY = ("query($owner: String!, $name: String!, $number: Int!) { repository(owner: $owner, name: $name) "
                 "{ pullRequest(number: $number) { reviewThreads(first: 100) { nodes { isResolved "
                 "comments(first: 100) { nodes { databaseId } } } } } } }")

# The ETA is a clock time: 7:10 PM, 7:10pm, 7:10 p.m. or 19:10.
NOTHING = re.compile(r"Nothing for you to do right now\. Waiting on .+, ETA \d{1,2}:\d{2}"
                     r"(?:\s*[AaPp]\.?[Mm]\.?)?\.?$")
ONE = re.compile(r"1 thing for you to do right now: (.+?)\.?$")
MANY = re.compile(r"(\d+) things for you to do right now:$")
ALL_CLEAR = re.compile(r"All clear: .+ empty, no open PRs, as of .+")
CLEAR = "all clear"  # the todo an All clear line logs; it starts no reminder
ITEM = re.compile(r"(\d+)[.)]\s+\S")


def clean(line):
    """A line without surrounding space or Markdown emphasis."""
    return line.strip().strip("*_").strip()


def first_line(message):
    """(holder, todo) if the message starts with one of the four lines, else None.
    holder is "claude" or "human"; todo is what the human has to do, for the reminder, or CLEAR."""
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
    if ALL_CLEAR.fullmatch(head):
        return ("human", CLEAR)
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


def unquoted(message):
    """The message's lines that aren't quoted (">"), each without the words it quotes."""
    if not isinstance(message, str):
        return []
    return [QUOTE.sub(" ", l) for l in message.splitlines() if not QUOTED.match(l)]


def asks_decision(message):
    """True if the message asks the human for a decision: a "1 thing" or "N things" line that names
    one, words that ask for one ("answer decision 3", "2 decisions for you", "which do you want",
    "your call"), a question that names one ("decide …?"), or a list of lettered options. A line that
    only mentions a decision, quotes one or talks about the check asks nothing, and "Nothing for you to
    do right now" asks nothing, whatever follows it."""
    lines = [clean(l) for l in unquoted(message)]
    lines = [NO_DECISION.sub(" ", l) for l in lines if l]
    if not lines or NOTHING.match(lines[0]) or lines[0].startswith("Nothing for you to do right now"):
        return False
    letters = set()
    for line in lines:
        if THINGS_LINE.search(line) and DECIDE.search(line):
            return True
        if ASK.search(line) or any(ASK_WORD.search(q) for q in QUESTION.findall(line)):
            return True
        option = OPTION.match(re.sub(r"[*_]", "", line))
        if option:
            letters.add(option.group(1))
    return {"a", "b"} <= letters


def decides(line):
    """True if the line names a decision, outside quoted words. "No decisions" names none."""
    return bool(DECIDE.search(NO_DECISION.sub(" ", QUOTE.sub(" ", line))))


def has_link(message):
    """True if the message links a GitHub PR or issue on a line that isn't quoted."""
    if not isinstance(message, str):
        return False
    return any(LINK.search(l) for l in message.splitlines() if not QUOTED.match(l))


def items(message):
    """The numbered lines under an "N things for you to do right now:" first line, else []."""
    if not isinstance(message, str):
        return []
    lines = [clean(l) for l in message.splitlines()]
    while lines and not lines[0]:
        lines.pop(0)
    if not lines or not MANY.match(lines[0]):
        return []
    found = []
    for line in lines[1:]:
        if not line and not found:
            continue
        if not ITEM.match(line):
            break
        found.append(line)
    return found


def decision_without_link(message):
    """True if a decision is asked with no GitHub link: in the first line, with no link anywhere
    outside a quoted line, or in a numbered item under "N things", with no link on that item's line."""
    if asks_decision(message) and not has_link(message):
        return True
    return any(decides(item) and not LINK.search(item) for item in items(message))


def apb_repo():
    """The apb plugin's own GitHub repo, owner/name, from the repository in its manifest, or None."""
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".claude-plugin",
                        "plugin.json")
    try:
        with open(path, encoding="utf-8") as f:
            url = json.load(f).get("repository")
    except (OSError, ValueError, AttributeError):
        return None
    m = re.fullmatch(r"https://github\.com/([\w.-]+/[\w.-]+?)(?:\.git)?/?", url) if isinstance(url, str) else None
    return m.group(1) if m else None


def apb_prs(message, repo):
    """The numbers of the repo's PRs the message links outside a quoted line, once each, in order."""
    if not isinstance(message, str) or not isinstance(repo, str):
        return []
    link = re.compile(r"(?<![\w.-])github\.com/%s/pull/(\d+)" % re.escape(repo))
    numbers = []
    for line in message.splitlines():
        if QUOTED.match(line):
            continue
        for m in link.finditer(line):
            n = int(m.group(1))
            if n not in numbers:
                numbers.append(n)
    return numbers


def changes_projects(path):
    """True if a change to this path in the apb repo reaches the projects that use apb."""
    return isinstance(path, str) and bool(CHANGES_PROJECTS.fullmatch(path))


def has_projects_line(body):
    """True if the PR description has a "Projects:" line, outside a quote, that says "none" or links a PR."""
    if not isinstance(body, str):
        return False
    for line in body.splitlines():
        # Markdown around the line doesn't matter: bold, code, a list item or a heading. A quoted line
        # starts with ">", so it never matches.
        m = PROJECTS_LINE.fullmatch(re.sub(r"[*`]", "", line).strip().lstrip("#-+ ").strip())
        if m and (PROJECTS_NONE.match(m.group(1)) or PR_LINK.search(m.group(1))):
            return True
    return False


def view_pr(repo, number, time_limit=PR_TIME_LIMIT, cwd=None):
    """The PR's state, description and files, from gh. With no repo, gh uses the repo of the folder cwd."""
    out = subprocess.run(["gh", "pr", "view", str(number)] + (["--repo", repo] if repo else [])
                         + ["--json", "state,body,files"],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True,
                         timeout=time_limit, cwd=cwd).stdout
    return json.loads(out)


def view_threads(repo, number, time_limit=PR_TIME_LIMIT):
    """{comment id: whether its thread is resolved}, for each comment in the PR's review threads, from gh."""
    owner, name = repo.split("/", 1)
    out = subprocess.run(["gh", "api", "graphql", "-F", "number=%d" % number, "-f", "owner=" + owner,
                          "-f", "name=" + name, "-f", "query=" + THREADS_QUERY],
                         stdin=subprocess.DEVNULL, capture_output=True, text=True, check=True,
                         timeout=time_limit).stdout
    try:
        nodes = json.loads(out)["data"]["repository"]["pullRequest"]["reviewThreads"]["nodes"]
        return {c["databaseId"]: t["isResolved"] is True for t in nodes for c in t["comments"]["nodes"]}
    except (KeyError, TypeError) as e:
        raise ValueError("unexpected answer: %s" % e)


class OutOfTime(subprocess.SubprocessError):
    """The stop's time for gh calls is used up."""


class GitHub:
    """The gh lookups of one stop: one time budget (PR_BUDGET) for all of them, each PR and each PR's
    threads looked up once, a failure remembered like an answer. pr_lookup(repo, number) and
    thread_lookup(repo, number) stand in for gh in tests. project is the folder whose repo gh uses for
    a PR named with no repo."""

    def __init__(self, project=None, pr_lookup=None, thread_lookup=None):
        self.project, self.pr_lookup, self.thread_lookup = project, pr_lookup, thread_lookup
        self.started = time.time()
        self.answers = {}

    def left(self):
        return PR_BUDGET - (time.time() - self.started)

    def pr(self, repo, number):
        return self.ask(("pr", repo, number), self.pr_lookup,
                        lambda limit: view_pr(repo, number, limit) if repo
                        else view_pr(repo, number, limit, cwd=self.project))

    def threads(self, repo, number):
        return self.ask(("threads", repo, number), self.thread_lookup,
                        lambda limit: view_threads(repo, number, limit))

    def ask(self, key, fake, real):
        if key not in self.answers:
            try:
                if fake:
                    value = fake(key[1], key[2])
                else:
                    left = self.left()
                    if left <= 0:
                        raise OutOfTime()
                    value = real(min(PR_TIME_LIMIT, left))
                self.answers[key] = (True, value)
            except (OSError, ValueError, subprocess.SubprocessError) as e:
                self.answers[key] = (False, e)
        ok, value = self.answers[key]
        if not ok:
            raise value
        return value


def hand_off_lines(message):
    """What the hand-off asks the human to do: the "1 thing" first line, or each numbered item under an
    "N things" first line. Any other message hands off nothing."""
    said = first_line(message)
    if not said or said[0] != "human" or said[1] == CLEAR:
        return []
    return items(message) or [clean(l) for l in message.splitlines() if l.strip()][:1]


def targets(message):
    """What the hand-off asks the human to act on, outside quoted words, once each, in order:
    ("thread", repo, PR, comment id) for each decision thread a hand-off line links, and, on a line that
    says "merge", ("pr", repo, number) for each PR it links (a thread link isn't one), or, if it links
    none, ("pr", None, number) for each PR it names ("merge PR #44"; the project's own repo)."""
    found = []
    for line in hand_off_lines(message):
        text = QUOTE.sub(" ", line)
        here = [("thread", m.group(1), int(m.group(2)), int(m.group(3))) for m in THREAD_LINK.finditer(text)]
        if MERGE.search(text):
            rest = THREAD_LINK.sub(" ", text)  # a thread link names a decision, not a PR to merge
            here += ([("pr", m.group(1), int(m.group(2))) for m in PULL_LINK.finditer(rest)]
                     or [("pr", None, int(m.group(1))) for m in BARE_PR.finditer(rest)])
        for t in here:
            if t not in found:
                found.append(t)
    return found


def already_done(message, github):
    """What the hand-off asks the human to do that is already done: a decision thread that is resolved,
    a PR to merge that is merged or closed. At most MAX_DONE lookups; one that fails or runs out of time
    is let through."""
    done = []
    for t in targets(message)[:MAX_DONE]:
        if github.left() <= 0:
            break
        try:
            if t[0] == "thread":
                threads = github.threads(t[1], t[2])
                if isinstance(threads, dict) and threads.get(t[3]) is True:
                    done.append("the decision thread https://github.com/%s/pull/%d#discussion_r%d is resolved"
                                % t[1:])
                continue
            pr = github.pr(t[1], t[2])
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        state = pr.get("state") if isinstance(pr, dict) else None
        if state in ("MERGED", "CLOSED"):
            done.append("PR #%d is %s" % (t[2], state.lower())
                        + (" (https://github.com/%s/pull/%d)" % (t[1], t[2]) if t[1] else ""))
    return done


def projects_line_missing(message, lookup=None, github=None):
    """The open apb PRs the message links that change how projects run and whose description has no
    Projects line. A PR that can't be looked up in time is let through."""
    github = github or GitHub(pr_lookup=lookup)
    repo = apb_repo()
    missing = []
    for number in apb_prs(message, repo)[:MAX_PRS]:
        if github.left() <= 0:
            break
        try:
            pr = github.pr(repo, number)
        except (OSError, ValueError, subprocess.SubprocessError):
            continue
        if not isinstance(pr, dict) or pr.get("state") != "OPEN":
            continue
        files = pr.get("files") if isinstance(pr.get("files"), list) else []
        if (any(changes_projects(f.get("path")) for f in files if isinstance(f, dict))
                and not has_projects_line(pr.get("body"))):
            missing.append(number)
    return missing


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
        if e.get("session") == session and e["event"] == "Stop" and not e.get("sent_back"):
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


def quiet_wake_up(events, session, message):
    """True if a wake-up that changed nothing for the human ends quietly: no prompt and no agent's
    stop in this turn (a watcher or a background command woke it), an earlier stop in this session
    already said who holds the ball, and the message is at most one word. The ball stays where it was."""
    if message is not None and not isinstance(message, str):
        return False
    if len((message or "").split()) > 1:
        return False
    for e in this_turn(events, session):
        if e["event"] == "UserPromptSubmit":
            return False
        # An agent's report. Claude Code's own helper also logs a SubagentStop, with agent_type ""
        # and no SubagentStart, just after each stop; it is no report.
        if e["event"] == "SubagentStop" and e.get("agent_type") != "":
            return False
    return holder_now(events, session) != (None, None)


def stated_facts(message):
    """The PRs, issues and cards (by number, or a count such as "13 PRs") the message gives a state,
    outside quotes, each once, in order: at most MAX_FACTS. A number with no state ("the builder opened
    PR #57") states nothing to check, nor does the word "board" or "column" with no number, nor a state
    in a plan ("once PR #80 is merged, I'll rebase"). Bold or italics around a state don't hide it."""
    facts = []
    for line in unquoted(message):
        for sentence in SENTENCE_END.split(re.sub(r"[*_]", "", line)):  # bold or italics don't matter
            sentence = PLAN.sub(" ", sentence)
            if not STATE.search(sentence):
                continue
            for m in REF.finditer(sentence):
                fact = m.group(0).strip()
                if fact not in facts:
                    facts.append(fact)
    return facts[:MAX_FACTS]


def checked_this_turn(events, session):
    """True if the orchestrator itself (not an agent) ran a lookup that finished in this turn."""
    return any(e["event"] == "PostToolUse" and "agent_id" not in e and isinstance(e.get("command"), str)
               and LOOKUP.search(e["command"]) for e in this_turn(events, session))


def departures(events):
    """Departure lines (hooks/apb-brief.py writes them, from any session) after the last DepartureResolved."""
    pending = []
    for e in events:
        if e["event"] == "Departure":
            pending.append(e)
        elif e["event"] == "DepartureResolved":
            pending = []
    return pending


def departure_send_backs(events, session):
    return sum(1 for e in this_turn(events, session) if e["event"] == "Stop" and e.get("departure"))


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


def handle(p, project, now, pr_lookup=None, thread_lookup=None):
    """Decide on one stop and log it. pr_lookup(repo, number) and thread_lookup(repo, number) stand in
    for gh in tests.
    Returns {"send_back": bool, "remind": what or None, "end": the events file's size after the line}."""
    session = p.get("session_id")
    if not isinstance(session, str):
        return {"send_back": False, "remind": None, "end": 0}
    events = read_events(project)
    message = last_message(p)
    said = first_line(message)
    retry = bool(p.get("stop_hook_active"))
    no_line = (said is None and not retry
               and work_ran_out(events, session, now, p.get("background_tasks"))
               and not quiet_wake_up(events, session, message))
    unchecked = [] if retry or checked_this_turn(events, session) else stated_facts(message)
    # A hand-off to the human waits while an agent's departure is unresolved, on a retry too.
    pending = departures(events) if said and said[0] == "human" else []
    departed = bool(pending) and departure_send_backs(events, session) < MAX_DEPARTURE_SEND_BACKS
    unlinked = not retry and decision_without_link(message)
    hand_off = not retry and bool(said) and said[0] == "human"
    github = GitHub(project, pr_lookup, thread_lookup)
    # A hand-off never asks for something already done: a resolved thread, a merged PR.
    done = already_done(message, github) if hand_off else []
    # A hand-off of an apb PR that changes how projects run needs its "Projects:" line.
    no_projects = projects_line_missing(message, github=github) if hand_off else []
    send_back = no_line or bool(unchecked) or departed or unlinked or bool(done) or bool(no_projects)
    line = {"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + ".%03dZ" % (now % 1 * 1000),
            "event": "Stop", "session": session[:MAX]}
    if send_back:
        line["sent_back"] = True
        if unchecked:
            line["unchecked"] = [f[:MAX] for f in unchecked]
        if departed:
            line["departure"] = True
        if unlinked:
            line["decision-without-link"] = True
        if done:
            line["already-done"] = [d[:MAX] for d in done]
        if no_projects:
            line["projects-line-missing"] = no_projects
    elif said:
        line["holder"] = said[0]
        if said[1]:
            line["todo"] = said[1][:MAX]
    end = append(project, line)
    if send_back:
        notes = ([REASON] if no_line else []) + ([UNCHECKED + ", ".join(unchecked) + "."] if unchecked else [])
        if departed:
            quotes = "; ".join('%s: "%s"' % (e.get("agent_type", "agent"), e.get("quote", "")) for e in pending)
            notes.append(DEPARTED % quotes)
        if unlinked:
            notes.append(NO_LINK)
        if done:
            notes.append(DONE % "; ".join(done))
        if no_projects:
            links = ", ".join("https://github.com/%s/pull/%d" % (apb_repo(), n) for n in no_projects)
            notes.append(PROJECTS % links)
        return {"send_back": True, "remind": None, "end": end, "note": "\n\n".join(notes)}
    if said:
        holder, todo = said
    elif any(e["event"] == "UserPromptSubmit" for e in this_turn(events, session)):
        holder, todo = holder_now(events, session)  # the prompt ended the last wait; start a new one
    else:
        holder, todo = None, None  # woken with no new line: the running wait carries on
    # All clear got its one alert from the orchestrator; nothing more until the human writes.
    return {"send_back": False, "remind": todo if holder == "human" and todo != CLEAR else None, "end": end}


def remind_after():
    """Seconds to wait before the reminder; tests shorten it with APB_REMIND_AFTER_SECONDS."""
    return float(os.environ.get("APB_REMIND_AFTER_SECONDS", REMIND_AFTER))


def start_reminder(project, session, todo, start):
    """Start the reminder in the background. It is the one process apb lets outlive the script that
    starts it, so it gets a deadline: the wait plus LIFETIME_MARGIN. It ends there, whatever it is doing."""
    deadline = time.time() + remind_after() + LIFETIME_MARGIN
    subprocess.Popen([sys.executable, os.path.abspath(__file__), "remind", project, session, todo, str(start),
                      "%.3f" % deadline],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True, close_fds=True)


class Deadline(BaseException):
    """Raised by the alarm at the reminder's deadline. subprocess.run kills its child when it sees it."""


def stop_at(deadline):
    """End this process at deadline (seconds since the epoch). Returns False if it has already passed."""
    left = deadline - time.time()
    if left <= 0:
        return False

    def ring(signum, frame):
        raise Deadline()
    signal.signal(signal.SIGALRM, ring)
    signal.setitimer(signal.ITIMER_REAL, left)
    return True


def human_acted(project, session, start):
    """True if, after byte start, this session has a prompt or a stop with a new first line.
    A stop with no first line (Claude woken by a watcher or an agent) leaves the wait running."""
    return any(e.get("session") == session
               and (e["event"] == "UserPromptSubmit" or (e["event"] == "Stop" and e.get("holder")))
               for e in read_events(project, start))


def remind(project, session, todo, start, deadline):
    """Wait REMIND_AFTER seconds; if the human did nothing in that time, alert once.
    Ends at deadline, wherever it has got to."""
    if not stop_at(deadline):
        return
    end = time.time() + remind_after()
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
    if len(sys.argv) == 7 and sys.argv[1] == "remind":
        try:
            remind(sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5]), float(sys.argv[6]))
        except Deadline:
            pass
        return
    p = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    if not isinstance(p, dict):
        return
    project = os.environ.get("CLAUDE_PROJECT_DIR") or p.get("cwd")
    if not isinstance(project, str) or not os.path.isfile(os.path.join(project, "docs", "MAP.md")):
        return
    result = handle(p, project, time.time())
    if result["send_back"]:
        # A quiet note: Claude goes on with it, shown as "Stop hook feedback", not as an error.
        sys.stdout.write(json.dumps({"hookSpecificOutput": {"hookEventName": "Stop",
                                                            "additionalContext": result["note"]}}))
        return
    if result["remind"]:
        start_reminder(project, p["session_id"], result["remind"], result["end"])


if __name__ == "__main__":
    try:
        main()
    except BaseException:
        pass
    sys.exit(0)
