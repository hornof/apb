"""Builds the progress page: one page that shows what's done, what's running and what's left.

Usage, from the project's repo root:
    python3 <apb plugin folder>/scripts/progress-page.py <output.html> [--repo owner/name]
        [--events .apb/events.jsonl] [--map docs/MAP.md]
Then publish <output.html> to the progress page's artifact link. Never edit the page by hand.

It reads, and never changes:
- docs/MAP.md: the stages, from its "Stages, in order" line;
- GitHub (needs the gh CLI, signed in): the repo's board, its open PRs with their checks and reviews,
  and the build count, worked out the same way as in the map artifact;
- .apb/events.jsonl, which the apb hooks write: the agents running now and the ones that just
  finished, and who holds the ball.

--repo is the repo whose board and PRs to show; by default, this folder's repo. Use it when the
project folder and the work are in different repos.
The page is a snapshot: it shows the time it was built, and how long ago that was.
"""
import argparse
import html
import importlib.util
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, file))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


mp = load("map_artifact", "map-artifact.py")  # gh calls with time limits, the build count, the look
watch = load("apb_watch", "apb-watch.py")  # reading the events file
ABANDONED = watch.ABANDONED  # an agent silent this long is taken to be gone
REVIEWER = "Claude (reviewer)"  # how the reviewer signs every comment it posts
SHORT = 100  # longest command shown, in characters


# ---------------------------------------------------------------- the stages

def stages(map_text):
    """The stage names from the map's "**Stages, in order:**" line, or [] if it has none."""
    m = re.search(r"^\*\*Stages, in order:\*\*\s*(.+)$", map_text, re.M)
    return [s.strip() for s in m.group(1).split("→") if s.strip()] if m else []


# ---------------------------------------------------------------- the board

def board(project):
    """{"title", "url", "columns": [(name, [cards])]} from GitHub's project, or None.
    Columns and cards keep the board's own order; cards with no status go in a last column."""
    if not project:
        return None
    names = [o["name"] for o in ((project.get("field") or {}).get("options") or [])]
    cards = {n: [] for n in names}
    loose = []
    for item in project["items"]["nodes"]:
        content = item.get("content") or {}
        if "number" not in content:
            continue  # a draft note on the board, not an issue or PR
        status = (item.get("fieldValueByName") or {}).get("name")
        card = {"number": content["number"], "title": content.get("title", ""), "url": content.get("url", "")}
        if status in cards:
            cards[status].append(card)
        else:
            loose.append(card)
    columns = [(n, cards[n]) for n in names]
    if loose:
        columns.append(("No status", loose))
    return {"title": project.get("title", ""), "url": project.get("url", ""), "columns": columns}


def done_count(b):
    """(cards in Done, all cards) on the board."""
    if not b:
        return 0, 0
    done = sum(len(cards) for name, cards in b["columns"] if name.lower() == "done")
    return done, sum(len(cards) for _, cards in b["columns"])


# ---------------------------------------------------------------- the events file

def describe(e):
    if e.get("command"):
        c = " ".join(str(e["command"]).split())
        return c if len(c) <= SHORT else c[:SHORT] + "…"
    return e.get("event", "?")


def agent_runs(events, now):
    """(running, finished), one row per agent. The orchestrator can resume an agent after it stops,
    so an agent's lines come in pieces, each ending with a stop line.
    running: agents whose last line is not a stop, heard from in the last ABANDONED seconds, oldest
    first. finished: agents whose last line is a stop, newest first.
    Each is {"id", "type", "start": its first line}. Running ones also have "piece" (when the current
    piece began), "last" and "what"; finished ones "stop" and "took", the time of all its pieces added up.
    A piece that is only a stop line is Claude Code's own short helper, not real work: it is left out."""
    agents = {}
    for e in events:
        a = e.get("agent_id")
        if not a:
            continue
        t = e["t"]
        g = agents.setdefault(a, {"id": a, "type": "", "start": None, "piece": None, "lines": 0,
                                  "took": 0, "last": t, "what": "", "stop": None})
        if e.get("agent_type"):
            g["type"] = e["agent_type"]
        if e.get("event") == "SubagentStop":
            if g["piece"] is not None:
                g["took"] += t - g["piece"]
                g["stop"], g["last"] = t, t
            g["piece"] = None
            continue
        if g["piece"] is None:
            g["piece"] = t
            if g["start"] is None:
                g["start"] = t
        g["last"], g["what"] = t, describe(e)
    running, finished = [], []
    for g in agents.values():
        if g["start"] is None:
            continue  # only lone stop lines
        kind = g["type"] or "agent"
        if g["piece"] is not None:
            if now - g["last"] <= ABANDONED:
                running.append({"id": g["id"], "type": kind, "start": g["start"], "piece": g["piece"],
                                "last": g["last"], "what": g["what"]})
        else:
            finished.append({"id": g["id"], "type": kind, "start": g["start"], "stop": g["stop"], "took": g["took"]})
    running.sort(key=lambda r: r["start"])
    finished.sort(key=lambda f: f["stop"], reverse=True)
    return running, finished


