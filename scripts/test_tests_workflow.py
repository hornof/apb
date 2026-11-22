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


def evaluate(text, ctx):
    """Fill in a value with `${{ }}` expressions, for the small subset this workflow uses:
    `github.<name>`, string literals, `==`, `&&`, `||`. GitHub's && and || return an operand,
    like Python's `and` and `or`, so the expression is translated and evaluated."""
    if isinstance(text, bool):
        return text

    def one(m):
        expr = m.group(1)
        unknown = re.sub(r"github\.\w+|'[^']*'|==|&&|\|\||\s", "", expr)
        if unknown:
            raise ValueError("unsupported expression: %r" % expr)
        expr = re.sub(r"github\.(\w+)", lambda g: repr(str(ctx[g.group(1)])), expr)
        expr = expr.replace("&&", " and ").replace("||", " or ")
        return str(eval(expr, {"__builtins__": {}}))  # only literals and operators remain

    out = re.sub(r"\$\{\{(.*?)\}\}", one, text)
    return {"True": True, "False": False}.get(out, out)


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
            self.assertEqual(len(steps), 2, name)
            # Runs after a pass and after a failure. Not `failure()` alone: then a green PR would
            # skip the second suite (review of PR #150, Fix 2).
            runs_either_way = {"success() || failure()", "failure() || success()", "always()", "!cancelled()"}
            for step in steps[1:]:
                cond = " ".join(step.get("if", "").replace("${{", "").replace("}}", "").split())
                self.assertIn(cond, runs_either_way, step.get("name"))

    def group(self, **ctx):
        return evaluate(self.wf["concurrency"]["group"], ctx)

    def test_a_push_run_has_a_group_of_its_own(self):
        # GitHub keeps one pending run per group and cancels an older pending one, even with
        # cancel-in-progress false (review of PR #150, Fix 1). Each run on main gets its own group,
        # so three quick merges each get a Tests run.
        push = dict(event_name="push", ref="refs/heads/main")
        self.assertNotEqual(self.group(run_id="101", **push), self.group(run_id="102", **push))
        self.assertEqual(evaluate(self.wf["concurrency"]["cancel-in-progress"], dict(event_name="push")), False)

    def test_a_new_push_to_a_pr_replaces_its_older_run(self):
        pr = dict(event_name="pull_request", ref="refs/pull/7/merge")
        self.assertEqual(self.group(run_id="101", **pr), self.group(run_id="102", **pr))
        other = dict(pr, ref="refs/pull/8/merge")
        self.assertNotEqual(self.group(run_id="101", **pr), self.group(run_id="101", **other))
        self.assertEqual(evaluate(self.wf["concurrency"]["cancel-in-progress"], dict(event_name="pull_request")), True)

    def test_the_suites_run_from_the_repo_root(self):
        # discover -s hooks needs the checkout as the working folder: no step moves it.
        for job in self.jobs.values():
            for step in job.get("steps", []):
                self.assertNotIn("working-directory", step)
            self.assertNotIn("defaults", job)


class Evaluator(unittest.TestCase):
    """The expression helper the group tests rely on (guards against a vacuous pass)."""

    def test_and_or_pick_an_operand(self):
        e = "x-${{ github.event_name == 'pull_request' && github.ref || github.run_id }}"
        self.assertEqual(evaluate(e, dict(event_name="pull_request", ref="r", run_id="9")), "x-r")
        self.assertEqual(evaluate(e, dict(event_name="push", ref="r", run_id="9")), "x-9")

    def test_a_plain_value_stays_as_is(self):
        self.assertEqual(evaluate("tests-${{ github.ref }}", dict(ref="r")), "tests-r")
        self.assertEqual(evaluate("${{ github.event_name == 'push' }}", dict(event_name="push")), True)

    def test_anything_else_is_refused(self):
        with self.assertRaises(ValueError):
            evaluate("${{ github.ref == 'a' && __import__('os') }}", dict(ref="a"))


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
