"""Tests for map-artifact.py. Run from the repo root: python3 -m unittest discover scripts

The pure parts (links, row and link checks, the build count) are checked with hand-made inputs
and, for the build count, with random counts against a second, simpler way of getting the answer.
"""
import importlib.util
import json
import os
import random
import re
import unittest

spec = importlib.util.spec_from_file_location("map_artifact", os.path.join(os.path.dirname(__file__), "map-artifact.py"))
mp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mp)

REPO, BRANCH, PAGE = "owner/repo", "main", "https://claude.ai/artifact/abc"
MD = """# `demo`: project map

The [map artifact](https://claude.ai/artifact/abc) is built from this file.

**Stages, in order:** PRD input → PRD (gate 1) → TDD (gate 2) → split the TDD into cards → build → review and merge (gate 3) → check the live product

| Piece | What it is |
|---|---|
| [PRD](PRD.md) | The product doc |
| [Site](https://example.com) | The live site |
"""
# The shape of what GitHub's renderer returns for MD.
STAGES = ("<p><strong>Stages, in order:</strong> PRD input → PRD (gate 1) → TDD (gate 2) → split the TDD into cards"
          " → build → review and merge (gate 3) → check the live product</p>")
HTML = """<h1>demo: project map</h1>
<p>The <a href="https://claude.ai/artifact/abc">map artifact</a> is built from this file.</p>
""" + STAGES + """
<table><thead><tr><th>Piece</th><th>What it is</th></tr></thead><tbody>
<tr><td><a href="PRD.md">PRD</a></td><td>The product doc</td></tr>
<tr><td><a href="https://example.com" rel="nofollow">Site</a></td><td>The live site</td></tr>
</tbody></table>"""


def build(md=MD, body=HTML, done=0, total=0):
    return mp.build(md, body, REPO, BRANCH, "abc123", PAGE, done, total)


class Links(unittest.TestCase):
    def test_repo_file_becomes_github_link(self):
        self.assertEqual(mp.absolute("PRD.md", REPO, BRANCH), "https://github.com/owner/repo/blob/main/docs/PRD.md")

    def test_path_above_docs(self):
        self.assertEqual(mp.absolute("../README.md", REPO, BRANCH), "https://github.com/owner/repo/blob/main/README.md")

    def test_web_links_and_anchors_unchanged(self):
        for href in ["https://example.com", "http://x.org/a", "#now", "mailto:a@b.c"]:
            self.assertEqual(mp.absolute(href, REPO, BRANCH), href)


class Build(unittest.TestCase):
    def test_good_input_builds_a_page(self):
        page = build()
        self.assertIn("<title>demo: project map</title>", page)
        self.assertIn('href="https://github.com/owner/repo/blob/main/docs/PRD.md"', page)
        self.assertIn('href="https://example.com"', page)
        self.assertIn("commit abc123", page)

    def test_link_to_the_page_itself_becomes_text(self):
        page = build()
        self.assertNotIn(f'href="{PAGE}"', page)
        self.assertIn("The map artifact is built", page)

    def test_missing_row_is_caught(self):
        dropped = HTML.replace('<tr><td><a href="https://example.com" rel="nofollow">Site</a></td><td>The live site</td></tr>', "")
        with self.assertRaises(SystemExit):
            build(body=dropped)

    def test_extra_row_is_caught(self):
        with self.assertRaises(SystemExit):
            build(body=HTML.replace("</tbody>", "<tr><td>x</td><td>y</td></tr></tbody>"))

    def test_missing_link_is_caught(self):
        with self.assertRaises(SystemExit):
            build(body=HTML.replace('<a href="PRD.md">PRD</a>', "PRD"))

    def test_divider_rows_not_counted(self):
        self.assertEqual(len(mp.md_rows(MD)), 3)
        self.assertEqual(len(mp.md_rows("| a |\n|:--|\n| b |\n")), 2)

    def test_no_stage_diagram_and_no_current_stage(self):
        # The map never marks a current stage; the board shows where the project stands.
        page = build(done=2, total=5)
        self.assertNotIn("mermaid", page)
        self.assertNotIn('class="now"', page)
        self.assertNotIn(">Now<", page)


class OneRel(unittest.TestCase):
    def test_each_link_has_one_rel(self):
        page = build()
        self.assertNotIn('rel="nofollow"', page)
        self.assertEqual(page.count('href="https://example.com" target="_blank" rel="noopener"'), 1)


