"""Tests for issue #145: apb keeps a list of projects in use. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts

The playbook's rule "An apb change reaches the projects already using apb in the same round" said to
apply a change to "every project in use", but apb kept no list of them. The orchestrator searched the
Mac's folders for an apb-style CLAUDE.md, which on PR #144 also found tahoe-tracker, a project no
longer in use (2026-11-21). Now one list, `docs/projects.md`, names each project's repo and whether
it is in use, and the rule reads it: a change goes to every project marked in use, and to no other.

Issue #148 (review of PR #147, Decision 3 and Note 2): apb-setup adds each new project's row, and the
list also says where each project's checkout is on the Mac, or says to clone the repo when there is
none, so the orchestrator never searches folders to find a checkout.
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIST = "docs/projects.md"
RULE = "An apb change reaches the projects already using apb in the same round."
SETUP = "skills/apb-setup/SKILL.md"
REPO = re.compile(r"^[\w.-]+/[\w.-]+$")
# A checkout cell: a folder in backticks, from the home folder (~/...) or absolute, or the word clone.
CHECKOUT = re.compile(r"^(`(~|/)[^`\s]*`|clone)$")


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def section(text, name):
    m = re.search(r"^## %s\n(.*?)(?=^## |\Z)" % re.escape(name), text, re.S | re.M)
    return m.group(1) if m else ""


def flat(text):
    return " ".join(text.replace("*", "").split())


def cells(line):
    """A table row's cells, stripped, or None when the line is not a table row."""
    line = line.strip()
    if not (line.startswith("|") and line.endswith("|")) or len(line) < 2:
        return None
    return [c.strip() for c in line[1:-1].split("|")]


def rows(text):
    """The list's entries as (repo, in use, checkout) triples, in order; checkout is None when the
    row has no third cell. Header and divider rows are skipped."""
    out = []
    for line in text.splitlines():
        c = cells(line)
        if not c or len(c) < 2:
            continue
        repo = c[0].strip("`")
        if REPO.match(repo):
            out.append((repo, c[1], c[2] if len(c) > 2 else None))
    return out


def the_rule(playbook):
    """The playbook bullet that starts with the projects rule, flattened, or None."""
    items = [flat(b)[2:] for b in re.split(r"\n(?=- )", "\n" + playbook) if b.startswith("- ")]
    found = [b for b in items if b.startswith(RULE)]
    return found[0] if len(found) == 1 else None


class TheList(unittest.TestCase):
    def setUp(self):
        self.text = read(LIST)
        self.rows = rows(self.text)

    def test_starting_entries(self):
        entries = {r: (u, c) for r, u, c in self.rows}
        self.assertEqual(entries.get("hornof/sketch-gallery"), ("yes", "`~/src/claude/sketch-gallery`"))
        self.assertEqual(entries.get("hornof/tahoe-tracker"), ("no", "`~/src/claude/tahoe-tracker`"))

    def test_every_row_is_well_formed(self):
        self.assertGreaterEqual(len(self.rows), 2)
        for repo, in_use, checkout in self.rows:
            self.assertRegex(repo, REPO)
            self.assertIn(in_use, ("yes", "no"), repo)
            # Every row gives a checkout or says to clone: nothing is left for a folder search.
            self.assertIsNotNone(checkout, repo)
            self.assertRegex(checkout, CHECKOUT, repo)

    def test_each_repo_once(self):
        repos = [r.lower() for r, _, _ in self.rows]
        self.assertEqual(len(repos), len(set(repos)))

    def test_each_checkout_once(self):
        # Two projects in one folder would send a change to the wrong repo.
        folders = [c for _, _, c in self.rows if c != "clone"]
        self.assertEqual(len(folders), len(set(folders)))

    def test_columns_are_named(self):
        self.assertRegex(self.text, r"(?m)^\|\s*Repo\s*\|\s*In use\s*\|\s*Checkout\s*\|\s*$")

    def test_says_what_checkout_and_clone_mean(self):
        text = flat(self.text)
        self.assertIn("Checkout", text)
        self.assertIn("`~` is the home folder", text)
        self.assertRegex(text, r"\"clone\"[^.]*no checkout")
        self.assertIn("gh repo clone", text)
        # A clone made later is written back into the row, so the next change finds it.
        self.assertRegex(text, r"(write|put) [^.]*folder[^.]*(in|into) the row")

    def test_says_setup_adds_the_row(self):
        self.assertIn("apb-setup", flat(self.text))

    def test_says_how_to_add_and_retire_a_project(self):
        text = flat(self.text)
        # Adding is a new row; a project no longer in use keeps its row with "no", so it is never
        # found again by a search and taken for one in use.
        self.assertIn("add a row", text)
        self.assertIn('change its In use to "no"', text)
        self.assertIn("keep the row", text)
        # The list is the only source: no search of the computer.
        self.assertIn("never by searching", text)

    def test_points_back_at_the_rule(self):
        self.assertIn(RULE.rstrip("."), flat(self.text))


