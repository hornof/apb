"""Tests for board-view.py, which gives a new board the kanban view. Run from the repo root:
python3 -m unittest discover -s scripts -t scripts -p test_board_view.py

Done when (issue #93):
- a fresh `/apb-setup` run ends with the board showing a "kanban" board view grouped by Status,
  with no manual step;
- it works for user-owned boards with the human's `gh` login;
- the hand-off names what (if anything) still needs a manual step.

GitHub is faked by `FakeGitHub`, a small in-memory board that answers the four calls the script
makes, the way GitHub answered them on a throwaway board on 2026-11-20: the GraphQL read, the
`updateProjectV2Field` and `deleteProjectV2View` mutations, and the REST create-view endpoint. The
oracle is the fake board's state after the run, checked against the target (apb's own board): one
view, "kanban", board layout, columns from Status = Todo / In progress / In review / Done. The
starting boards are random, so the rules hold for more than the one board GitHub makes today.
"""
import copy
import importlib.util
import io
import json
import os
import random
import subprocess
import unittest
from contextlib import redirect_stderr, redirect_stdout

HERE = os.path.dirname(__file__)
spec = importlib.util.spec_from_file_location("board_view", os.path.join(HERE, "board-view.py"))
bv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bv)

TARGET = ["Todo", "In progress", "In review", "Done"]
# What GitHub gives a new board today (seen on the throwaway board).
FRESH_OPTIONS = [
    {"id": "f75ad846", "name": "Todo", "color": "GREEN", "description": "This item hasn't been started"},
    {"id": "47fc9ee4", "name": "In Progress", "color": "YELLOW", "description": "This is actively being worked on"},
    {"id": "98236657", "name": "Done", "color": "PURPLE", "description": "This has been completed"},
]
FRESH_VIEWS = [{"id": "PVTV_1", "name": "View 1", "layout": "TABLE_LAYOUT", "group": []}]


class FakeGitHub:
    """An in-memory board. `refuse` names calls that fail the way GitHub fails them: "create" (the
    REST endpoint refuses the token), "delete", "update". Records every call with its env and
    timeout, and every write."""

    def __init__(self, owner_type="User", options=None, views=None, refuse=()):
        self.owner = "someone"
        self.number = 7
        self.owner_type = owner_type
        self.status_db_id = 447860978
        self.options = copy.deepcopy(FRESH_OPTIONS if options is None else options)
        self.views = copy.deepcopy(FRESH_VIEWS if views is None else views)
        self.refuse = set(refuse)
        self.calls = []
        self.writes = []
        self.next_id = 100
        self.min_views = len(self.views)

    def _new_id(self, prefix):
        self.next_id += 1
        return "%s_%d" % (prefix, self.next_id)

    def _read(self):
        nodes = [{"name": "Title"}, {
            "id": "PVTSSF_status", "databaseId": self.status_db_id, "name": "Status",
            "options": copy.deepcopy(self.options)}, {}]
        views = [{"id": v["id"], "name": v["name"], "layout": v["layout"],
                  "verticalGroupByFields": {"nodes": [{"name": g} for g in v["group"]]}}
                 for v in self.views]
        project = {"id": "PVT_x", "url": "https://github.com/users/someone/projects/7",
                   "fields": {"nodes": nodes}, "views": {"nodes": views}}
        return {"data": {"repositoryOwner": {"__typename": self.owner_type, "projectV2": project}}}

    def __call__(self, args, input=None, env=None, timeout=None):
        self.calls.append({"args": list(args), "env": dict(env or {}), "timeout": timeout})
        assert args[:2] == ["gh", "api"], args
        body = json.loads(input) if input else {}
        if args[2:] == ["graphql", "--input", "-"]:
            query, var = body["query"], body.get("variables", {})
            if "updateProjectV2Field" in query:
                self.writes.append("update")
                if "update" in self.refuse:
                    return self._fail("Resource not accessible")
                assert var["fieldId"] == "PVTSSF_status"
                old = {o["id"] for o in self.options}
                new = []
                for o in var["options"]:
                    assert set(o) <= {"id", "name", "color", "description"}, o
                    assert "color" in o and "description" in o, o
                    if "id" in o:
                        assert o["id"] in old, o
                    new.append(dict(o, id=o.get("id") or self._new_id("opt")))
                self.options = new
                return self._ok({"data": {"updateProjectV2Field": {"clientMutationId": None}}})
            if "deleteProjectV2View" in query:
                self.writes.append("delete")
                if "delete" in self.refuse:
                    return self._fail("Something went wrong")
                ids = [v["id"] for v in self.views]
                assert var["viewId"] in ids
                self.views = [v for v in self.views if v["id"] != var["viewId"]]
                self.min_views = min(self.min_views, len(self.views))
                return self._ok({"data": {"deleteProjectV2View": {"clientMutationId": None}}})
            assert "projectV2(number:" in query, query
            assert var["owner"] == self.owner, var
            if var["number"] != self.number:
                return self._ok({"data": {"repositoryOwner": {"__typename": self.owner_type, "projectV2": None}},
                                 "errors": [{"message": "Could not resolve to a ProjectV2 with the number %d." % var["number"]}]})
            return self._ok(self._read())
        # REST: create a view.
        assert args[2:5] == ["-X", "POST", "%s/%s/projectsV2/%d/views" % (
            "users" if self.owner_type == "User" else "orgs", self.owner, self.number)], args
        assert args[5:] == ["--input", "-"], args
        self.writes.append("create")
        if "create" in self.refuse:
            return self._fail("gh: Resource not accessible by personal access token (HTTP 403)")
        assert body["layout"] == "board"
        assert body["vertical_group_by"] == [self.status_db_id]
        self.views.append({"id": self._new_id("PVTV"), "name": body["name"],
                           "layout": "BOARD_LAYOUT", "group": ["Status"]})
        return self._ok({"id": 1, "name": body["name"], "layout": "board"})

    def _ok(self, data):
        return subprocess.CompletedProcess([], 0, json.dumps(data), "")

    def _fail(self, msg):
        return subprocess.CompletedProcess([], 1, "", msg)


