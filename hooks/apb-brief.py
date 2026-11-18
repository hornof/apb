"""Brief check: a brief gives an agent the task only; the agent's own file says how to post its work.

Claude Code runs this from hooks/hooks.json, with the event's JSON on stdin:
- PreToolUse on the Agent tool: when the orchestrator sends a builder or reviewer a brief that tells it
  how to format or post its work ("as a single comment", "post as", "use one thread", or "format" next
  to review, comment, report or PR; in a reviewer's brief also "leave out items" and "no index"), the
  call is refused, with a reason that names what was
  found and says to send the task only. The format lives in agents/builder.md and agents/reviewer.md.
- SubagentStop: when an agent's report says it "departed from" its file, or did something "as you
  asked, instead of" what its file says, a Departure line goes into .apb/events.jsonl (apb projects
  only, the ones with docs/MAP.md). hooks/apb-stop.py then sends back any hand-off to the human until
  the orchestrator has put it right and run:

    python3 <apb plugin folder>/hooks/apb-brief.py resolve "<what you did>"

  from the project's root. That writes a DepartureResolved line, which clears every departure before it.

As a hook it never fails Claude: any error lets the call or the stop through.
"""
import json
import os
import re
import sys
import time

MAX = 300  # longest quote kept, in characters

# Who gets checked at dispatch: the agents whose files set a format.
CHECKED = ("builder", "reviewer")

# Nouns for what an agent posts or reports. "format" next to one of them, in the same sentence, is
# about the agent's output, not about something it builds (a date format, an image format). Words that
# are also everyday words in an app (post, summary, description, items, index, reply) are left out;
# "PR" still covers a PR description.
OUTPUT = r"(?:review|comment|report|PR|pull request|thread|hand-?off|findings|bucket)s?"
OBJECT = (r"(?:it|them|this|everything|all of it|the review|your review|the findings|the items|the notes|"
          r"the decisions|the fixes|the report|your report|each \w+|every \w+)")
WHAT = r"(?:items?|notes?|nits?|decisions?|fix(?:es)?|index|buckets?|sections?|findings?)"

# Phrases that tell the agent how to format or post its work, anywhere in the brief.
PHRASES = [re.compile(p, re.I) for p in [
    r"\b(?:as|in|into) (?:a |one )?single (?:\w+ )?(?:comment|post|thread|review|message)s?\b",
    r"\b(?:as|in|into) (?:just |only )?one (?:\w+ )?(?:comment|post|thread|message)\b",
    r"\bpost (?:" + OBJECT + r" )?as\b",
    r"\b(?:this|the following|following|same|that|usual|this exact) format\b",
    r"\bone (?:review )?thread (?:for (?:everything|all|each|every|the whole)|"
    r"per (?:file|item|decision|fix|note|bucket|section|finding|comment|point)s?\b)",
    r"\b(?:no|instead of|without) (?:review )?threads\b",
    r"\b(?:use|post|write|leave|put|open|start) (?:just |only )?one (?:review )?(?:comment|thread|post)\b",
    r"\b(?:don't|do not|never) (?:use|post|open|start|write) (?:any )?(?:review )?threads\b",
]]
# Only in a reviewer's brief: in a builder's brief these are ordinary tasks ("drop items with no image",
# "skip the index page").
REVIEWER_PHRASES = [re.compile(p, re.I) for p in [
    r"\b(?:leave out|skip|drop|omit) (?:the |any |some |all )?(?:low[- ]\w+ )?" + WHAT + r"\b",
    r"\b(?:no|without(?: an?| the)?|skip the) index\b",
]]
FORMAT_WORD = re.compile(r"\bformat(?:s|ted|ting)?\b", re.I)
OUTPUT_WORD = re.compile(r"\b" + OUTPUT + r"\b", re.I)
SENTENCE = re.compile(r"[.!?;]+(?=\s|$)|\n\s*\n|\n\s*(?=[-*+]|\d+[.)])")


def plain(text):
    """The text without Markdown emphasis and code marks."""
    return re.sub(r"[*_`]", "", text or "")


def sentences(text):
    return [s.strip() for s in SENTENCE.split(plain(text)) if s.strip()]


def format_instructions(text, agent="reviewer"):
    """What in the brief tells the agent how to format or post its work, each once, in order.
    agent: "builder" or "reviewer" (any "plugin:" prefix is ignored); a reviewer's brief is checked
    with REVIEWER_PHRASES too."""
    if not isinstance(text, str):
        return []
    phrases = PHRASES + (REVIEWER_PHRASES if str(agent).split(":")[-1] == "reviewer" else [])
    found = []
    for s in sentences(text):
        flat = " ".join(s.split())
        hits = [m.group(0) for p in phrases for m in p.finditer(flat)]
        if FORMAT_WORD.search(flat) and OUTPUT_WORD.search(flat) and not hits:
            hits = [flat[:MAX]]
        for h in hits:
            if h.lower() not in [f.lower() for f in found]:
                found.append(h)
    return found


