"""Tests for decision-check.py. Run from the repo root: python3 scripts/test_decision_check.py

The shape check is tested with hand-made threads, and with random well-formed Decisions that must
pass and must fail once any one of the four parts is taken out. The answer check is tested with
hand-made replies, and with random reply histories against a second, simpler way of working out
the answer: the generator knows which letter, if any, each reply it writes names.
"""
import importlib.util
import os
import random
import unittest
from unittest import mock

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("decision_check", os.path.join(HERE, "decision-check.py"))
dc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dc)

GOOD = """**16. Decision:** Which word should the save button use?

The page calls it "Save" and the docs call it "Keep".

a) "Save", as on the page.
b) "Keep", as in the docs.

Recommended: a
Resolve with no reply = a

— Claude (reviewer)"""

SIGN = "\n\n— Claude (orchestrator)"


def thread(*comments, resolved=False):
    return {"isResolved": resolved, "path": "docs/x.md", "line": 3, "comments": list(comments)}


class Shape(unittest.TestCase):
    def test_a_good_decision_has_no_problems(self):
        self.assertEqual(dc.shape_problems(GOOD), [])

    def test_the_options(self):
        self.assertEqual(dc.options(GOOD), ["a", "b"])
        self.assertEqual(dc.options("Which?\n- (a) one\n- (b) two\n- (c) three"), ["a", "b", "c"])
        self.assertEqual(dc.options("Which?\n**a.** one\n**b.** two"), ["a", "b"])
        self.assertEqual(dc.options("Which?\nA: one\nB: two"), ["a", "b"])

    def test_options_must_run_from_a(self):
        self.assertEqual(dc.options("Which?\nb) one\nc) two"), [])
        self.assertEqual(dc.options("Which?\na) one\nc) two"), [])
        self.assertEqual(dc.options("Which?\na) only one"), [])

    def test_each_missing_part_is_named(self):
        parts = {
            "question": "**16. Decision:** Which word should the save button use?\n",
            "options": 'a) "Save", as on the page.\nb) "Keep", as in the docs.\n',
            "recommended": "Recommended: a\n",
            "resolve": "Resolve with no reply = a\n",
        }
        for left_out in parts:
            body = "".join(text for name, text in parts.items() if name != left_out)
            with self.subTest(left_out=left_out):
                self.assertEqual(dc.shape_problems(body), [dc.MISSING[left_out]])

    def test_a_question_in_two_sentences_is_a_problem(self):
        body = GOOD.replace("**16. Decision:** Which", "**16. Decision:** The button is unclear. Which")
        self.assertEqual(dc.shape_problems(body), [dc.MISSING["question"]])

    def test_a_statement_is_not_a_question(self):
        body = GOOD.replace("Which word should the save button use?", "Pick the save button's word.")
        self.assertEqual(dc.shape_problems(body), [dc.MISSING["question"]])

    def test_a_letter_that_is_not_an_option(self):
        self.assertEqual(dc.shape_problems(GOOD.replace("Recommended: a", "Recommended: c")),
                         [dc.MISSING["recommended"]])
        self.assertEqual(dc.shape_problems(GOOD.replace("reply = a", "reply = d")), [dc.MISSING["resolve"]])

    def test_resolve_must_match_recommended(self):
        self.assertEqual(dc.shape_problems(GOOD.replace("reply = a", "reply = b")), [dc.MISMATCH])

    def test_markup_and_case_around_the_labels(self):
        body = GOOD.replace("Recommended: a", "**Recommended:** (a)").replace(
            "Resolve with no reply = a", "_Resolve with no reply = a._")
        self.assertEqual(dc.shape_problems(body), [])

    def test_the_item_number(self):
        self.assertEqual(dc.number(GOOD), "16")
        self.assertEqual(dc.number("Decision 7: which?"), "7")
        self.assertIsNone(dc.number("Which one?"))


