"""Tests for plugin-update.py (#162): after a version-bumping merge the orchestrator updates the plugin
itself, checks the installed version against main's, and hands the human one step: open a new session.
Run from the repo root: python3 -m unittest discover -s scripts -t scripts

The card's "Done when": a test covers the hand-off text for both the success case and the fallback.
- Success: both commands worked and the installed version equals main's. The output names the version
  installed, and its last line is the one ask: "1 thing for you to do right now: open a new session and
  send `Start #N.`". It never hands the human the update commands.
- Fallback: a command failed, hung or wasn't found, the installed version can't be read or differs from
  main's, or main's can't be read. The output says what went wrong and which versions it saw, and its
  last line hands the human both commands, then the new session.

A fake claude and a fake gh stand in (--claude, --gh). The fake claude records each call, and
`claude plugin list --json` reports the version the fake "installed". Every run has a time limit.
"""
import itertools
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "plugin-update.py")
MARKETPLACE = "claude plugin marketplace update apb"
UPDATE = "claude plugin update apb@apb"
ASK = "1 thing for you to do right now: "

FAKE_CLAUDE = r'''
import json, os, sys, time
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "calls.log"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\n")
with open(os.path.join(here, "mode.json")) as f:
    mode = json.load(f)
args = sys.argv[1:]
if args[:3] == ["plugin", "marketplace", "update"]:
    step = "marketplace"
elif args[:2] == ["plugin", "update"]:
    step = "update"
elif args[:2] == ["plugin", "list"]:
    step = "list"
else:
    sys.exit("fake claude: unexpected " + " ".join(args))
how = mode.get(step, "ok")
if how == "hang":
    time.sleep(60)
if how == "fail":
    sys.stderr.write("Error: %s went wrong\nmore detail\n" % step)
    sys.exit(1)
if step == "update":
    with open(os.path.join(here, "installed"), "w") as f:
        f.write(mode["installs"])
if step == "list":
    if how == "garbage":
        print("not json")
        sys.exit(0)
    with open(os.path.join(here, "installed")) as f:
        installed = f.read()
    plugins = [{"id": "other@x", "version": "9.9.9"}]
    if how != "missing":
        plugins.append({"id": "apb@apb", "version": installed, "enabled": True})
    print(json.dumps(plugins, indent=2))
    sys.exit(0)
print("ok: " + step)
'''

FAKE_GH = r'''
import os, sys
here = os.path.dirname(os.path.abspath(__file__))
with open(os.path.join(here, "main")) as f:
    main = f.read()
if main == "fail":
    sys.exit("gh: HTTP 404")
print('{"name": "apb", "version": "%s", "repository": "https://github.com/hornof/apb"}' % main)
'''

CREATED = []


def tmp():
    d = tempfile.mkdtemp()
    CREATED.append(d)
    return d


def executable(folder, name, body):
    path = os.path.join(folder, name)
    with open(path, "w") as f:
        f.write("#!%s\n%s" % (sys.executable, body))
    os.chmod(path, 0o755)
    return path


class Rig:
    """A fake claude, a fake gh and a fake installed plugin, set up for one run."""

    def __init__(self, session="0.3.71", main="0.3.72", installs=None, **steps):
        d = tmp()
        self.claude = executable(d, "claude", FAKE_CLAUDE)
        self.gh = executable(d, "gh", FAKE_GH)
        with open(os.path.join(d, "mode.json"), "w") as f:
            json.dump(dict(steps, installs=installs or main), f)
        with open(os.path.join(d, "installed"), "w") as f:
            f.write(session)
        with open(os.path.join(d, "main"), "w") as f:
            f.write(main)
        self.root = os.path.join(d, "plugin")
        os.makedirs(os.path.join(self.root, ".claude-plugin"))
        with open(os.path.join(self.root, ".claude-plugin", "plugin.json"), "w") as f:
            json.dump({"name": "apb", "version": session, "repository": "https://github.com/hornof/apb"}, f)
        self.folder = d

    def run(self, *extra, timeout="5"):
        return subprocess.run([sys.executable, SCRIPT, "--claude", self.claude, "--gh", self.gh,
                               "--plugin-root", self.root, "--timeout", timeout, *extra],
                              stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)

    def calls(self):
        path = os.path.join(self.folder, "calls.log")
        if not os.path.exists(path):
            return []
        with open(path) as f:
            return [" ".join(json.loads(line)) for line in f.read().splitlines()]


