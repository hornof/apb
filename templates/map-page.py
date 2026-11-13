"""Builds the project map page from docs/MAP.md, so the page and the file never disagree.

Usage, from the repo root:  python3 scripts/map-page.py <output.html>
Then publish <output.html> to the same Claude page as before. Never edit the page by hand.

Needs the GitHub CLI (gh), signed in. It uses GitHub's own markdown renderer, so the page
shows the map the way GitHub does. Links to files in the repo become links to GitHub.
Before writing, it checks that every table row and every link in the file made it onto the page.
"""
import json
import posixpath
import re
import subprocess
import sys
from datetime import date

MAP = "docs/MAP.md"


def run(args):
    return subprocess.run(args, check=True, capture_output=True, text=True).stdout


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


def build(text, body, repo, branch, commit):
    body = re.sub(r'href="([^"]+)"',
                  lambda m: f'href="{absolute(m.group(1), repo, branch)}" target="_blank" rel="noopener"', body)
    title = re.search(r"^# (.+)$", text, re.M).group(1).replace("`", "")
    # Checks: nothing in the file may go missing on the page.
    rows_md, rows_html = len(md_rows(text)), body.count("<tr>")
    if rows_md != rows_html:
        sys.exit(f"table rows: {rows_md} in {MAP}, {rows_html} on the page")
    for href in md_links(text):
        if f'href="{absolute(href, repo, branch)}"' not in body:
            sys.exit(f"link missing on the page: {href}")
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
    if len(sys.argv) != 2:
        sys.exit(__doc__)
    text = open(MAP).read()
    info = json.loads(run(["gh", "repo", "view", "--json", "nameWithOwner,defaultBranchRef"]))
    repo, branch = info["nameWithOwner"], info["defaultBranchRef"]["name"]
    body = run(["gh", "api", "markdown", "-f", f"text={text}", "-f", "mode=gfm", "-f", f"context={repo}"])
    commit = run(["git", "rev-parse", "--short", "HEAD"]).strip()
    open(sys.argv[1], "w").write(build(text, body, repo, branch, commit))
    print("wrote", sys.argv[1])


if __name__ == "__main__":
    main()
