"""Tests for issue #130: a change with no code runs no tests. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts

On 2026-11-21 the orchestrator ran every apb test file before opening a PR that only added a
one-line CLAUDE.md, because nothing said what to do when no code changed. The rule sits where the
builder reads it (agents/builder.md, "Tests and pushes") and in the playbook's "Testing and models".
It also says plainly that a skill's SKILL.md and an agent's file are not docs, since the model runs
them as instructions.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RULE = "A PR that changes only docs runs no tests."
HAND_OFF = '"no tests: docs only"'


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def section(text, name):
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(name), text, re.S | re.M)
    return m.group(1) if m else ""


def flat(text):
    return " ".join(text.replace("*", "").split())


def bullets(text, prefix):
    """Each bullet starting with `prefix` (e.g. "- " or "  - "), joined with its wrapped lines, flattened."""
    out, cur = [], None
    for line in text.splitlines():
        if line.startswith(prefix):
            if cur is not None:
                out.append(flat(cur))
            cur = line[len(prefix):]
        elif cur is not None and line.startswith(" " * len(prefix)) and not line.lstrip().startswith("- "):
            cur += " " + line
        elif cur is not None:
            out.append(flat(cur))
            cur = None
    if cur is not None:
        out.append(flat(cur))
    return out


def the_rule(items):
    found = [b for b in items if b.startswith(RULE)]
    return found[0] if len(found) == 1 else None


class RuleText:
    """What both copies of the rule must say."""

    def check(self, rule):
        self.assertIsNotNone(rule, "exactly one bullet starts with the rule")
        # The hand-off line, word for word.
        self.assertIn(HAND_OFF, rule)
        # What counts as docs (flat() drops the `*` of `*.md`).
        self.assertIn("Docs are `.md` files people read", rule)
        # The edge cases: instructions the model runs are not docs.
        for words in ("`SKILL.md`", "`agents/`", "not docs", "instructions", "hook or script reads"):
            self.assertIn(words, rule, words)
        # A doc a test reads still runs that test.
        self.assertIn("a test file reads", rule)


class Builder(unittest.TestCase, RuleText):
    def test_tests_and_pushes_states_the_rule(self):
        text = read("agents/builder.md")
        block = text.split("Tests and pushes:\n", 1)[1].split("\n\n", 1)[0]
        self.check(the_rule(bullets(block, "- ")))

    def test_the_rule_is_stated_once(self):
        self.assertEqual(flat(read("agents/builder.md")).count(RULE), 1)


class Playbook(unittest.TestCase, RuleText):
    def test_run_each_test_only_where_needed_states_the_rule(self):
        testing = section(read("docs/playbook.md"), "Testing and models")
        self.check(the_rule(bullets(testing, "  - ")))

    def test_the_rule_is_stated_once(self):
        self.assertEqual(flat(read("docs/playbook.md")).count(RULE), 1)


class Oracle(unittest.TestCase):
    """The bullet helper finds a rule only when it is a bullet of its own (guards against a vacuous pass)."""

    def test_rule_inside_another_bullet_is_not_found(self):
        self.assertIsNone(the_rule(bullets("- Other text. " + RULE + "\n", "- ")))

    def test_wrapped_bullet_is_joined(self):
        items = bullets("- " + RULE + "\n  and more\n- next\n", "- ")
        self.assertEqual(items, [RULE + " and more", "next"])


if __name__ == "__main__":
    unittest.main()