def go(fake, argv=None):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = bv.main(argv or [fake.owner, str(fake.number)], run=fake)
    return code, out.getvalue(), err.getvalue()


class tokens_set:
    """Sets the named token variables for the length of a `with` block, then puts them back."""

    def __init__(self, names):
        self.names = names

    def __enter__(self):
        self.old = {n: os.environ.get(n) for n in self.names}
        for n in self.names:
            os.environ[n] = "ghp_claudes"

    def __exit__(self, *exc):
        for n, v in self.old.items():
            if v is None:
                os.environ.pop(n, None)
            else:
                os.environ[n] = v


def kanbans(fake):
    return [v for v in fake.views
            if v["name"] == "kanban" and v["layout"] == "BOARD_LAYOUT" and v["group"] == ["Status"]]


def random_board(rng):
    """A random starting board: Status options in any mix of the target names (any case), GitHub's
    defaults and extras; views that are GitHub defaults ("View 3"), human-made, or already kanban."""
    pool = TARGET + ["In Progress", "TODO", "done", "Backlog", "Blocked"]
    names = []
    for n in rng.sample(pool, rng.randint(0, len(pool))):
        if n.lower() not in {x.lower() for x in names}:
            names.append(n)
    options = [{"id": "o%d" % i, "name": n, "color": rng.choice(["GRAY", "RED", "PINK"]),
                "description": rng.choice(["", "words"])} for i, n in enumerate(names)]
    views = []
    for i in range(rng.randint(1, 4)):
        kind = rng.choice(["default", "default", "human", "kanban", "kanban-table"])
        if kind == "kanban" and any(x["layout"] == "BOARD_LAYOUT" and x["name"] == "kanban" for x in views):
            kind = "default"  # at most one finished kanban view to start with
        if kind == "default":
            v = {"name": "View %d" % (i + 1), "layout": rng.choice(["TABLE_LAYOUT", "BOARD_LAYOUT"]), "group": []}
        elif kind == "human":
            v = {"name": rng.choice(["Roadmap", "My table", "bugs"]), "layout": "TABLE_LAYOUT", "group": []}
        elif kind == "kanban":
            v = {"name": "kanban", "layout": "BOARD_LAYOUT", "group": ["Status"]}
        else:
            v = {"name": "kanban", "layout": "TABLE_LAYOUT", "group": []}
        v["id"] = "PVTV_r%d" % i
        views.append(v)
    return options, views