class OnePlace(unittest.TestCase):
    """The list lives in one place: no other file in the repo holds a row of it."""

    def test_no_other_file_lists_projects(self):
        others = []
        for top in ("docs", "agents", "skills", "templates", "hooks", "scripts"):
            for folder, _, files in os.walk(os.path.join(ROOT, top)):
                for name in files:
                    path = os.path.join(folder, name)
                    rel = os.path.relpath(path, ROOT)
                    if rel == LIST or not name.endswith(".md"):
                        continue
                    with open(path, encoding="utf-8") as f:
                        if any(r == "hornof/sketch-gallery" for r, _, _ in rows(f.read())):
                            others.append(rel)
        self.assertEqual(others, [])


class Playbook(unittest.TestCase):
    def setUp(self):
        self.rule = the_rule(section(read("docs/playbook.md"), "Helping the human keep track"))

    def test_rule_names_the_list(self):
        self.assertIsNotNone(self.rule, "exactly one bullet starts with the projects rule")
        self.assertIn("`%s`" % LIST, self.rule)

    def test_rule_reads_the_list_and_nothing_else(self):
        self.assertIn("every project marked in use", self.rule)
        self.assertIn("and to no other", self.rule)
        self.assertIn("never by searching", self.rule)
        # The old wording, which had no list behind it, is gone.
        self.assertNotIn("every project in use", self.rule)

    def test_rule_says_the_list_is_where_projects_change(self):
        self.assertIn("by editing that list", self.rule)

    def test_rule_says_how_to_reach_each_checkout(self):
        self.assertIn("Checkout", self.rule)
        self.assertRegex(self.rule, r"\"clone\"[^.]*gh repo clone")
        # A new clone is recorded in the list, so the next change finds it there.
        self.assertRegex(self.rule, r"clone[^.]*(write|put)[^.]*folder[^.]*(in|into) the row")
        # Still no search: the checkout comes from the list.
        self.assertRegex(self.rule, r"never by searching the computer's folders")

    def test_rule_says_setup_adds_new_projects(self):
        self.assertIn("apb-setup", self.rule)

    def test_rule_keeps_the_projects_line(self):
        # The Stop hook still reads the Projects line; the list changes which projects, not the line.
        self.assertIn("`Projects: <links to those PRs>`", self.rule)
        self.assertIn("`Projects: none`", self.rule)


class Oracle(unittest.TestCase):
    """The helpers find what they should and nothing else (guards against a vacuous pass)."""

    def test_rows(self):
        table = ("| Repo | In use | Checkout |\n|---|---|---|\n| a/b | yes | `~/src/b` |\n"
                 "| `c/d` | no | clone |\n| e/f | yes |\nnot | a/x | row\n")
        self.assertEqual(rows(table), [("a/b", "yes", "`~/src/b`"), ("c/d", "no", "clone"),
                                       ("e/f", "yes", None)])

    def test_checkout_pattern(self):
        for good in ("`~/src/claude/x`", "`/Users/me/x`", "clone"):
            self.assertRegex(good, CHECKOUT)
        for bad in ("", "none", "~/src/x", "`src/x`", "`~/a b`", "clone it", "-"):
            self.assertNotRegex(bad, CHECKOUT)

    def test_rule_inside_another_bullet_is_not_found(self):
        self.assertIsNone(the_rule("- Other text. " + RULE + "\n"))

    def test_rule_found_with_wrapped_lines(self):
        self.assertEqual(the_rule("- **" + RULE + "**\n  more\n- next\n"), RULE + " more")