def lines(out):
    return [l for l in out.splitlines() if l.strip()]


class Clean(unittest.TestCase):
    @classmethod
    def tearDownClass(cls):
        while CREATED:
            shutil.rmtree(CREATED.pop(), ignore_errors=True)


class Success(Clean):
    def test_updates_and_hands_over_one_step(self):
        rig = Rig(session="0.3.71", main="0.3.72")
        r = rig.run("--next", "163")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        out = lines(r.stdout)
        self.assertEqual(out[-1], ASK + "open a new session and send `Start #163.`")
        self.assertIn("0.3.72", out[0])  # the version it installed
        self.assertIn("0.3.71", out[0])  # and the one this session still runs
        self.assertEqual(r.stdout.count(ASK), 1)
        # The human isn't handed the update commands: the script ran them.
        self.assertNotIn(MARKETPLACE, r.stdout)
        self.assertNotIn(UPDATE, r.stdout)

    def test_runs_the_two_commands_in_order_then_reads_the_version(self):
        rig = Rig()
        rig.run("--next", "5")
        calls = rig.calls()
        self.assertEqual(calls[0], "plugin marketplace update apb")
        self.assertEqual(calls[1], "plugin update apb@apb")
        self.assertTrue(calls[2].startswith("plugin list") and "--json" in calls[2], calls)
        self.assertEqual(len(calls), 3)

    def test_without_a_next_card_the_ask_is_the_new_session_alone(self):
        r = Rig().run()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(lines(r.stdout)[-1], ASK + "open a new session.")

    def test_a_card_number_with_a_hash_is_the_same(self):
        r = Rig().run("--next", "#163")
        self.assertEqual(lines(r.stdout)[-1], ASK + "open a new session and send `Start #163.`")

    def test_already_at_main_is_still_success(self):
        # The update says "already at the latest version": installed equals main, nothing to fall back to.
        r = Rig(session="0.3.72", main="0.3.72").run("--next", "7")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("0.3.72", lines(r.stdout)[0])


