"""Tests for what apb-setup says about how long the first map and progress build takes. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts -p test_setup_build_time.py

In the 2026-11-20 rehearsal (#104) the step "Building the real map and progress pages" seemed to
take about 5 minutes. The build itself took 12-17 s; the wait was a hung check that ran just before
it. Step 1a says the build takes seconds and runs on its own, so a long wait there points elsewhere.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = "skills/apb-setup/SKILL.md"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def flat(text):
    return " ".join(text.replace("*", "").split())


def steps():
    """Top-level numbered steps of apb-setup, keyed by their label ("1", "1a", "4", ...)."""
    parts = re.split(r"^(\d+[a-z]?)\. ", read(SETUP), flags=re.M)
    return {parts[i]: flat(parts[i + 1]) for i in range(1, len(parts), 2)}


def timing_sentence():
    """The sentence in step 1a that gives the build time in seconds."""
    for s in re.split(r"(?<=[.!?])\s+", steps()["1a"]):
        if re.search(r"\d+\s*[-–]\s*\d+\s*s(econds)?\b", s):
            return s
    return None


class BuildTakesSeconds(unittest.TestCase):
    def test_step_1a_gives_the_time_in_seconds(self):
        s = timing_sentence()
        self.assertIsNotNone(s, "step 1a should say how many seconds the build and publish take")
        self.assertIn("seconds", s)
        lo, hi = map(int, re.search(r"(\d+)\s*[-–]\s*(\d+)\s*s(?:econds)?\b", s).groups())
        # Independent check against the measured builds (12 s and 17 s) and the card's 30 s target.
        self.assertLessEqual(lo, 12)
        self.assertGreaterEqual(hi, 17)
        self.assertLess(hi, 30)

    def test_it_covers_both_pages_and_the_publish(self):
        s = timing_sentence() or ""
        self.assertIn("map", s)
        self.assertIn("progress", s)
        self.assertIn("publish", s)

    def test_run_on_their_own_not_behind_a_slow_check(self):
        s = timing_sentence() or ""
        self.assertIn("on their own", s)
        self.assertRegex(s, r"not behind (a|any) slow check")

    def test_a_long_wait_means_something_else(self):
        s = timing_sentence() or ""
        self.assertRegex(s, r"a (long )?wait (there|here) means something else is running")

    def test_only_step_1a_carries_it(self):
        # The timing note belongs with the build commands, not in another step.
        others = [k for k, v in steps().items() if k != "1a" and "something else is running" in v]
        self.assertEqual(others, [])


if __name__ == "__main__":
    unittest.main()