def ball(events, running=()):
    """Who holds the ball in the latest orchestrator session, from the Stop hook's lines.
    {"state": "human" | "claude" | "working" | None, "todo", "since"}.
    "working": Claude is on it now. Either a turn is under way (a prompt came after the last turn
    ended), or the human answered after the last "who holds the ball" line and an agent started or
    resumed since then is still running. Otherwise the last such line holds: a question answered in between
    doesn't change it."""
    nobody = {"state": None, "todo": None, "since": None}
    main = [e for e in events if not e.get("agent_id") and e.get("event") in ("UserPromptSubmit", "Stop")]
    if not main:
        return nobody
    session = main[-1].get("session")
    mine = [e for e in main if e.get("session") == session]
    if mine[-1]["event"] == "UserPromptSubmit":
        return {"state": "working", "todo": None, "since": mine[-1]["t"]}
    said = next((e for e in reversed(mine) if e["event"] == "Stop" and e.get("holder")), None)
    if said is None:
        return nobody
    prompts = [e["t"] for e in mine if e["event"] == "UserPromptSubmit" and e["t"] >= said["t"]]
    if prompts and any(r.get("piece", r["start"]) >= prompts[0] for r in running):
        return {"state": "working", "todo": None, "since": prompts[0]}
    return {"state": said["holder"], "todo": said.get("todo"), "since": said["t"]}


# ---------------------------------------------------------------- PRs

def checks(p):
    nodes = p["commits"]["nodes"]
    rollup = nodes[0]["commit"]["statusCheckRollup"] if nodes else None
    state = (rollup or {}).get("state")
    if not state:
        return "none"
    return {"SUCCESS": "passed", "FAILURE": "failed", "ERROR": "failed",
            "PENDING": "running", "EXPECTED": "running"}.get(state, state.lower())


def waiting_on(p):
    """(who, why) for an open PR: builder, CI, reviewer or you."""
    if p.get("isDraft"):
        return "builder", "draft"
    c = checks(p)
    if c == "running":
        return "CI", "checks running"
    if c == "failed":
        return "builder", "checks failed"
    bodies = [n["body"] for n in p["comments"]["nodes"]] + [n["body"] for n in p["reviews"]["nodes"]]
    threads = p["reviewThreads"]["nodes"]
    bodies += [c["body"] for t in threads for c in t["comments"]["nodes"]]
    if not any(REVIEWER in (b or "") for b in bodies):
        return "reviewer", "no review yet"
    # Fix items have no threads, so the page can't tell whether they are done: it never says "ready to merge".
    # Only Decisions get threads, so open threads are open decisions.
    open_items = sum(not t["isResolved"] for t in threads)
    if open_items:
        return "you", "review posted, %d decision%s open" % (open_items, "" if open_items == 1 else "s")
    return "you", "review posted"


QUERY = """query($owner: String!, $name: String!) { repository(owner: $owner, name: $name) {
  projectsV2(first: 1) { nodes { title url
    field(name: "Status") { ... on ProjectV2SingleSelectField { options { name } } }
    items(first: 100) { nodes {
      fieldValueByName(name: "Status") { ... on ProjectV2ItemFieldSingleSelectValue { name } }
      content { ... on Issue { number title url } ... on PullRequest { number title url } } } } } }
  pullRequests(first: 50, states: OPEN, orderBy: {field: CREATED_AT, direction: DESC}) { nodes {
    number title url isDraft
    commits(last: 1) { nodes { commit { statusCheckRollup { state } } } }
    comments(last: 100) { nodes { body } }
    reviews(last: 50) { nodes { body } }
    reviewThreads(first: 100) { nodes { isResolved comments(first: 20) { nodes { body } } } } } } } }"""


def github(repo):
    """(board, open PRs, build count) for repo, read from GitHub."""
    owner, name = repo.split("/", 1)
    got = json.loads(mp.run(["gh", "api", "graphql", "-f", "owner=" + owner, "-f", "name=" + name,
                             "-f", "query=" + QUERY]))["data"]["repository"]
    projects = got["projectsV2"]["nodes"]
    prs = []
    for p in got["pullRequests"]["nodes"]:
        who, why = waiting_on(p)
        prs.append({"number": p["number"], "title": p["title"], "url": p["url"], "checks": checks(p),
                    "who": who, "why": why})
    count = mp.build_count(mp.run(["gh", "issue", "list", "--repo", repo, "--label", mp.BUILD_LABEL,
                                   "--state", "all", "--limit", "1000", "--json", "state,stateReason"]))
    return board(projects[0] if projects else None), prs, count