DEPARTED = re.compile(r"\bdeparted from\b", re.I)
AS_ASKED = re.compile(r"\bas you asked,?\s+instead of\b", re.I)
NEGATION = re.compile(r"\b(?:not|never|nothing|no|none|\w+n't)\b", re.I)
REPORT_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n\s*\n")


def departure(text):
    """The sentence where the report says it departed from its file, or None."""
    if not isinstance(text, str):
        return None
    for s in REPORT_SENTENCE.split(plain(text)):
        flat = " ".join(s.split())
        if AS_ASKED.search(flat):
            return flat[:MAX]
        m = DEPARTED.search(flat)
        clause = re.split(r"[,;:]", flat[:m.start()])[-1] if m else ""
        if m and not NEGATION.search(" ".join(clause.split()[-4:])):
            return flat[:MAX]
    return None


def refusal(agent, found):
    name = agent.split(":")[-1]
    return ("Brief refused: it tells the %s how to format or post its work (%s). The format is in "
            "agents/%s.md, and a brief never changes it. Send the task only: the issue or PR, the "
            "worktree or branch, the scratch folder, the command rule, and what to report back. Then "
            "launch the agent again." % (name, "; ".join('"%s"' % f for f in found), name))


def checked(agent):
    return isinstance(agent, str) and agent.split(":")[-1] in CHECKED


def on_dispatch(p):
    """The hook's output for a PreToolUse event, or None to let the call through."""
    tool_input = p.get("tool_input")
    if p.get("tool_name") not in ("Agent", "Task") or not isinstance(tool_input, dict):
        return None
    agent = tool_input.get("subagent_type")
    if not checked(agent):
        return None
    found = format_instructions(tool_input.get("prompt"), agent)
    if not found:
        return None
    return {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny",
                                   "permissionDecisionReason": refusal(agent, found)}}


def apb_project(p=None):
    """The apb project's folder, or None: CLAUDE_PROJECT_DIR, else the event's cwd, else this folder."""
    project = os.environ.get("CLAUDE_PROJECT_DIR") or (p or {}).get("cwd") or (None if p else os.getcwd())
    if isinstance(project, str) and os.path.isfile(os.path.join(project, "docs", "MAP.md")):
        return project
    return None


def append(project, line):
    folder = os.path.join(project, ".apb")
    os.makedirs(folder, exist_ok=True)
    ignore = os.path.join(folder, ".gitignore")
    if not os.path.exists(ignore):
        with open(ignore, "w") as f:
            f.write("*\n")
    now = time.time()
    line = dict({"time": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(now)) + ".%03dZ" % (now % 1 * 1000)},
                **line)
    with open(os.path.join(folder, "events.jsonl"), "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")


def on_agent_stop(p):
    quote = departure(p.get("last_assistant_message"))
    project = apb_project(p)
    if not quote or not project:
        return
    line = {"event": "Departure", "session": p.get("session_id"), "quote": quote}
    for key in ("agent_id", "agent_type"):
        if isinstance(p.get(key), str):
            line[key] = p[key][:MAX]
    append(project, line)


def resolve(args):
    """The resolve command. Returns the exit code."""
    note = " ".join(args).strip()
    if not note:
        print('Usage: python3 apb-brief.py resolve "<what you did>"', file=sys.stderr)
        return 2
    project = apb_project()
    if not project:
        print("Not an apb project (no docs/MAP.md here). Run it from the project's root.", file=sys.stderr)
        return 2
    path = os.path.join(project, ".apb", "events.jsonl")
    pending = 0
    if os.path.exists(path):
        with open(path, encoding="utf-8", errors="replace") as f:
            for raw in f:
                try:
                    event = json.loads(raw).get("event")
                except (ValueError, AttributeError):
                    continue
                pending = pending + 1 if event == "Departure" else 0 if event == "DepartureResolved" else pending
    append(project, {"event": "DepartureResolved", "note": note[:MAX]})
    print("Resolved %d departure%s." % (pending, "" if pending == 1 else "s"))
    return 0


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "resolve":
        return resolve(sys.argv[2:])
    try:
        p = json.loads(sys.stdin.buffer.read().decode("utf-8"))
        if not isinstance(p, dict):
            return 0
        event = p.get("hook_event_name")
        if event == "PreToolUse":
            out = on_dispatch(p)
            if out:
                sys.stdout.write(json.dumps(out))
        elif event == "SubagentStop":
            on_agent_stop(p)
    except BaseException:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
