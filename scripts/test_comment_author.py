"""Tests for comment-author.py: Claude's comments told from the human's by author login, with the
signature as the fallback.

Run: python3 -m unittest discover -s scripts -t scripts
"""
import importlib.util
import json
import os
import random
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("comment_author", os.path.join(HERE, "comment-author.py"))
ca = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ca)

CLAUDE, HUMAN = "hornof-claude", "hornof"
SIGN = "\n\n— Claude (reviewer)"


class ByLogin(unittest.TestCase):
    def test_an_unsigned_comment_by_claudes_account_is_claudes(self):
        self.assertTrue(ca.is_claude("Fixed in abc.", CLAUDE, CLAUDE))

    def test_logins_match_without_case(self):
        self.assertTrue(ca.is_claude("Fixed.", "Hornof-Claude", CLAUDE))

    def test_an_unsigned_comment_by_the_human_is_the_humans(self):
        self.assertFalse(ca.is_claude("b, thanks", HUMAN, CLAUDE))

    def test_a_human_reply_quoting_claude_is_the_humans(self):
        quote = "> 3. Rename it.\n>\n> — Claude (reviewer)\n\nNo, keep it."
        self.assertFalse(ca.is_claude(quote, HUMAN, CLAUDE))

    def test_a_signed_comment_by_the_humans_login_is_claudes(self):
        """The advisor posts as the human, with its signature; older comments are signed too."""
        self.assertTrue(ca.is_claude("Research notes." + "\n\n— Claude (advisor)", HUMAN, CLAUDE))


class Fallback(unittest.TestCase):
    def test_no_account_reads_signatures(self):
        self.assertTrue(ca.is_claude("Fixed." + SIGN, HUMAN, None))
        self.assertFalse(ca.is_claude("Fixed.", HUMAN, None))

    def test_no_author_reads_signatures(self):
        """A deleted user's comments have no author."""
        self.assertTrue(ca.is_claude("Fixed." + SIGN, None, CLAUDE))
        self.assertFalse(ca.is_claude("Fixed.", None, CLAUDE))

    def test_signature_forms(self):
        self.assertTrue(ca.signed("Fixed.\n\n- Claude (builder)"))
        self.assertTrue(ca.signed("Done." + SIGN))
        self.assertFalse(ca.signed("I asked Claude about it."))
        self.assertFalse(ca.signed(None))


def oracle(body, author, login):
    """The rule as the issue states it, written separately: the author first, then the signature on the
    lines that aren't quoted."""
    if login is not None and author is not None and author.casefold() == login.casefold():
        return True
    for line in (body or "").split("\n"):
        if line.strip().startswith(">"):
            continue
        for dash in ("—", "-"):
            i = line.find(dash)
            while i >= 0:
                rest = line[i + 1:].lstrip()
                if rest.startswith("Claude (") and ")" in rest[8:]:
                    role = rest[8:rest.index(")", 8)]
                    if role and all(c.isalnum() or c == "_" for c in role):
                        return True
                i = line.find(dash, i + 1)
    return False


class Properties(unittest.TestCase):
    def test_random_comments(self):
        rng = random.Random(64)
        pieces = ["Fixed in abc.", "b", "why?", "— Claude (reviewer)", "- Claude (builder)", "> — Claude (reviewer)",
                  "> quoted", "Claude said so", "ok", "", "-Claude (orchestrator)", "— Claude ()"]
        logins = [CLAUDE, HUMAN, "HORNOF-CLAUDE", None]
        for i in range(500):
            body = "\n".join(rng.choice(pieces) for _ in range(rng.randint(0, 4)))
            author, login = rng.choice(logins), rng.choice([CLAUDE, None])
            with self.subTest(i=i, body=body, author=author, login=login):
                self.assertEqual(ca.is_claude(body, author, login), oracle(body, author, login))

    def test_claudes_account_always_wins_and_signatures_always_count(self):
        rng = random.Random(65)
        for i in range(200):
            body = rng.choice(["", "x", "> — Claude (reviewer)", "b"])
            self.assertTrue(ca.is_claude(body, CLAUDE, CLAUDE))
            self.assertTrue(ca.is_claude(body + SIGN, rng.choice([HUMAN, None, CLAUDE]), rng.choice([CLAUDE, None])))


class Login(unittest.TestCase):
    def settings(self, data):
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, "settings.local.json")
        with open(path, "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))
        return path

    def test_the_option_wins(self):
        path = self.settings({"env": {"GH_TOKEN": "x", "GIT_AUTHOR_NAME": "other"}})
        self.assertEqual(ca.claude_login("given", path, {}), "given")

    def test_from_the_settings_file(self):
        path = self.settings({"env": {"GH_TOKEN": "x", "GIT_AUTHOR_NAME": CLAUDE}})
        self.assertEqual(ca.claude_login(None, path, {"GH_TOKEN": "y", "GIT_AUTHOR_NAME": "env-login"}), CLAUDE)

    def test_from_the_environment(self):
        missing = os.path.join(tempfile.mkdtemp(), "none.json")
        self.assertEqual(ca.claude_login(None, missing, {"GH_TOKEN": "y", "GIT_AUTHOR_NAME": CLAUDE}), CLAUDE)

    def test_an_author_name_without_a_token_is_not_an_account(self):
        """A human may set GIT_AUTHOR_NAME for themselves; only claude-account.py sets both."""
        path = self.settings({"env": {"GIT_AUTHOR_NAME": "Luke"}})
        self.assertIsNone(ca.claude_login(None, path, {"GIT_AUTHOR_NAME": "Luke"}))

    def test_none_without_an_account(self):
        for data in ["not json", {"env": "x"}, [], {"permissions": {}}]:
            with self.subTest(data=data):
                self.assertIsNone(ca.claude_login(None, self.settings(data), {}))

    def test_the_token_never_reaches_the_result(self):
        path = self.settings({"env": {"GH_TOKEN": "secret-token", "GIT_AUTHOR_NAME": CLAUDE}})
        self.assertNotIn("secret", ca.claude_login(None, path, {}))


if __name__ == "__main__":
    unittest.main()