class FreshBoard(unittest.TestCase):
    """The board GitHub makes today, as setup's step 3 leaves it."""

    def setUp(self):
        self.fake = FakeGitHub()
        self.code, self.out, self.err = go(self.fake)

    def test_ends_with_one_kanban_view_grouped_by_status(self):
        self.assertEqual(self.code, 0, self.err)
        self.assertEqual(len(kanbans(self.fake)), 1)
        self.assertEqual([v["name"] for v in self.fake.views], ["kanban"])

    def test_columns_are_the_four(self):
        self.assertEqual([o["name"] for o in self.fake.options], TARGET)

    def test_columns_have_apbs_colours(self):
        # As on apb's own board.
        self.assertEqual([o["color"] for o in self.fake.options], ["GRAY", "YELLOW", "BLUE", "GREEN"])

    def test_keeps_the_ids_of_options_it_renames(self):
        # "In Progress" becomes "In progress" in place, so a card already in it stays in it.
        ids = {o["name"].lower(): o["id"] for o in self.fake.options}
        for o in FRESH_OPTIONS:
            self.assertEqual(ids[o["name"].lower()], o["id"])

    def test_never_leaves_the_board_without_a_view(self):
        self.assertGreaterEqual(self.fake.min_views, 1)
        self.assertLess(self.fake.writes.index("create"), self.fake.writes.index("delete"))

    def test_says_nothing_is_left_by_hand(self):
        self.assertIn("Manual steps: none", self.out)

    def test_names_the_view(self):
        self.assertIn("kanban", self.out)


class RightNamesWrongColours(unittest.TestCase):
    def test_recolours_in_place(self):
        options = [{"id": "o%d" % i, "name": n, "color": "RED", "description": ""} for i, n in enumerate(TARGET)]
        fake = FakeGitHub(options=options)
        go(fake)
        self.assertEqual([o["color"] for o in fake.options], ["GRAY", "YELLOW", "BLUE", "GREEN"])
        self.assertEqual([o["id"] for o in fake.options], ["o0", "o1", "o2", "o3"])


class AsTheHumansLogin(unittest.TestCase):
    def test_every_call_drops_both_token_variables(self):
        # GitHub refuses the views endpoint on a user-owned board from fine-grained and App tokens,
        # so the script runs `gh` as the human's own stored login even if GH_TOKEN or GITHUB_TOKEN
        # is set (`gh` reads either one ahead of its stored login).
        for names in (["GH_TOKEN"], ["GITHUB_TOKEN"], ["GH_TOKEN", "GITHUB_TOKEN"]):
            fake = FakeGitHub()
            with tokens_set(names):
                go(fake)
            self.assertTrue(fake.calls)
            # Compare only the names, so a failure never prints the environment (it holds secrets).
            for call in fake.calls:
                found = sorted({"GH_TOKEN", "GITHUB_TOKEN"} & set(call["env"]))
                self.assertEqual(found, [], "set: %s" % names)
                self.assertTrue("PATH" in call["env"])

    def test_every_call_has_a_time_limit(self):
        fake = FakeGitHub()
        go(fake)
        for call in fake.calls:
            self.assertIsNotNone(call["timeout"])
            self.assertGreater(call["timeout"], 0)

    def test_org_owned_board_uses_the_org_endpoint(self):
        fake = FakeGitHub(owner_type="Organization")
        code, _, err = go(fake)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(kanbans(fake)), 1)


class RandomBoards(unittest.TestCase):
    """Property checks over random starting boards."""

    def test_rules_hold(self):
        rng = random.Random(93)
        for trial in range(300):
            options, views = random_board(rng)
            fake = FakeGitHub(options=options, views=views)
            code, out, err = go(fake)
            msg = "trial %d: %r %r\n%s%s" % (trial, options, views, out, err)
            self.assertEqual(code, 0, msg)
            # Exactly one kanban board view grouped by Status.
            self.assertEqual(len(kanbans(fake)), 1, msg)
            # The four columns come first, in order; nothing the board had is dropped.
            names = [o["name"] for o in fake.options]
            self.assertEqual(names[:4], TARGET, msg)
            after = {o["id"] for o in fake.options}
            for o in options:
                self.assertIn(o["id"], after, msg)
            # GitHub's default views are gone; views a person made stay.
            left = [v["name"] for v in fake.views]
            for v in views:
                if v["name"].startswith("View "):
                    self.assertNotIn(v["id"], [x["id"] for x in fake.views], msg)
                elif v["name"] != "kanban" or v["layout"] != "BOARD_LAYOUT":
                    self.assertIn(v["id"], [x["id"] for x in fake.views], msg)
            self.assertFalse([n for n in left if n.startswith("View ")], msg)
            self.assertGreaterEqual(fake.min_views, 1, msg)
            # Views other than kanban that a person made are named for the hand-off.
            others = [v["name"] for v in fake.views if v not in kanbans(fake)]
            for n in others:
                self.assertIn(n, out, msg)

    def test_second_run_writes_nothing(self):
        rng = random.Random(930)
        for trial in range(100):
            options, views = random_board(rng)
            fake = FakeGitHub(options=options, views=views)
            go(fake)
            fake.writes.clear()
            code, out, _ = go(fake)
            self.assertEqual(code, 0)
            self.assertEqual(fake.writes, [], "trial %d" % trial)


