"""Tests for issue #165: the orchestrator's session runs one watcher, the hook's. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts -p test_one_watcher_docs.py

The apb-build skill and the playbook used to tell the orchestrator to start a watcher of its own per
card, beside the hook's, so one PR event woke the session twice. Now they tell it to follow the card
with `apb-watch.py --follow N`, which the hook's watcher reads.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = ("skills/apb-build/SKILL.md", "docs/playbook.md", "skills/apb-loop/SKILL.md", "README.md")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


class OneWatcher(unittest.TestCase):
    def test_no_doc_starts_a_second_watcher(self):
        for rel in DOCS:
            if not os.path.exists(os.path.join(ROOT, rel)):
                continue
            text = read(rel)
            self.assertIsNone(re.search(r"apb-watch\.py --state", text), rel)
            self.assertNotIn("own watcher", text, rel)

    def test_the_build_skill_and_playbook_follow_the_card(self):
        for rel in ("skills/apb-build/SKILL.md", "docs/playbook.md"):
            text = read(rel)
            self.assertIn("apb-watch.py --follow", text, rel)
            self.assertIn("--unfollow", text, rel)


if __name__ == "__main__":
    unittest.main()