# ---------------------------------------------------------------- the page

def span(seconds):
    m = int(seconds) // 60
    if m < 1:
        return "under 1 min"
    return "%d min" % m if m < 60 else "%d h %d min" % (m // 60, m % 60)


def clock(t, now):
    same_day = time.localtime(t)[:3] == time.localtime(now)[:3]
    return time.strftime("%H:%M" if same_day else "%b %d, %H:%M", time.localtime(t))


def esc(value):
    return html.escape(str(value), quote=True)


def link(url, text):
    return '<a href="%s" target="_blank" rel="noopener">%s</a>' % (esc(url), esc(text))


def ball_html(b, now):
    if b["state"] == "human":
        return '<p class="ball you">Your turn: %s <span>since %s</span></p>' % (
            esc(b["todo"] or "see the last message"), clock(b["since"], now))
    if b["state"] == "claude":
        return '<p class="ball">Nothing for you to do right now. <span>since %s</span></p>' % clock(b["since"], now)
    if b["state"] == "working":
        return '<p class="ball">Claude is working. <span>since %s</span></p>' % clock(b["since"], now)
    return '<p class="ball">No turn has ended yet in the events file.</p>'


def stages_html(names, count, map_path=mp.MAP, found=True):
    """The stages, with the build count after the first word "build", the same rule as the map
    artifact's (map-artifact.py, add_build_count)."""
    if not found:
        return '<p class="muted">%s not found, so no stages.</p>' % esc(map_path)
    if not names:
        return '<p class="muted">No "Stages, in order" line in %s.</p>' % esc(map_path)
    done, total = count
    out, counted = [], False
    for s in names:
        label = esc(s)
        if total and not counted:
            label, n = re.subn(r"\b(build)\b", r"\1 (%d of %d cards done)" % (done, total), label, count=1, flags=re.I)
            counted = n == 1
        out.append('<li>%s</li>' % label)
    return '<ol class="stages">%s</ol>' % "".join(out)


def bar(done, total):
    pct = 100 * done // total if total else 0
    return ('<div class="bar" role="img" aria-label="%d of %d"><div style="width:%d%%"></div></div>'
            % (done, total, pct))


def agents_html(running, now):
    if not running:
        return '<p class="muted">No agents running, as far as the events file shows.</p>'
    rows = "".join(
        "<tr><td>%s</td><td>%s</td><td><code>%s</code></td><td>%s ago</td></tr>"
        % (esc(r["type"]), span(now - r["start"]), esc(r["what"]), span(now - r["last"])) for r in running)
    return ("<table><thead><tr><th>Agent</th><th>Running for</th><th>Last event</th><th>When</th></tr></thead>"
            "<tbody>%s</tbody></table>" % rows)


def prs_html(prs):
    if not prs:
        return '<p class="muted">No open PRs.</p>'
    rows = "".join(
        '<tr><td>%s</td><td>%s</td><td><span class="who who-%s">%s</span> %s</td></tr>'
        % (link(p["url"], "#%s %s" % (p["number"], p["title"])), esc(p["checks"]),
           esc(p["who"].lower()), esc(p["who"]), esc(p["why"])) for p in prs)
    return ("<table><thead><tr><th>PR</th><th>Checks</th><th>Waiting on</th></tr></thead>"
            "<tbody>%s</tbody></table>" % rows)


def board_html(b):
    if not b:
        return '<p class="muted">No board linked to this repo.</p>'
    cols = []
    for name, cards in b["columns"]:
        items = "".join("<li>%s</li>" % link(c["url"], "#%s %s" % (c["number"], c["title"])) for c in cards)
        body = "<ul>%s</ul>" % items if cards else '<p class="muted">Empty</p>'
        head = "%s <span>%d</span>" % (esc(name), len(cards))
        if name.lower() == "done" and cards:
            cols.append('<section class="col"><details><summary><h3>%s</h3></summary>%s</details></section>'
                        % (head, body))
        else:
            cols.append('<section class="col"><h3>%s</h3>%s</section>' % (head, body))
    return '<p>%s</p><div class="cols">%s</div>' % (link(b["url"], "Open the board on GitHub"), "".join(cols))


def finished_html(finished, now, limit=10):
    if not finished:
        return '<p class="muted">None yet.</p>'
    rows = "".join("<tr><td>%s</td><td>%s</td><td>took %s</td></tr>"
                   % (esc(f["type"]), clock(f["start"], now), span(f["took"]))
                   for f in finished[:limit])
    return ("<table><thead><tr><th>Agent</th><th>Started</th><th>Time</th></tr></thead>"
            "<tbody>%s</tbody></table>" % rows)


STYLE = re.search(r"<link[^>]*>\s*<style>.*?</style>", mp.PAGE, re.S).group(0).replace("{{", "{").replace("}}", "}")
EXTRA = """<style>
td:first-child { min-width: 0; }
.muted, .note { color: var(--muted); }
.ball { font-size: 18px; font-weight: 600; padding: 14px 16px; border: 1px solid var(--line); border-radius: 8px; background: var(--card); }
.ball.you { border-color: var(--fg); }
.ball span { font-weight: 400; font-size: 14px; color: var(--muted); margin-left: 6px; }
.stages { list-style: none; padding: 0; display: flex; flex-wrap: wrap; gap: 6px 4px; }
.stages li { padding: 4px 10px; border: 1px solid var(--line); border-radius: 999px; background: var(--card); font-size: 14px; }
.stages li + li::before { content: "\\2192";margin: 0 6px 0 -2px; color: var(--muted); }
.bar { height: 8px; border-radius: 4px; background: var(--line); overflow: hidden; max-width: 420px; }
.bar div { height: 100%; background: var(--fg); }
.cols { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr)); gap: 12px; }
.col { background: var(--card); border: 1px solid var(--line); border-radius: 8px; padding: 4px 14px 10px; }
.col h3 { font-size: 14px; margin: 10px 0 6px; display: inline-block; }
.col h3 span { color: var(--muted); font-weight: 400; margin-left: 4px; }
.col ul { margin: 0; padding-left: 18px; font-size: 14px; }
.col li { margin: 4px 0; }
summary { cursor: pointer; }
.who { display: inline-block; font-size: 12px; font-weight: 600; padding: 1px 8px; border-radius: 999px; border: 1px solid var(--line); }
.who-you { background: var(--fg); color: var(--bg); border-color: var(--fg); }
td code { white-space: pre-wrap; word-break: break-all; }
</style>"""


def render(d):
    now = d["now"]
    name = d["repo"].split("/")[-1]
    done, total = done_count(d["board"])
    board_line = ("<p>%d of %d cards done</p>%s" % (done, total, bar(done, total))) if total else ""
    built = time.strftime("%Y-%m-%d %H:%M", time.localtime(now))
    return """<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>%(name)s progress</title>
%(style)s
%(extra)s
<main>
<h1>%(name)s: progress</h1>
%(ball)s
<h2>Stages</h2>
%(stages)s
%(board_line)s
<h2>Agents running</h2>
%(agents)s
<h2>Open PRs</h2>
%(prs)s
<h2>Board</h2>
%(board)s
<h2>Agents that finished</h2>
%(finished)s
<p class="note">Built at %(built)s<span id="age"></span> from %(events)s and GitHub (%(repo)s).
This page is a snapshot; it is built again when something changes. Merge, comment and approve on GitHub.</p>
</main>
<script>
(function () {
  var mins = Math.floor((Date.now() / 1000 - %(now)d) / 60);
  if (mins >= 1) document.getElementById("age").textContent = ", " + mins + " min ago";
})();
</script>
""" % {"name": esc(name), "style": STYLE, "extra": EXTRA, "ball": ball_html(d["ball"], now),
       "stages": stages_html(d["stages"], d["build_count"], d.get("map", mp.MAP), d.get("map_found", True)),
       "board_line": board_line,
       "agents": agents_html(d["running"], now), "prs": prs_html(d["prs"]), "board": board_html(d["board"]),
       "finished": finished_html(d["finished"], now), "built": built, "events": esc(d["events"]),
       "repo": esc(d["repo"]), "now": int(now)}


def main(argv):
    a = argparse.ArgumentParser(description="Build the progress page.")
    a.add_argument("output")
    a.add_argument("--repo", help="owner/name whose board and PRs to show; default: this folder's repo")
    a.add_argument("--events", default=os.path.join(".apb", "events.jsonl"))
    a.add_argument("--map", default=mp.MAP)
    opts = a.parse_args(argv)
    repo = opts.repo or json.loads(mp.run(["gh", "repo", "view", "--json", "nameWithOwner"]))["nameWithOwner"]
    try:
        with open(opts.map, encoding="utf-8") as f:
            names, found = stages(f.read()), True
    except OSError:
        names, found = [], False
    b, prs, count = github(repo)
    now = time.time()
    events = watch.read_events(opts.events)
    running, finished = agent_runs(events, now)
    page = render({"repo": repo, "now": now, "ball": ball(events, running),"stages": names, "build_count": count,
                   "board": b, "prs": prs, "running": running, "finished": finished, "events": opts.events,
                   "map": opts.map, "map_found": found})
    with open(opts.output, "w", encoding="utf-8") as f:
        f.write(page)
    print("wrote", opts.output)


if __name__ == "__main__":
    main(sys.argv[1:])
