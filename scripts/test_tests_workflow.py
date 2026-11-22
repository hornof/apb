"""Tests for issue #143: apb runs its tests on GitHub for every PR. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts

`.github/workflows/tests.yml` runs both unittest suites on macOS on every PR and every push to
main. The playbook's docs-only rule (#130) says "GitHub's checks still run as usual"; it names
this workflow, so the reader knows which check that is. The workflow is parsed with Ruby's YAML
parser, as in test_ci_template.py (no Python YAML library ships with macOS).
"""
import json
import os
import re
import shutil
import subprocess
import unittest

from test_docs_only_docs import RULE, bullets, read, section

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOW = ".github/workflows/tests.yml"
SUITES = {
    "hooks": "python3 -m unittest discover -s hooks -t hooks",
    "scripts": "python3 -m unittest discover -s scripts -t scripts",
}


def load(rel):
    script = "require 'yaml'; require 'json'; puts JSON.generate(YAML.load_file(ARGV[0]))"
    out = subprocess.run(["ruby", "-e", script, os.path.join(ROOT, rel)],
                         capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def runs(job):
    """Each step's `run` text, with its whitespace collapsed."""
    return [" ".join(s["run"].split()) for s in job.get("steps", []) if "run" in s]


@unittest.skipUnless(shutil.which("ruby"), "needs ruby to parse YAML")
class Workflow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wf = load(WORKFLOW)
        cls.jobs = cls.wf["jobs"]

    def test_runs_on_every_pr_and_every_push_to_main(self):
        # Ruby's YAML 1.1 parser reads the bare key `on` as true.
        on = self.wf.get("on", self.wf.get("true"))
        self.assertIn("pull_request", on)
        # Every PR, whatever branch it targets: no branch filter on pull_request.
        self.assertFalse((on["pull_request"] or {}).get("branches"), "pull_request has no branch filter")
        self.assertEqual(on["push"]["branches"], ["main"])

    def test_reads_only(self):
        self.assertEqual(self.wf.get("permissions"), {"contents": "read"})

    def test_each_suite_runs_on_macos_with_a_time_limit(self):
        # apb's hooks assume a Mac (#143). A stuck test must not hold the runner for six hours.
        found = {}
        for name, job in self.jobs.items():
            for line in runs(job):
                for suite, command in SUITES.items():
                    if command in line:
                        found.setdefault(suite, []).append(name)
                        self.assertEqual(job["runs-on"], "macos-latest", name)
                        self.assertLessEqual(int(job["timeout-minutes"]), 30, name)
        self.assertEqual(sorted(found), sorted(SUITES), "both suites run")
        for suite, names in found.items():
            self.assertEqual(len(names), 1, "%s runs once" % suite)

    def test_a_failing_first_suite_still_runs_the_second(self):
        # Each suite is its own step; the later one runs even when the earlier one failed,
        # so one red run shows every broken test, not only the first suite's.
        for name, job in self.jobs.items():
            steps = [s for s in job.get("steps", []) if any(c in " ".join(s.get("run", "").split())
                                                            for c in SUITES.values())]
            for step in steps[1:]:
                self.assertIn("failure()", step.get("if", ""), step.get("name"))

    def test_the_suites_run_from_the_repo_root(self):
        # discover -s hooks needs the checkout as the working folder: no step moves it.
        for job in self.jobs.values():
            for step in job.get("steps", []):
                self.assertNotIn("working-directory", step)
            self.assertNotIn("defaults", job)


class EveryTestFileRuns(unittest.TestCase):
    """The two discover commands reach every test file in the repo (independent of the workflow text)."""

    def test_every_test_file_is_in_hooks_or_scripts(self):
        out = subprocess.run(["git", "-C", ROOT, "ls-files"], capture_output=True, text=True,
                             timeout=30, check=True).stdout.split()
        tests = [p for p in out if re.match(r"test.*\.py$", os.path.basename(p))]
        self.assertTrue(tests)
        stray = [p for p in tests if os.path.dirname(p) not in SUITES]
        self.assertEqual(stray, [], "a test file outside hooks/ and scripts/ would never run on GitHub")


class Playbook(unittest.TestCase):
    def test_the_docs_only_rule_names_the_workflow(self):
        testing = section(read("docs/playbook.md"), "Testing and models")
        rule = [b for b in bullets(testing, "  - ") if b.startswith(RULE)]
        self.assertEqual(len(rule), 1)
        self.assertIn("GitHub's checks still run as usual", rule[0])
        self.assertIn("`%s`" % WORKFLOW, rule[0])


if __name__ == "__main__":
    unittest.main()
