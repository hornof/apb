"""Tests for claude-account.py. Run from the repo root: python3 -m unittest discover -s scripts -t scripts

GitHub and git are faked: each test hands the script a fake `run` that answers `gh api -i user` and
`git check-ignore` the way GitHub and git do. The main rule is checked on every path, including
random tokens and random settings files: the token never appears in anything the script prints or
passes on a command line, and it reaches the settings file only when every check passed.
"""
import importlib.util
import io
import json
import os
import random
import shutil
import string
import subprocess
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("claude_account", os.path.join(HERE, "claude-account.py"))
ca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ca)

TOKEN = "ghp_" + "A1b2C3d4E5" * 3 + "f6G7h8"
USER = {"login": "someone-claude", "id": 1234567, "node_id": "U_x", "name": "Claude"}


def answer(scopes="project, read:org, repo", status="200 OK", user=USER):
    head = ["HTTP/2.0 " + status, "Content-Type: application/json; charset=utf-8"]
    if scopes is not None:
        head.append("X-Oauth-Scopes: " + scopes)
    return "\r\n".join(head) + "\r\n\r\n" + json.dumps(user)


class Fake:
    """Answers the two commands the script runs, and records every call."""

    def __init__(self, user_answer=None, ignored=True, gh_fails=None):
        self.user_answer = answer() if user_answer is None else user_answer
        self.ignored = ignored
        self.gh_fails = gh_fails
        self.calls = []

    def __call__(self, args, env=None):
        self.calls.append((list(args), dict(env or {})))
        if args[:2] == ["gh", "api"]:
            if self.gh_fails:
                return subprocess.CompletedProcess(args, 1, "", self.gh_fails)
            return subprocess.CompletedProcess(args, 0, self.user_answer, "")
        if args[0] == "git" and "check-ignore" in args:
            return subprocess.CompletedProcess(args, 0 if self.ignored else 1, "", "")
        raise AssertionError("unexpected command: %r" % (args,))


class Case(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir)
        self.token_file = os.path.join(self.dir, "claude-token.txt")
        self.settings = os.path.join(self.dir, ".claude", "settings.local.json")

    def write_token(self, text=TOKEN + "\n"):
        with open(self.token_file, "w") as f:
            f.write(text)

    def go(self, fake, *extra, token=TOKEN):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = ca.main([self.token_file, "--settings", self.settings] + list(extra), run=fake)
        self.out = out.getvalue() + err.getvalue()
        self.assertNotIn(token, self.out, "the token was printed")
        for args, _ in fake.calls:
            self.assertNotIn(token, " ".join(args), "the token was on a command line")
        return code

    def stored(self):
        with open(self.settings) as f:
            return json.load(f)


class Success(Case):
    def test_stores_the_token_and_the_git_author(self):
        self.write_token()
        self.assertEqual(self.go(Fake()), 0)
        env = self.stored()["env"]
        email = "1234567+someone-claude@users.noreply.github.com"
        self.assertEqual(env, {"GH_TOKEN": TOKEN,
                               "GIT_AUTHOR_NAME": "someone-claude", "GIT_AUTHOR_EMAIL": email,
                               "GIT_COMMITTER_NAME": "someone-claude", "GIT_COMMITTER_EMAIL": email})

    def test_deletes_the_token_file(self):
        self.write_token()
        self.go(Fake())
        self.assertFalse(os.path.exists(self.token_file))

    def test_keep_file_keeps_it(self):
        self.write_token()
        self.go(Fake(), "--keep-file")
        self.assertTrue(os.path.exists(self.token_file))

    def test_keeps_everything_else_in_the_settings_file(self):
        os.makedirs(os.path.dirname(self.settings))
        with open(self.settings, "w") as f:
            json.dump({"permissions": {"allow": ["Bash(ls *)"]}, "env": {"OTHER": "1", "GH_TOKEN": "old"}}, f)
        self.write_token()
        self.go(Fake())
        got = self.stored()
        self.assertEqual(got["permissions"], {"allow": ["Bash(ls *)"]})
        self.assertEqual(got["env"]["OTHER"], "1")
        self.assertEqual(got["env"]["GH_TOKEN"], TOKEN)

    def test_settings_file_is_private(self):
        self.write_token()
        self.go(Fake())
        self.assertEqual(os.stat(self.settings).st_mode & 0o777, 0o600)

    def test_token_goes_to_gh_in_the_environment(self):
        self.write_token()
        fake = Fake()
        self.go(fake)
        gh = [env for args, env in fake.calls if args[0] == "gh"]
        self.assertEqual(len(gh), 1)
        self.assertEqual(gh[0].get("GH_TOKEN"), TOKEN)

    def test_reports_the_login_and_scopes(self):
        self.write_token()
        self.go(Fake())
        for words in ("someone-claude", "project", "read:org", "repo", "gh auth status"):
            self.assertIn(words, self.out)

    def test_extra_scopes_are_allowed(self):
        self.write_token()
        self.assertEqual(self.go(Fake(answer("gist, project, read:org, repo, workflow"))), 0)

    def test_surrounding_whitespace_is_ignored(self):
        self.write_token("\n  " + TOKEN + "  \n\n")
        self.assertEqual(self.go(Fake()), 0)
        self.assertEqual(self.stored()["env"]["GH_TOKEN"], TOKEN)