def random_decision(rng):
    """A well-formed Decision in one of the ways a reviewer might write it, and its parts."""
    n = rng.randint(2, 5)
    letters = "abcde"[:n]
    rec = rng.choice(letters)
    label = rng.choice(["{}) ", "({}) ", "- ({}) ", "**{}.** ", "{}: ", "- {}) "])
    upper = rng.random() < 0.2
    parts = {
        "question": rng.choice(["**%d. Decision:** " % rng.randint(1, 30), "", "Decision: "]) +
        rng.choice(["Which name should it use?", "Keep `docs/a.md` or fold it in?",
                    "Should the page show 1.5 or 2 columns?"]) + "\n",
        "context": rng.choice(["", "The page and the docs disagree. Both work.\n", "Context: e.g. the map.\n"]),
        "options": "".join(label.format(c.upper() if upper else c) + "choice %s.\n" % c for c in letters),
        "recommended": rng.choice(["Recommended: %s\n", "**Recommended:** %s\n", "Recommended: (%s)\n"]) % rec,
        "resolve": rng.choice(["Resolve with no reply = %s\n", "**Resolve with no reply = %s**\n"]) % rec,
        "sign": rng.choice(["", "\n— Claude (reviewer)\n"]),
    }
    return parts, letters, rec


class ShapeProperties(unittest.TestCase):
    def test_random_decisions(self):
        rng = random.Random(53)
        for i in range(300):
            parts, letters, rec = random_decision(rng)
            body = "".join(parts.values())
            with self.subTest(i=i, body=body):
                self.assertEqual(dc.shape_problems(body), [])
                self.assertEqual(dc.options(body), list(letters))
                self.assertEqual(dc.recommended(body), rec)
            for left_out in ("question", "options", "recommended", "resolve"):
                cut = "".join(text for name, text in parts.items() if name != left_out)
                with self.subTest(i=i, left_out=left_out, body=cut):
                    self.assertIn(dc.MISSING[left_out], dc.shape_problems(cut))


class Letter(unittest.TestCase):
    def test_replies_that_name_a_letter(self):
        for reply, want in [("b", "b"), ("B.", "b"), ("ok, c", "c"), ("(b)", "b"), ("option c", "c"),
                            ("Go with a, it's simpler.", "a"), ("b) but shorten it", "b"),
                            ("Let's pick b.", "b"), ("I'd choose (c) here", "c"), ("Fine with (b) here.", "b")]:
            with self.subTest(reply=reply):
                self.assertEqual(dc.chosen(reply, ["a", "b", "c"]), want)

    def test_replies_that_do_not(self):
        for reply in ["Why is this needed?", "I think a better name would help.", "Can you show me a mockup?",
                      "A mockup first, please.", "Can you pick a better name?", "option d", "(e)", "Let's talk about it.", ""]:
            with self.subTest(reply=reply):
                self.assertIsNone(dc.chosen(reply, ["a", "b", "c"]))

    def test_a_reply_signed_by_claude_is_not_the_humans(self):
        self.assertTrue(dc.is_claude("Done in abc123." + SIGN))
        self.assertTrue(dc.is_claude("Fixed.\n\n- Claude (builder)"))
        self.assertFalse(dc.is_claude("b, thanks"))


class Status(unittest.TestCase):
    def test_no_reply_is_waiting(self):
        self.assertEqual(dc.status(thread(GOOD)), ("waiting", None))

    def test_resolved_with_no_reply_takes_the_recommendation(self):
        self.assertEqual(dc.status(thread(GOOD, resolved=True)), ("resolved", "a"))

    def test_resolved_after_a_letter_takes_the_letter(self):
        self.assertEqual(dc.status(thread(GOOD, "b", resolved=True)), ("resolved", "b"))

    def test_a_letter_in_the_last_reply_answers(self):
        self.assertEqual(dc.status(thread(GOOD, "why?", "Explained." + SIGN, "ok, b")), ("answered", "b"))

    def test_only_the_last_human_reply_counts(self):
        self.assertEqual(dc.status(thread(GOOD, "b", "hmm, wait")), ("waiting", None))

    def test_claude_replies_after_the_letter_do_not_hide_it(self):
        self.assertEqual(dc.status(thread(GOOD, "b", "Starting on b." + SIGN)), ("answered", "b"))

    def test_three_human_replies_and_no_letter_is_a_discussion(self):
        self.assertEqual(dc.status(thread(GOOD, "why?", "A." + SIGN, "but then?", "B." + SIGN, "and the docs?")),
                         ("discussion", None))

    def test_claude_replies_do_not_count_toward_the_three(self):
        self.assertEqual(dc.status(thread(GOOD, "why?", "A." + SIGN, "B." + SIGN, "C." + SIGN)), ("waiting", None))

    def test_ready(self):
        self.assertTrue(dc.ready(("resolved", "a")))
        self.assertTrue(dc.ready(("answered", "b")))
        self.assertFalse(dc.ready(("waiting", None)))
        self.assertFalse(dc.ready(("discussion", None)))


