"""The hook suite names its failing tests on its last lines (issue #136). Run from the repo root:
python3 -m unittest discover -s hooks -t hooks

A failed run used to end with "FAILED (failures=1)"; the test's name was only in the report above it,
which a run cut to its last lines (`| tail`) loses. Discovery imports this module before any test
runs, and importing it makes unittest's text runner end a failed run with one line per failing test:

    Failed tests (2):
      test_module.SomeCase.test_that_failed
      test_module.OtherCase.test_that_raised (error)

Only a run that imports this file names its failures: `discover` on this folder does; running one
other test file on its own does not.

The tests below check that in a separate run on a made-up suite: the oracle is the list of outcomes the
made-up suite was built with, not the runner's own result.
"""
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.runner

HEADER = "Failed tests (%d):"


def _names(result):
    """One line per failing test, failures then errors, in the order they happened."""
    out = ["  %s" % t.id() for t, _ in result.failures]
    out += ["  %s (error)" % t.id() for t, _ in result.errors]
    out += ["  %s (unexpected success)" % t.id() for t in getattr(result, "unexpectedSuccesses", [])]
    return out


def _install():
    """Wrap TextTestRunner.run once, however many times this file is imported."""
    runner = unittest.runner.TextTestRunner
    if getattr(runner.run, "_apb_names_failures", False):
        return
    plain = runner.run

    def run(self, test):
        result = plain(self, test)
        names = _names(result)
        if names:
            self.stream.writeln(HEADER % len(names))
            for line in names:
                self.stream.writeln(line)
            self.stream.flush()
        return result

    run._apb_names_failures = True
    runner.run = run


_install()


SAMPLE_HEAD = '''import unittest
import suite_report  # noqa: F401  (a copy of hooks/test_suite_report.py)
'''

BODY = {
    "pass": "        pass\n",
    "fail": "        self.fail('made to fail')\n",
    "error": "        raise RuntimeError('made to error')\n",
    "skip": "        self.skipTest('made to skip')\n",
    "unexpected": "        pass\n",  # marked as an expected failure, but it passes
}
DECORATOR = {"unexpected": "    @unittest.expectedFailure\n"}


def sample_suite(outcomes, copies=1):
    """A folder holding a test file whose tests end with the given outcomes, and copies of this module
    for it to import. outcomes: {module name: [(class, test, outcome), ...]}."""
    d = tempfile.mkdtemp()
    src = os.path.abspath(__file__)
    shutil.copy(src, os.path.join(d, "suite_report.py"))
    head = SAMPLE_HEAD
    for n in range(1, copies):
        shutil.copy(src, os.path.join(d, "suite_report_%d.py" % n))
        head += "import suite_report_%d  # noqa: F401\n" % n
    for module, tests in outcomes.items():
        classes = {}
        for cls, name, outcome in tests:
            classes.setdefault(cls, []).append("%s    def %s(self):\n%s"
                                               % (DECORATOR.get(outcome, ""), name, BODY[outcome]))
        text = head + "".join("\n\nclass %s(unittest.TestCase):\n%s" % (c, "\n".join(m))
                              for c, m in classes.items())
        with open(os.path.join(d, module + ".py"), "w") as f:
            f.write(text)
    return d


def run_suite(folder, extra=()):
    """Run the made-up suite as the hook suite is run; returns (exit code, its non-empty output lines)."""
    done = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", folder, "-t", folder] + list(extra),
                          capture_output=True, text=True, timeout=60, cwd=folder)
    return done.returncode, [l for l in (done.stderr + done.stdout).splitlines() if l.strip()]


def expected(outcomes):
    """The lines a failed run must end with, worked out from the outcomes the suite was made with.
    unittest runs modules, classes and tests in name order."""
    fails, errors, unexpected = [], [], []
    for module in sorted(outcomes):
        for cls, name, outcome in sorted(outcomes[module]):
            tid = "%s.%s.%s" % (module, cls, name)
            if outcome == "fail":
                fails.append("  " + tid)
            elif outcome == "error":
                errors.append("  %s (error)" % tid)
            elif outcome == "unexpected":
                unexpected.append("  %s (unexpected success)" % tid)
    names = fails + errors + unexpected
    return [HEADER % len(names)] + names if names else []


class NamesFailingTests(unittest.TestCase):
    def test_a_failed_run_ends_with_the_failing_tests_names(self):
        outcomes = {"test_sample": [("A", "test_ok", "pass"), ("A", "test_bad", "fail"),
                                    ("B", "test_broken", "error"), ("B", "test_later", "skip")]}
        rc, lines = run_suite(sample_suite(outcomes))
        self.assertNotEqual(rc, 0)
        self.assertEqual(lines[-3:], ["Failed tests (2):", "  test_sample.A.test_bad",
                                      "  test_sample.B.test_broken (error)"])
        self.assertTrue(lines[-4].startswith("FAILED ("), lines[-4])

    def test_an_unexpected_success_is_named_after_the_errors(self):
        outcomes = {"test_sample": [("A", "test_bad", "fail"), ("A", "test_lucky", "unexpected"),
                                    ("B", "test_broken", "error")]}
        rc, lines = run_suite(sample_suite(outcomes))
        self.assertNotEqual(rc, 0)
        self.assertEqual(lines[-4:], ["Failed tests (3):", "  test_sample.A.test_bad",
                                      "  test_sample.B.test_broken (error)",
                                      "  test_sample.A.test_lucky (unexpected success)"])

    def test_a_passing_run_names_nothing(self):
        outcomes = {"test_sample": [("A", "test_ok", "pass"), ("A", "test_skipped", "skip")]}
        rc, lines = run_suite(sample_suite(outcomes))
        self.assertEqual(rc, 0)
        self.assertTrue(lines[-1].startswith("OK"), lines[-1])
        self.assertFalse([l for l in lines if l.startswith("Failed tests")])

    def test_imported_twice_it_names_each_test_once(self):
        outcomes = {"test_sample": [("A", "test_bad", "fail")]}
        rc, lines = run_suite(sample_suite(outcomes, copies=3))
        self.assertEqual(lines[-2:], ["Failed tests (1):", "  test_sample.A.test_bad"])
        self.assertEqual(len([l for l in lines if l.startswith("Failed tests")]), 1)

    def test_verbose_and_quiet_runs_name_them_too(self):
        outcomes = {"test_sample": [("A", "test_bad", "fail"), ("A", "test_ok", "pass")]}
        folder = sample_suite(outcomes)
        for extra in (["-v"], ["-q"]):
            rc, lines = run_suite(folder, extra)
            self.assertEqual(lines[-2:], ["Failed tests (1):", "  test_sample.A.test_bad"], extra)

    def test_random_suites_name_exactly_the_failing_tests(self):
        rng = random.Random(136)
        for _ in range(6):
            outcomes = {}
            for m in range(rng.randint(1, 3)):
                tests = [("C%d" % rng.randint(0, 2), "test_%d" % i, rng.choice(list(BODY)))
                         for i in range(rng.randint(1, 6))]
                outcomes["test_m%d" % m] = tests
            rc, lines = run_suite(sample_suite(outcomes))
            want = expected(outcomes)
            self.assertEqual(rc != 0, bool(want), outcomes)
            if want:
                self.assertEqual(lines[-len(want):], want, outcomes)
            else:
                self.assertFalse([l for l in lines if l.startswith("Failed tests")], outcomes)


if __name__ == "__main__":
    unittest.main()