class FromAnEarlierProject(Case):
    """An earlier project's settings file can stand in for the token file; it is never deleted."""

    def earlier(self, env):
        path = os.path.join(self.dir, "earlier", ".claude", "settings.local.json")
        os.makedirs(os.path.dirname(path))
        with open(path, "w") as f:
            json.dump({"env": env, "permissions": {}}, f)
        self.token_file = path
        return path

    def test_copies_the_token(self):
        path = self.earlier({"GH_TOKEN": TOKEN, "GIT_AUTHOR_NAME": "x"})
        self.assertEqual(self.go(Fake()), 0)
        self.assertEqual(self.stored()["env"]["GH_TOKEN"], TOKEN)
        self.assertTrue(os.path.exists(path))
        with open(path) as f:
            self.assertEqual(json.load(f)["env"]["GH_TOKEN"], TOKEN)

    def test_no_token_in_it(self):
        self.earlier({"OTHER": "1"})
        self.assertEqual(self.go(Fake()), 1)
        self.assertIn("GH_TOKEN", self.out)
        self.assertFalse(os.path.exists(self.settings))


class Refusals(Case):
    """Every refusal exits 1, leaves the settings file alone and keeps the token file."""

    def refused(self, fake, words):
        self.assertEqual(self.go(fake), 1)
        self.assertFalse(os.path.exists(self.settings))
        self.assertTrue(os.path.exists(self.token_file))
        self.assertIn(words, self.out)

    def test_missing_read_org(self):
        self.write_token()
        self.refused(Fake(answer("project, repo")), "read:org")

    def test_missing_project(self):
        self.write_token()
        self.refused(Fake(answer("read:org, repo")), "project")

    def test_fine_grained_token(self):
        self.write_token()
        self.refused(Fake(answer(scopes=None)), "classic")

    def test_github_rejects_the_token(self):
        self.write_token()
        self.refused(Fake(gh_fails="HTTP 401: Bad credentials (token %s)" % TOKEN), "Bad credentials")

    def test_settings_file_not_git_ignored(self):
        self.write_token()
        self.refused(Fake(ignored=False), "git-ignored")

    def test_file_with_more_than_the_token(self):
        self.write_token(TOKEN + "\nmy note\n")
        self.refused(Fake(), "only the token")

    def test_empty_file(self):
        self.write_token("\n")
        self.refused(Fake(), "only the token")

    def test_missing_file(self):
        self.assertEqual(self.go(Fake()), 1)
        self.assertIn("no file", self.out)
        self.assertFalse(os.path.exists(self.settings))


class Scopes(unittest.TestCase):
    def test_reads_the_header_in_any_case(self):
        self.assertEqual(ca.scopes(answer("repo, project").replace("X-Oauth-Scopes", "x-oauth-scopes")),
                         {"repo", "project"})

    def test_no_header_means_none(self):
        self.assertIsNone(ca.scopes(answer(scopes=None)))

    def test_empty_header_means_no_scopes(self):
        self.assertEqual(ca.scopes(answer(scopes="")), set())

    def test_body_is_read_after_the_headers(self):
        self.assertEqual(ca.body(answer())["login"], "someone-claude")

    def test_body_with_plain_newlines(self):
        self.assertEqual(ca.body(answer().replace("\r\n", "\n"))["id"], 1234567)


class RandomRuns(Case):
    """Random tokens, scopes and settings files, against a direct statement of the rule: stored exactly
    when the three needed scopes are there, never printed either way."""

    def test_random(self):
        rng = random.Random(3)
        all_scopes = ["repo", "project", "read:org", "gist", "workflow", "admin:org"]
        for i in range(150):
            token = "ghp_" + "".join(rng.choice(string.ascii_letters + string.digits) for _ in range(36))
            chosen = [s for s in all_scopes if rng.random() < 0.6]
            rng.shuffle(chosen)
            existing = {"env": {"KEEP_%d" % i: "v"}} if rng.random() < 0.5 else None
            if os.path.exists(self.settings):
                os.remove(self.settings)
            if existing:
                os.makedirs(os.path.dirname(self.settings), exist_ok=True)
                with open(self.settings, "w") as f:
                    json.dump(existing, f)
            self.write_token(token + "\n")
            code = self.go(Fake(answer(", ".join(chosen))), token=token)
            ok = {"repo", "project", "read:org"} <= set(chosen)
            self.assertEqual(code, 0 if ok else 1, chosen)
            self.assertEqual(os.path.exists(self.token_file), not ok)
            if ok:
                env = self.stored()["env"]
                self.assertEqual(env["GH_TOKEN"], token)
                if existing:
                    self.assertEqual(env["KEEP_%d" % i], "v")
            elif existing:
                self.assertEqual(self.stored(), existing)
            else:
                self.assertFalse(os.path.exists(self.settings))


class RealRun(unittest.TestCase):
    def test_run_has_a_time_limit_and_passes_the_environment(self):
        done = ca.run(["python3", "-c", "import os; print(os.environ.get('APB_TEST_VAR'))"],
                      env={"APB_TEST_VAR": "seen"})
        self.assertEqual(done.stdout.strip(), "seen")


if __name__ == "__main__":
    unittest.main()