class Fallback(Clean):
    def assert_fallback(self, r, *says, next_card="163"):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        out = lines(r.stdout)
        self.assertEqual(r.stdout.count(ASK), 1, r.stdout)
        self.assertEqual(out[-1], ASK + "run `%s` and `%s`, then open a new session and send `Start #%s.`"
                         % (MARKETPLACE, UPDATE, next_card))
        for part in says:
            self.assertIn(part, r.stdout)
        return out

    def test_marketplace_fails_stops_there(self):
        rig = Rig(marketplace="fail")
        r = rig.run("--next", "163")
        self.assert_fallback(r, MARKETPLACE, "Error: marketplace went wrong", "0.3.71", "0.3.72")
        self.assertNotIn("more detail", r.stdout)  # the command's first line of why, not all of it
        self.assertEqual(rig.calls(), ["plugin marketplace update apb"])

    def test_plugin_update_fails(self):
        rig = Rig(update="fail")
        self.assert_fallback(rig.run("--next", "163"), UPDATE, "Error: update went wrong")

    def test_a_command_that_hangs_is_stopped(self):
        rig = Rig(update="hang")
        r = rig.run("--next", "163", timeout="1")
        self.assert_fallback(r, UPDATE, "time")

    def test_claude_not_found(self):
        rig = Rig()
        r = subprocess.run([sys.executable, SCRIPT, "--claude", os.path.join(rig.folder, "no-such-claude"),
                            "--gh", rig.gh, "--plugin-root", rig.root, "--next", "163"],
                           stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
        self.assert_fallback(r, MARKETPLACE)

    def test_installed_version_still_differs_from_main(self):
        # Both commands "worked" but the installed version is still the old one.
        r = Rig(session="0.3.71", main="0.3.72", installs="0.3.71").run("--next", "163")
        self.assert_fallback(r, "0.3.71", "0.3.72")

    def test_installed_version_unreadable(self):
        self.assert_fallback(Rig(list="garbage").run("--next", "163"), "installed")
        self.assert_fallback(Rig(list="missing").run("--next", "163"), "installed")
        self.assert_fallback(Rig(list="fail").run("--next", "163"), "installed")

    def test_main_unreadable(self):
        self.assert_fallback(Rig(main="fail", installs="0.3.72").run("--next", "163"), "main")

    def test_without_a_next_card(self):
        r = Rig(update="fail").run()
        self.assertEqual(r.returncode, 1)
        self.assertEqual(lines(r.stdout)[-1], ASK + "run `%s` and `%s`, then open a new session."
                         % (MARKETPLACE, UPDATE))


class EveryCase(Clean):
    """Every mix of outcomes, against an independent oracle: success exactly when both commands worked,
    the installed version was read and equals main's, and main's was read. Whatever happens, one ask,
    on the last line, and it hands over the commands exactly when the run didn't succeed."""

    def test_oracle(self):
        cases = itertools.product(["ok", "fail"], ["ok", "fail"], ["ok", "garbage", "missing"],
                                  ["0.3.72", "fail"], ["0.3.72", "0.3.71"])
        for market, update, listing, main, installs in cases:
            with self.subTest(market=market, update=update, listing=listing, main=main, installs=installs):
                rig = Rig(session="0.3.71", main=main, installs=installs, marketplace=market, update=update,
                          list=listing)
                r = rig.run("--next", "9")
                ok = market == "ok" and update == "ok" and listing == "ok" and main != "fail" \
                    and installs == main
                self.assertEqual(r.returncode, 0 if ok else 1, r.stdout + r.stderr)
                out = lines(r.stdout)
                self.assertEqual(r.stdout.count(ASK), 1, r.stdout)
                self.assertTrue(out[-1].startswith(ASK))
                self.assertEqual(MARKETPLACE in out[-1], not ok)
                self.assertTrue(out[-1].endswith("open a new session and send `Start #9.`"), out[-1])
                # The plugin update never runs after a failed marketplace update.
                self.assertEqual(any(c.startswith("plugin update") for c in rig.calls()), market == "ok")


class RandomVersions(Clean):
    """Random versions and card numbers (seeded): success exactly when the installed version equals main's;
    the first line names the installed version on success, both versions it saw on a fallback; the ask
    names the card, with or without a "#"."""

    def test_random(self):
        rng = random.Random(162)
        for _ in range(12):
            v = lambda: "%d.%d.%d" % (rng.randint(0, 3), rng.randint(0, 99), rng.randint(0, 200))
            session, main = v(), v()
            installs = main if rng.random() < 0.5 else v()
            card = str(rng.randint(1, 9999))
            given = rng.choice([card, "#" + card, " %s " % card])
            with self.subTest(session=session, main=main, installs=installs, given=given):
                r = Rig(session=session, main=main, installs=installs).run("--next", given)
                ok = installs == main
                self.assertEqual(r.returncode, 0 if ok else 1, r.stdout + r.stderr)
                out = lines(r.stdout)
                self.assertTrue(out[-1].endswith("open a new session and send `Start #%s.`" % card), out[-1])
                self.assertIn(installs, out[0])
                if not ok:
                    self.assertIn(main, out[0])


class Allowed(Clean):
    def test_new_projects_allow_the_script_without_a_prompt(self):
        # The wake names the script by its path in the installed plugin; the rule matches any folder.
        import fnmatch
        with open(os.path.join(os.path.dirname(HERE), "templates", "claude-settings-template.json")) as f:
            allow = json.load(f)["permissions"]["allow"]
        command = "python3 /Users/x/.claude/plugins/cache/apb/apb/0.3.72/scripts/plugin-update.py --next 163"
        rules = [a[len("Bash("):-1] for a in allow if a.startswith("Bash(")]
        self.assertTrue(any(fnmatch.fnmatchcase(command, r) for r in rules), rules)


if __name__ == "__main__":
    unittest.main()