class BuildCount(unittest.TestCase):
    """The Build stage shows how many build cards are done, worked out from GitHub."""

    def test_count_from_github_issues(self):
        issues = json.dumps([{"state": "CLOSED"}, {"state": "OPEN"}, {"state": "CLOSED"}, {"state": "OPEN"}])
        self.assertEqual(mp.build_count(issues), (2, 4))

    def test_no_issues(self):
        self.assertEqual(mp.build_count("[]"), (0, 0))

    def test_count_shows_after_build_only(self):
        page = build(done=3, total=10)
        self.assertIn("→ build (3 of 10 cards done) →", page)
        self.assertEqual(page.count("of 10 cards done"), 1)

    def test_no_build_cards_yet_shows_no_count(self):
        page = build(done=0, total=0)
        self.assertNotIn("cards done", page)
        self.assertIn("→ build →", page)

    def test_capital_build_works_too(self):
        page = build(body=HTML.replace("→ build →", "→ Build →"), done=1, total=2)
        self.assertIn("→ Build (1 of 2 cards done) →", page)

    def test_build_elsewhere_in_page_left_alone(self):
        body = HTML.replace("is built from this file", "is a build of this file")
        page = build(body=body, done=1, total=2)
        self.assertIn("is a build of this file", page)
        self.assertIn("→ build (1 of 2 cards done) →", page)

    def test_missing_stages_line_is_caught(self):
        with self.assertRaises(SystemExit):
            build(body=HTML.replace(STAGES, "<p>No stages here.</p>"), done=1, total=2)

    def test_missing_build_stage_is_caught(self):
        with self.assertRaises(SystemExit):
            build(body=HTML.replace("→ build →", "→"), done=1, total=2)

    def test_more_done_than_total_is_caught(self):
        with self.assertRaises(SystemExit):
            build(done=3, total=2)

    def test_random_counts_against_a_second_way(self):
        rng = random.Random(31)
        for _ in range(300):
            states = [rng.choice(["OPEN", "CLOSED"]) for _ in range(rng.randint(0, 40))]
            done, total = mp.build_count(json.dumps([{"state": s} for s in states]))
            # Second way: count the words in the raw JSON.
            raw = json.dumps([{"state": s} for s in states])
            self.assertEqual(done, raw.count('"CLOSED"'))
            self.assertEqual(total, raw.count('"state"'))
            page = build(done=done, total=total)
            # Removing the count gives back the page with no count: nothing else changed.
            without = re.sub(r" \(\d+ of \d+ cards done\)", "", page)
            self.assertEqual(without, build(done=0, total=0))
            if total:
                self.assertEqual(page.count(f"build ({done} of {total} cards done)"), 1)


class RepoFiles(unittest.TestCase):
    def test_finds_only_this_repos_files_on_this_branch(self):
        page = ('<a href="https://github.com/owner/repo/blob/main/docs/PRD.md">'
                '<a href="https://github.com/owner/repo/blob/other/docs/X.md">'
                '<a href="https://github.com/owner/repo">'
                '<a href="https://github.com/someone/else/blob/main/a.md">')
        self.assertEqual(mp.repo_files(page, REPO, BRANCH), ["docs/PRD.md"])

    def test_branch_names_with_slashes(self):
        page = '<a href="https://github.com/owner/repo/blob/docs/fix-x/scripts/a.py">'
        self.assertEqual(mp.repo_files(page, REPO, "docs/fix-x"), ["scripts/a.py"])

    def test_heading_part_is_dropped(self):
        page = '<a href="https://github.com/owner/repo/blob/main/docs/PRD.md#goals">'
        self.assertEqual(mp.repo_files(page, REPO, BRANCH), ["docs/PRD.md"])


class FilesOnGitHub(unittest.TestCase):
    PAGE = ('<a href="https://github.com/owner/repo/blob/main/docs/PRD.md">'
            '<a href="https://github.com/owner/repo/blob/main/scripts/a.py#top">')

    def test_all_files_present_passes(self):
        asked = []
        mp.check_files(self.PAGE, REPO, BRANCH, lambda r, b, p: asked.append((r, b, p)) or True)
        self.assertEqual(asked, [(REPO, BRANCH, "docs/PRD.md"), (REPO, BRANCH, "scripts/a.py")])

    def test_missing_file_is_caught(self):
        with self.assertRaises(SystemExit):
            mp.check_files(self.PAGE, REPO, BRANCH, lambda r, b, p: p != "scripts/a.py")


class Source(unittest.TestCase):
    def test_clean_file_on_a_branch_passes(self):
        mp.check_source("", "main")

    def test_uncommitted_map_is_caught(self):
        with self.assertRaises(SystemExit):
            mp.check_source(" M docs/MAP.md\n", "main")

    def test_detached_head_is_caught(self):
        with self.assertRaises(SystemExit):
            mp.check_source("", "HEAD")


class TimeLimits(unittest.TestCase):
    """Every command the script starts has a time limit, so a stuck gh can't stall it."""

    def setUp(self):
        self.calls = []
        real = mp.subprocess.run

        class Done:
            returncode, stdout = 0, ""

        def fake(args, **kwargs):
            self.calls.append(kwargs)
            return Done()

        mp.subprocess.run = fake
        self.addCleanup(setattr, mp.subprocess, "run", real)

    def test_run_has_a_time_limit(self):
        mp.run(["gh", "x"])
        self.assertGreater(self.calls[0].get("timeout") or 0, 0)

    def test_file_check_has_a_time_limit(self):
        mp.on_github(REPO, BRANCH, "docs/PRD.md")
        self.assertGreater(self.calls[0].get("timeout") or 0, 0)


if __name__ == "__main__":
    unittest.main()