class Setup(unittest.TestCase):
    """apb-setup adds the new project's row, with its checkout, as one of its steps."""

    def setUp(self):
        self.text = flat(read(SETUP))

    def test_setup_names_the_list(self):
        self.assertIn("`%s`" % LIST, self.text)

    def test_row_has_yes_and_the_project_folder(self):
        sentence = next((x for x in re.split(r"(?<=[.!?])\s", self.text) if "add a row to `%s`" % LIST in x), "")
        self.assertIn('"yes"', sentence)
        self.assertIn("Checkout", sentence)
        self.assertRegex(sentence, r"project folder[^.]*step 0")

    def test_finds_the_apb_repo_without_naming_a_login(self):
        # The apb repo comes from the plugin's own manifest, not a hard-coded owner.
        self.assertRegex(self.text, r"`repository`[^.]*`[^`]*\.claude-plugin/plugin\.json`")

    def test_row_goes_in_a_pr_and_the_hand_off_links_it(self):
        after = self.text[self.text.index("`%s`" % LIST):]
        self.assertIn("gh pr create", after)
        self.assertRegex(self.text, r"hand-off[^.]*links? the apb PR")

    def test_every_clone_command_names_the_clone(self):
        # Review Fix 1 on PR #151: the session stays in the project folder, so a bare git or gh
        # command would act on the project's repo. Each command names the clone or the apb repo.
        for cmd in ("`gh repo clone <apb repo> <clone>`", "`git -C <clone> switch -c <branch>`",
                    "`git -C <clone> push -u origin <branch>`",
                    "`gh pr create -R <apb repo> --head <branch>`"):
            self.assertIn(cmd, self.text)
        self.assertRegex(self.text, r"`git -C <clone> commit [^`]*`")
        step = self.text[self.text.index("apb's list of projects"):self.text.index("setup-done")]
        for bare in re.findall(r"`((?:git|gh) [^`]*)`", step):
            if bare.startswith("git "):
                self.assertTrue(bare.startswith("git -C <clone> "), bare)
            if bare.startswith("gh pr "):
                self.assertIn("-R <apb repo>", bare)

    def test_checks_push_access_before_the_clone(self):
        # Review Fix 2: anyone else's account can't push to the apb repo; check before cloning.
        check = "`gh api repos/<apb repo> --jq .permissions.push`"
        self.assertIn(check, self.text)
        self.assertLess(self.text.index(check), self.text.index("`gh repo clone <apb repo>"))
        self.assertIn("If Claude's account can't push to the apb repo", self.text)

    def test_hand_off_covers_no_apb_pr(self):
        # Review Fix 3: the step can skip, so the hand-off says why when there is no PR.
        self.assertRegex(self.text, r"hand-off[^.]*links the apb PR that adds the project's row, "
                                    r"or says the row was already there, or why it wasn't added")

    def test_row_comes_after_the_deny_list_check(self):
        self.assertLess(self.text.index("`git push origin main`"), self.text.index("`%s`" % LIST))


class Readme(unittest.TestCase):
    """Review Fix 4 on PR #151: the README's row for the list mentions the checkout."""

    def test_readme_row_mentions_checkout(self):
        row = [l for l in read("README.md").splitlines() if l.startswith("| `%s`" % LIST)]
        self.assertEqual(len(row), 1)
        self.assertIn("whether each is in use", row[0])
        self.assertIn("checkout", row[0])


if __name__ == "__main__":
    unittest.main()