LETTER_REPLIES = ["{}", "{}.", "ok, {}", "({})", "option {}", "Go with {}, it's simpler.", "Let's pick {}."]
OTHER_REPLIES = ["Why is this needed?", "I think a better name would help.", "Can you show me a mockup?",
                 "A mockup first, please.", "Let's talk about it.", "What does the map say?"]


def oracle(resolved, replies, rec):
    """replies: [(text, letter or None, by_claude)]. The rule as the issue states it."""
    human = [(t, l) for t, l, by_claude in replies if not by_claude]
    last = human[-1][1] if human else None
    if resolved:
        return ("resolved", last or rec)
    if last:
        return ("answered", last)
    if len(human) >= 3:
        return ("discussion", None)
    return ("waiting", None)


class StatusProperties(unittest.TestCase):
    def test_random_histories(self):
        rng = random.Random(530)
        for i in range(500):
            parts, letters, rec = random_decision(rng)
            replies = []
            for _ in range(rng.randint(0, 6)):
                kind = rng.random()
                if kind < 0.3:
                    replies.append(("Done in abc." + SIGN, None, True))
                elif kind < 0.6:
                    c = rng.choice(letters)
                    replies.append((rng.choice(LETTER_REPLIES).format(c), c, False))
                else:
                    replies.append((rng.choice(OTHER_REPLIES), None, False))
            resolved = rng.random() < 0.3
            t = thread("".join(parts.values()), *[r[0] for r in replies], resolved=resolved)
            with self.subTest(i=i, replies=replies, resolved=resolved):
                self.assertEqual(dc.status(t), oracle(resolved, replies, rec))


class Report(unittest.TestCase):
    def test_lines_and_exit_code(self):
        bad = GOOD.replace("Resolve with no reply = a\n", "")
        threads = [thread(GOOD, "b"), thread(bad, resolved=True)]
        lines, problems = dc.report(threads)
        self.assertEqual(problems, 1)
        self.assertIn("Decision 16 (docs/x.md:3): shape ok; answered b, ready for the builder", lines[0])
        self.assertIn("missing " + dc.MISSING["resolve"], lines[1])
        self.assertIn("resolved, a", lines[1])

    def test_a_discussion_says_so(self):
        lines, _ = dc.report([thread(GOOD, "why?", "and?", "but?")])
        self.assertIn("design discussion, not a decision", lines[0])

    def test_resolved_with_nothing_to_accept(self):
        old = "Should we keep it?\n\nI'd keep it.\n\n— Claude (reviewer)"
        state = dc.status(thread(old, resolved=True))
        self.assertEqual(state, ("resolved", None))
        self.assertFalse(dc.ready(state))
        lines, _ = dc.report([thread(old, resolved=True)])
        self.assertIn("resolved with no option letter: read the thread", lines[0])

    def test_a_thread_with_no_line(self):
        t = thread(GOOD)
        t["line"] = None
        self.assertIn("Decision 16 (docs/x.md): shape ok", dc.report([t])[0][0])

    def test_no_threads(self):
        self.assertEqual(dc.report([]), (["No Decision threads."], 0))

    def test_main_reads_github_and_exits_1_on_a_bad_shape(self):
        got = {"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": [
            {"isResolved": False, "path": "a.md", "line": 1,
             "comments": {"nodes": [{"body": "Which one?\na) x\nb) y"}]}}]}}}}}
        with mock.patch.object(dc.subprocess, "run") as run, mock.patch("builtins.print"):
            run.return_value = mock.Mock(stdout=__import__("json").dumps(got))
            self.assertEqual(dc.main(["7", "--repo", "o/r"]), 1)
            self.assertIn("timeout", run.call_args.kwargs)
            self.assertIn("number=7", run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
