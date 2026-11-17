"""Builds the map artifact (a Claude artifact) from docs/MAP.md, so the two never disagree.

Usage, from the project's repo root:
    python3 <apb plugin folder>/scripts/map-artifact.py <output.html> <map artifact link>
Then publish <output.html> to that same artifact link. Never edit the artifact by hand.

Needs the GitHub CLI (gh), signed in. It uses GitHub's own markdown renderer, so the artifact
shows the map the way GitHub does. Links to files in the repo become links to GitHub, on the
branch the artifact is built from. After a merge, build it again from main.
In the "Stages, in order" line, the build stage gets a count worked out from GitHub, for example
"build (3 of 10 cards done)": closed issues out of all issues labeled "build".
Before writing, it checks that every table row and every link in docs/MAP.md made it into the
artifact, and that every linked repo file exists on GitHub on that branch.
"""
import json
import posixpath
import re
import subprocess
import sys
from datetime import date

MAP = "docs/MAP.md"
TIME_LIMIT = 60  # seconds for each gh or git command
BUILD_LABEL = "build"


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=TIME_LIMIT).stdout


def absolute(href, repo, branch):
    """A link relative to docs/ becomes a GitHub link; web links and #anchors stay as they are."""
    if re.match(r"^(https?:|mailto:|#)", href):
        return href
    path = posixpath.normpath(posixpath.join("docs", href))
    return f"https://github.com/{repo}/blob/{branch}/{path}"


def md_rows(text):
    """Table rows in the markdown, header rows included, divider rows left out."""
    return [l for l in text.splitlines() if l.startswith("|") and not re.match(r"^\|[\s:|-]+\|$", l)]


def md_links(text):
    return re.findall(r"\]\(([^)\s]+)\)", text)


def build_count(issues_json):
    """(closed, all) for the build issues, from `gh issue list --json state` output."""
    states = [i["state"] for i in json.loads(issues_json)]
    return sum(s == "CLOSED" for s in states), len(states)


def add_build_count(body, done, total):
    """Adds "(3 of 10 cards done)" after the build stage in the "Stages, in order" line."""
    if total == 0:
        return body
    if done > total:
        sys.exit(f"build count: {done} done is more than {total} in all")
    line = re.search(r"<strong>Stages, in order:</strong>[^<]*", body)
    if not line:
        sys.exit(f'{MAP} needs a "**Stages, in order:**" line, for the build count')
    stages, n = re.subn(r"\b(build)\b", rf"\1 ({done} of {total} cards done)", line.group(0), count=1, flags=re.I)
    if n != 1:
        sys.exit(f'the "Stages, in order" line in {MAP} has no build stage, for the build count')
    return body[:line.start()] + stages + body[line.end():]


def build(text, body, repo, branch, commit, page_url, done, total):
    # A link to this page itself does nothing in the artifact, so it becomes plain text there.
    body = re.sub(r'<a href="' + re.escape(page_url) + r'"[^>]*>(.*?)</a>', r"\1", body)
    body = re.sub(r'\srel="[^"]*"', "", body)  # GitHub adds its own rel; one rel per link
    body = re.sub(r'href="([^"]+)"',
                  lambda m: f'href="{absolute(m.group(1), repo, branch)}" target="_blank" rel="noopener"', body)
    body = add_build_count(body, done, total)
    title = re.search(r"^# (.+)$", text, re.M).group(1).replace("`", "")
    # Checks: nothing in the file may go missing in the artifact.
    rows_md, rows_html = len(md_rows(text)), body.count("<tr>")
    if rows_md != rows_html:
        sys.exit(f"table rows: {rows_md} in {MAP}, {rows_html} in the artifact")
    for href in md_links(text):
        if href != page_url and f'href="{absolute(href, repo, branch)}"' not in body:
            sys.exit(f"link missing in the artifact: {href}")
    note = f"Built from {MAP} at commit {commit} on {date.today().isoformat()}."
    return PAGE.format(title=title, body=body, note=note)


