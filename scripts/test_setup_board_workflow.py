"""Tests for the board's "Item closed" workflow in apb-setup. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts -p test_setup_board_workflow.py

Done when: a fresh setup ends with the workflow on and no manual step for it.

GitHub's API can't switch the workflow on; only the board's Workflows page in a browser can, and
only for a board admin. So setup gives Claude's GitHub account Admin on the board (the board only;
the repo stays at write), then switches the workflow on itself from Chrome, signed in as that
account. The board is made in step 3, before the account exists (step 5), so the grant goes in
step 5b and the switch in a step after 5e, once the account works. The human gets one clear step
only if Claude's own attempt fails.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SETUP = "skills/apb-setup/SKILL.md"
CLICKS = "Workflows → Item closed → Edit → set Status to Done → Save and turn on"
WORKFLOW_STEP = "g"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def flat(text):
    return " ".join(text.replace("*", "").split())


def steps():
    parts = re.split(r"^(\d+[a-z]?)\. ", read(SETUP), flags=re.M)
    return {parts[i]: flat(parts[i + 1]) for i in range(1, len(parts), 2)}


def substeps(step):
    """Lettered parts (`a.` ... ) of a numbered step, in order, keyed by letter."""
    body = steps()[step]
    found = re.findall(r"(?:^| )([a-h])\. (.*?)(?= [a-h]\. |$)", body)
    return dict(found), [letter for letter, _ in found]


def substep(step, letter):
    return substeps(step)[0][letter]


class BoardStepHandsNothingToTheHuman(unittest.TestCase):
    def test_step_3_points_to_the_later_step(self):
        body = steps()["3"]
        self.assertIn("Item closed", body)
        self.assertIn("step 5%s" % WORKFLOW_STEP, body)

    def test_step_3_says_why_it_waits(self):
        body = steps()["3"]
        self.assertRegex(body, r"[Cc]laude's GitHub account[^.]*(doesn't exist yet|step 5)")

    def test_step_3_no_longer_holds_the_clicks(self):
        # The clicks live in one place, the step that does them.
        self.assertNotIn("Workflows →", steps()["3"])


class AccountIsBoardAdmin(unittest.TestCase):
    def test_board_grant_is_admin(self):
        body = substep("5", "b")
        self.assertIn("updateProjectV2Collaborators", body)
        self.assertIn("role: ADMIN", body)
        self.assertNotIn("role: WRITER", body)

    def test_admin_is_for_the_board_only(self):
        body = substep("5", "b")
        self.assertRegex(body, r"[Aa]dmin on the board only")
        # Oracle: the repo grant itself still asks for write (`push`), never admin.
        repo_grant = re.search(r"collaborators/<account> -f permission=(\w+)", body)
        self.assertEqual(repo_grant.group(1), "push")
        self.assertNotIn("permission=admin", body)

    def test_says_why_admin(self):
        body = substep("5", "b")
        self.assertRegex(body, r"[Ww]orkflows[^.]*admin")


class ClaudeSwitchesTheWorkflowOn(unittest.TestCase):
    def body(self):
        return substep("5", WORKFLOW_STEP)

    def test_step_exists_after_the_account_works(self):
        _, order = substeps("5")
        self.assertIn(WORKFLOW_STEP, order)
        # After the grant (b) and the check that the account works (e).
        self.assertGreater(order.index(WORKFLOW_STEP), order.index("e"))
        self.assertGreater(order.index(WORKFLOW_STEP), order.index("b"))

    def test_uses_chrome_and_the_clicks(self):
        body = self.body()
        self.assertIn("Chrome", body)
        self.assertIn(CLICKS, body)

    def test_checks_the_chrome_account_before_clicking(self):
        body = self.body()
        self.assertRegex(body, r"[Cc]heck which GitHub account Chrome is signed in as")
        self.assertLess(body.index("which GitHub account Chrome"), body.index(CLICKS))

    def test_signed_in_as_claudes_account(self):
        self.assertIn("signed in as Claude's GitHub account", self.body())

    def test_accepts_either_login_in_chrome(self):
        # Decision 6 (a): Chrome signed in as Claude's GitHub account or the human is fine;
        # a sign-in is asked for only if it's neither.
        body = self.body()
        self.assertRegex(body, r"[Ee]ither Claude's GitHub account or the human")
        self.assertRegex(body, r"[Oo]nly if it's neither[^.]*sign in")

    def test_no_account_path_is_covered_in_5g_itself(self):
        body = self.body()
        self.assertRegex(body, r"said no to the account[^.]*login must be the human's[^.]*owns the board")
        # 5g never asks a human with no account to sign in as one.
        self.assertNotRegex(body, r"sign in to github\.com in Chrome as the account")

    def test_notes_the_account_that_switched_it_on(self):
        # Decision 6: "a plus name the account".
        self.assertRegex(self.body(), r"[Nn]ote which GitHub account switched it on")

    def test_confirms_it_is_on(self):
        self.assertRegex(self.body(), r"(reload|look again)[^.]*\bon\b")

    def test_human_gets_one_step_only_on_failure(self):
        body = self.body()
        self.assertRegex(body, r"[Oo]nly if [^.]*fails")
        self.assertIn("one clear step", body)
        self.assertLess(body.index(CLICKS), body.index("one clear step"))

    def test_no_account_path_still_switches_it_on(self):
        # A human who said no to the account still gets the workflow switched on by Claude.
        body = steps()["5"]
        self.assertRegex(body, r"says no[^.]*step 5%s" % WORKFLOW_STEP)


class HandOffNamesTheAccount(unittest.TestCase):
    def last_step(self):
        s = steps()
        return s[max(s, key=lambda k: (int(re.match(r"\d+", k).group()), k))]

    def test_hand_off_names_the_account(self):
        body = self.last_step()
        self.assertRegex(body, r"hand-off[^.]*names the GitHub account that switched on "
                               r"\"Item closed\" in step 5%s" % WORKFLOW_STEP)

    def test_hand_off_comes_after_the_deny_list_check(self):
        body = self.last_step()
        self.assertLess(body.index("`git push origin main`"), body.index("hand-off"))


class NoManualStepLeft(unittest.TestCase):
    def test_no_sentence_asks_the_human_to_switch_it_on(self):
        for sentence in re.split(r"(?<=[.!?])\s", flat(read(SETUP))):
            if "Item closed" in sentence and re.search(r"(ask|tell) the human", sentence):
                self.assertRegex(sentence, r"[Oo]nly if|fails", sentence)

    def test_general_words_not_a_login(self):
        text = read(SETUP).lower()
        self.assertNotIn("hornof", text)


if __name__ == "__main__":
    unittest.main()
