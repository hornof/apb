"""Tests for map-page.py. Run from the repo root: python3 -m unittest discover scripts"""
import importlib.util
import os
import unittest

spec = importlib.util.spec_from_file_location("map_page", os.path.join(os.path.dirname(__file__), "map-page.py"))
mp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mp)

REPO, BRANCH = "owner/repo", "main"
MD = """# `demo`: project map

**Now:** testing

| Piece | What it is |
|---|---|
| [PRD](PRD.md) | The product doc |
| [Site](https://example.com) | The live site |
"""
# What GitHub's renderer returns for MD (shape only; the script never parses it beyond hrefs and rows).
HTML = """<h1>demo: project map</h1><p><strong>Now:</strong> testing</p>
<table><thead><tr><th>Piece</th><th>What it is</th></tr></thead><tbody>
<tr><td><a href="PRD.md">PRD</a></td><td>The product doc</td></tr>
<tr><td><a href="https://example.com">Site</a></td><td>The live site</td></tr>
</tbody></table>"""


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
        page = mp.build(MD, HTML, REPO, BRANCH, "abc123")
        self.assertIn("<title>demo: project map</title>", page)
        self.assertIn('href="https://github.com/owner/repo/blob/main/docs/PRD.md"', page)
        self.assertIn('href="https://example.com"', page)
        self.assertIn("commit abc123", page)

    def test_missing_row_is_caught(self):
        dropped = HTML.replace('<tr><td><a href="https://example.com">Site</a></td><td>The live site</td></tr>', "")
        with self.assertRaises(SystemExit):
            mp.build(MD, dropped, REPO, BRANCH, "abc123")

    def test_extra_row_is_caught(self):
        extra = HTML.replace("</tbody>", "<tr><td>x</td><td>y</td></tr></tbody>")
        with self.assertRaises(SystemExit):
            mp.build(MD, extra, REPO, BRANCH, "abc123")

    def test_missing_link_is_caught(self):
        broken = HTML.replace('<a href="PRD.md">PRD</a>', "PRD")
        with self.assertRaises(SystemExit):
            mp.build(MD, broken, REPO, BRANCH, "abc123")

    def test_divider_rows_not_counted(self):
        self.assertEqual(len(mp.md_rows(MD)), 3)
        self.assertEqual(len(mp.md_rows("| a |\n|:--|\n| b |\n")), 2)


if __name__ == "__main__":
    unittest.main()