PAGE = """<title>{title}</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600&family=Space+Grotesk:wght@600&display=swap">
<style>
:root {{ --bg: #FAFAF7; --fg: #1A1A1A; --muted: #55524B; --line: #DDD9D0; --card: #FFFFFF; }}
@media (prefers-color-scheme: dark) {{ :root:not([data-theme="light"]) {{
  --bg: #161614; --fg: #EDEBE6; --muted: #A9A49A; --line: #36332E; --card: #1E1D1A; color-scheme: dark }} }}
:root[data-theme="dark"] {{ --bg: #161614; --fg: #EDEBE6; --muted: #A9A49A; --line: #36332E; --card: #1E1D1A; color-scheme: dark }}
body {{ margin: 0; background: var(--bg); color: var(--fg); font: 16px/1.5 Inter, system-ui, sans-serif; }}
main {{ max-width: 1040px; margin: 0 auto; padding: 40px 16px 64px; }}
h1, h2 {{ font-family: 'Space Grotesk', Inter, sans-serif; font-weight: 600; margin: 32px 0 10px; }}
h1 {{ font-size: clamp(28px, 4vw, 40px); margin-top: 0; }}
h2 {{ font-size: 20px; }}
.anchor {{ display: none; }}
a {{ color: inherit; text-underline-offset: 3px; }}
table {{ display: block; overflow-x: auto; border-collapse: collapse; background: var(--card); border: 1px solid var(--line); border-radius: 8px; font-size: 15px; }}
th, td {{ text-align: left; vertical-align: top; padding: 10px 12px; border-bottom: 1px solid var(--line); }}
tr:last-child td {{ border-bottom: 0; }}
th {{ font-size: 12px; text-transform: uppercase; letter-spacing: 0.04em; color: var(--muted); }}
td:first-child {{ font-weight: 600; min-width: 200px; }}
code {{ font-size: 13px; background: var(--bg); border: 1px solid var(--line); border-radius: 4px; padding: 1px 4px; }}
.note {{ color: var(--muted); font-size: 13px; margin-top: 32px; }}
</style>
<main>
{body}
<p class="note">{note}</p>
</main>
"""


def main():
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    # Links to repo files point at the branch the artifact is built from, so they work while a PR is open.
    branch = run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip()
    check_source(run(["git", "status", "--porcelain", MAP]), branch)
    text = open(MAP).read()
    repo = json.loads(run(["gh", "repo", "view", "--json", "nameWithOwner"]))["nameWithOwner"]
    body = run(["gh", "api", "markdown", "-f", f"text={text}", "-f", "mode=gfm", "-f", f"context={repo}"])
    commit = run(["git", "rev-parse", "--short", "HEAD"]).strip()
    done, total = build_count(run(["gh", "issue", "list", "--repo", repo, "--label", BUILD_LABEL,
                                   "--state", "all", "--limit", "1000", "--json", "state"]))
    page = build(text, body, repo, branch, commit, sys.argv[2], done, total)
    check_files(page, repo, branch, on_github)
    open(sys.argv[1], "w").write(page)
    print("wrote", sys.argv[1])


def check_source(status, branch):
    """The artifact must match a commit: no unsaved changes to the map, and a real branch."""
    if status.strip():
        sys.exit(f"{MAP} has changes that aren't committed; commit them first")
    if branch == "HEAD":
        sys.exit("not on a branch; check out a branch first")


def on_github(repo, branch, path):
    return subprocess.run(["gh", "api", f"repos/{repo}/contents/{path}?ref={branch}", "--silent"],
                          capture_output=True, timeout=TIME_LIMIT).returncode == 0


def check_files(page, repo, branch, exists):
    """Every repo file the page links to must exist on GitHub on that branch (so it must be pushed)."""
    for path in repo_files(page, repo, branch):
        if not exists(repo, branch, path):
            sys.exit(f"linked file not on GitHub on branch {branch}: {path} (push first?)")


def repo_files(page, repo, branch):
    """Paths of the repo files a page links to, without any #heading part."""
    return re.findall(rf'href="https://github.com/{re.escape(repo)}/blob/{re.escape(branch)}/([^"#]+)', page)


if __name__ == "__main__":
    main()