class WhenGitHubRefuses(unittest.TestCase):
    def test_create_refused_names_the_manual_step(self):
        fake = FakeGitHub(refuse={"create"})
        code, out, err = go(fake)
        self.assertNotEqual(code, 0)
        text = out + err
        self.assertIn("Manual step", text)
        self.assertIn("kanban", text)
        self.assertIn("Status", text)
        self.assertIn("https://github.com/users/someone/projects/7", text)
        self.assertIn("HTTP 403", text)
        # Without a kanban view, the default view stays: never a board with no view.
        self.assertEqual([v["name"] for v in fake.views], ["View 1"])
        self.assertNotIn("delete", fake.writes)

    def test_delete_refused_still_succeeds_and_names_the_leftover(self):
        fake = FakeGitHub(refuse={"delete"})
        code, out, err = go(fake)
        self.assertEqual(code, 0, err)
        self.assertEqual(len(kanbans(fake)), 1)
        self.assertIn("Manual step", out)
        self.assertIn("View 1", out)
        self.assertNotIn("Manual steps: none", out)

    def test_update_refused_names_the_columns(self):
        fake = FakeGitHub(refuse={"update"})
        code, out, err = go(fake)
        text = out + err
        self.assertIn("Manual step", text)
        self.assertIn("In review", text)
        self.assertNotIn("Manual steps: none", text)

    def test_first_read_refused_puts_githubs_error_in_the_manual_step(self):
        def refuse(args, input=None, env=None, timeout=None):
            return subprocess.CompletedProcess(args, 1, "", "HTTP 401: Bad credentials")
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = bv.main(["someone", "7"], run=refuse)
        self.assertNotEqual(code, 0)
        steps = [l for l in out.getvalue().splitlines() if l.startswith("Manual step:")]
        self.assertEqual(len(steps), 1, out.getvalue())
        self.assertIn("HTTP 401: Bad credentials", steps[0])
        self.assertRegex(steps[0], r"(?i)fix\b.*\brun\b.*\bagain")

    def test_board_not_found_is_in_the_manual_step(self):
        fake = FakeGitHub()
        code, out, _ = go(fake, ["someone", "99"])
        self.assertNotEqual(code, 0)
        line = [l for l in out.splitlines() if l.startswith("Manual step:")][0]
        self.assertIn("Could not resolve to a ProjectV2 with the number 99", line)
        self.assertRegex(line, r"(?i)fix\b.*\brun\b.*\bagain")

    def test_gh_that_hangs_is_reported(self):
        def hang(args, input=None, env=None, timeout=None):
            raise subprocess.TimeoutExpired(args, timeout)
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = bv.main(["someone", "7"], run=hang)
        self.assertNotEqual(code, 0)
        self.assertIn("timed out", (out.getvalue() + err.getvalue()).lower())

    def test_bad_arguments(self):
        for argv in ([], ["someone"], ["someone", "seven"]):
            out, err = io.StringIO(), io.StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                code = bv.main(argv, run=FakeGitHub())
            self.assertEqual(code, 2, argv)
            self.assertIn("usage", err.getvalue().lower())


class DefaultRun(unittest.TestCase):
    def test_default_run_drops_gh_token_and_has_a_limit(self):
        seen = {}

        def fake_subprocess_run(args, **kw):
            seen.update(kw)
            return subprocess.CompletedProcess(args, 0, "{}", "")
        real = bv.subprocess.run
        bv.subprocess.run = fake_subprocess_run
        try:
            with tokens_set(["GH_TOKEN", "GITHUB_TOKEN"]):
                bv.gh(["gh", "api", "user"])
        finally:
            bv.subprocess.run = real
        self.assertGreater(seen["timeout"], 0)
        self.assertEqual(sorted({"GH_TOKEN", "GITHUB_TOKEN"} & set(seen["env"])), [])


if __name__ == "__main__":
    unittest.main()
