"""Tests for templates/ci-template.yml and the docs that describe it. Run from the repo root:
python3 -m unittest discover scripts

The template is parsed with Ruby's YAML parser (no Python YAML library ships with macOS).
The checks follow the rule the docs state: the full browser set runs once per PR, when the
`full-tests` label is added, and on every push to main; every other push to a PR runs the
quick set; a docs-only change skips the browser tests.
"""
import json
import os
import re
import shutil
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TEMPLATE = os.path.join(ROOT, "templates", "ci-template.yml")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def load_template():
    script = "require 'yaml'; require 'json'; puts JSON.generate(YAML.load_file(ARGV[0]))"
    out = subprocess.run(["ruby", "-e", script, TEMPLATE], capture_output=True, text=True, timeout=30, check=True)
    return json.loads(out.stdout)


def condition(job):
    return re.sub(r"\s+", " ", job.get("if", "")).strip()


@unittest.skipUnless(shutil.which("ruby"), "needs ruby to parse YAML")
class TemplateShape(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.wf = load_template()
        cls.jobs = cls.wf["jobs"]

    def test_triggers(self):
        # Ruby's YAML 1.1 parser reads the bare key `on` as true.
        on = self.wf.get("on", self.wf.get("true"))
        self.assertEqual(on["push"]["branches"], ["main"])
        self.assertEqual(set(on["pull_request"]["types"]), {"opened", "synchronize", "reopened", "labeled"})

    def test_other_labels_start_nothing(self):
        cond = condition(self.jobs["changes"])
        self.assertIn("github.event.action != 'labeled'", cond)
        self.assertIn("github.event.label.name == 'full-tests'", cond)
        for name, job in self.jobs.items():
            if name != "changes":
                self.assertIn("changes", job.get("needs", []), name)

    def test_quick_and_full_never_both_run(self):
        full, quick = condition(self.jobs["browsers-full"]), condition(self.jobs["browsers-quick"])
        is_full = "(github.event_name == 'push' || contains(github.event.pull_request.labels.*.name, 'full-tests'))"
        self.assertIn(is_full, full)
        self.assertIn("!" + is_full, quick)
        for cond in (full, quick):
            self.assertIn("needs.changes.outputs.code == 'true'", cond)

    def test_every_event_runs_the_right_jobs(self):
        # Evaluate each job's `if` for every kind of event, and compare with the rule written out
        # separately in plain Python.
        events = [("push", None, None)]
        for action in ("opened", "synchronize", "reopened", "labeled"):
            for added in (["full-tests", "other"] if action == "labeled" else [None]):
                events.append(("pull_request", action, added))
        for event, action, added in events:
            for has_label in (False, True):
                if added == "full-tests" and not has_label:
                    continue  # the label just added is on the PR
                for code in (False, True):
                    ctx = dict(event=event, action=action, added=added, has_label=has_label, code=code)
                    runs = {name: self.runs(job, ctx) for name, job in self.jobs.items()}
                    changes = not (action == "labeled" and added != "full-tests")
                    full = event == "push" or has_label
                    expected = {
                        "changes": changes,
                        "unit": changes,
                        "browsers-full": changes and code and full,
                        "browsers-quick": changes and code and not full,
                    }
                    self.assertEqual(runs, expected, ctx)

    def runs(self, job, ctx):
        """True when GitHub would run the job, for the few expressions the template uses."""
        needs = job.get("needs", [])
        if needs and not all(self.runs(self.jobs[n], ctx) for n in needs):
            return False  # a job whose needed job was skipped is skipped too
        cond = condition(job)
        if not cond:
            return True
        values = {
            "github.event.action": repr(ctx["action"]),
            "github.event.label.name": repr(ctx["added"]),
            "github.event_name": repr(ctx["event"]),
            "contains(github.event.pull_request.labels.*.name, 'full-tests')": str(ctx["has_label"]),
            "needs.changes.outputs.code": repr("true" if ctx["code"] else "false"),
        }
        for expr, value in values.items():
            cond = cond.replace(expr, value)
        cond = cond.replace("&&", " and ").replace("||", " or ").replace("!(", " not (")
        self.assertNotRegex(cond, r"github\.|needs\.|contains\(", "unknown expression left: " + cond)
        return eval(cond, {"__builtins__": {}})

    def group(self, action, added):
        """The concurrency group GitHub would compute for a PR event on one branch."""
        values = {
            "github.ref": "'refs/pull/1/merge'",
            "github.event.action": repr(action),
            "github.event.label.name": repr(added),
        }

        def evaluate(m):
            expr = m.group(1)
            for name, value in values.items():
                expr = expr.replace(name, value)
            expr = expr.replace("&&", " and ").replace("||", " or ")
            self.assertNotRegex(expr, r"github\.", "unknown expression left: " + expr)
            return str(eval(expr, {"__builtins__": {}}))

        return re.sub(r"\$\{\{(.*?)\}\}", evaluate, self.wf["concurrency"]["group"])

    def test_another_label_never_cancels_a_test_run(self):
        # Runs in one group cancel each other. An event that starts no jobs must not share a
        # group with any event that does; every event that starts jobs shares one group.
        self.assertTrue(self.wf["concurrency"]["cancel-in-progress"])
        working = {self.group(a, None) for a in ("opened", "synchronize", "reopened")}
        working.add(self.group("labeled", "full-tests"))
        self.assertEqual(len(working), 1)
        for other in ("other", "bug", "full-tests-later"):
            self.assertNotIn(self.group("labeled", other), working, other)

    def test_unit_tests_run_on_docs_only_changes_too(self):
        self.assertNotIn("outputs.code", condition(self.jobs["unit"]))

    def test_quick_set_commands(self):
        runs = " ".join(s.get("run", "") for s in self.jobs["browsers-quick"]["steps"])
        self.assertIn("--only-changed=origin/", runs)
        checkout = self.jobs["browsers-quick"]["steps"][0]
        self.assertEqual(checkout["with"]["fetch-depth"], 0)

    def test_docs_only_paths(self):
        runs = " ".join(s.get("run", "") for s in self.jobs["changes"]["steps"])
        for path in (r"LOG\.md", r"README\.md", "docs/"):
            self.assertIn(path, runs)

    def test_every_job_has_a_time_limit(self):
        for name, job in self.jobs.items():
            self.assertIn("timeout-minutes", job, name)


class DocsAgree(unittest.TestCase):
    def test_no_doc_says_the_whole_suite_runs_on_every_push(self):
        for rel in ("agents/builder.md", "docs/playbook.md", "skills/apb-tdd/SKILL.md"):
            text = read(rel)
            self.assertNotRegex(text, r"whole (test )?suite[^.]*on every (PR and )?push", rel)
            self.assertNotIn("GitHub runs it when you push", text, rel)

    def test_tdd_step_points_at_the_template_and_label(self):
        text = read("skills/apb-tdd/SKILL.md")
        self.assertIn("templates/ci-template.yml", text)
        self.assertIn("full-tests", text)

    def test_hand_off_adds_the_label(self):
        self.assertIn("--add-label full-tests", read("skills/apb-review/SKILL.md"))

    def test_readme_lists_the_template(self):
        self.assertRegex(read("README.md"), r"`templates/`[^\n]*CI workflow")


if __name__ == "__main__":
    unittest.main()
