"""Tests for human-turn.py. Run from the repo root: python3 -m unittest scripts/test_human_turn.py

The rule (is a card or PR assigned to the human still waiting on them?) is checked with hand-made
cases, and with random boards against a second, simpler way of getting the same answer. The whole
script is then run for real, with a fake `gh` on the PATH that answers from the same random boards,
to check what it asks GitHub, what it prints and how it exits.
"""
import importlib.util
import json
import os
import random
import stat
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "human-turn.py")

spec = importlib.util.spec_from_file_location("human_turn", SCRIPT)
ht = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ht)

HUMAN, CLAUDE = "hum", "claude-bot"
SIGN = "\n\n— Claude (orchestrator)"


def issue(n, state="OPEN", last=None, prs=()):
    """last: (author, body) of the card's last comment, or None. prs: PRs that close it."""
    return {"kind": "issue", "number": n, "state": state,
            "last_comment": None if last is None else {"author": last[0], "body": last[1]},
            "prs": [dict(p) for p in prs]}


def pr(n, state="OPEN", unresolved=0):
    return {"kind": "pr", "number": n, "state": state, "unresolved": unresolved}


class Asks(unittest.TestCase):
    def test_a_question_asks(self):
        self.assertTrue(ht.asks("Which name should the button use?" + SIGN))
        self.assertTrue(ht.asks("The plan is below.\n\nWaiting for your go." + SIGN))

    def test_a_note_does_not_ask(self):
        self.assertFalse(ht.asks("Build took 14 minutes, review 6." + SIGN))
        self.assertFalse(ht.asks(""))
        self.assertFalse(ht.asks(None))

    def test_a_quoted_question_does_not_ask(self):
        self.assertFalse(ht.asks("> Which one?\n\nDone, as you said." + SIGN))


class Verdict(unittest.TestCase):
    def v(self, item):
        return ht.verdict(item, CLAUDE)[0]

    def test_an_open_pr_waits(self):
        self.assertEqual(self.v(pr(7)), "waiting")

    def test_a_merged_pr_with_an_open_decision_waits(self):
        self.assertEqual(self.v(pr(7, "MERGED", unresolved=1)), "waiting")

    def test_a_merged_or_closed_pr_with_nothing_open_is_stale(self):
        self.assertEqual(self.v(pr(7, "MERGED")), "stale")
        self.assertEqual(self.v(pr(7, "CLOSED")), "stale")

    def test_a_card_with_its_pr_open_waits(self):
        self.assertEqual(self.v(issue(3, prs=[pr(7)])), "waiting")

    def test_a_card_whose_pr_has_an_open_decision_waits(self):
        self.assertEqual(self.v(issue(3, "CLOSED", prs=[pr(7, "MERGED", unresolved=2)])), "waiting")

    def test_a_card_closed_by_its_merge_is_stale(self):
        self.assertEqual(self.v(issue(3, "CLOSED", prs=[pr(7, "MERGED")])), "stale")

    def test_a_question_from_claude_on_the_card_waits(self):
        self.assertEqual(self.v(issue(3, last=(CLAUDE, "Which one, a or b?"))), "waiting")
        self.assertEqual(self.v(issue(3, last=(HUMAN, "Which one, a or b?" + SIGN))), "waiting")  # signed

    def test_the_humans_answer_on_the_card_makes_it_stale(self):
        self.assertEqual(self.v(issue(3, last=(HUMAN, "b, please. Why not?"))), "stale")

    def test_a_note_from_claude_is_not_a_question(self):
        self.assertEqual(self.v(issue(3, last=(CLAUDE, "Build took 14 minutes."))), "stale")

    def test_a_question_on_a_closed_card_is_stale(self):
        self.assertEqual(self.v(issue(3, "CLOSED", last=(CLAUDE, "Which one?"))), "stale")

    def test_a_card_with_nothing_on_it_is_stale(self):
        self.assertEqual(self.v(issue(3)), "stale")

    def test_every_verdict_gives_a_reason(self):
        for item in [pr(7), pr(7, "MERGED"), issue(3), issue(3, prs=[pr(7)]), issue(3, last=(CLAUDE, "Ok?"))]:
            kind, reason = ht.verdict(item, CLAUDE)
            self.assertTrue(reason.strip())


class Report(unittest.TestCase):
    def test_stale_lines_name_the_command_and_exit_1(self):
        lines, stale = ht.report([issue(3, "CLOSED", prs=[pr(7, "MERGED")]), pr(7, "MERGED")], HUMAN, "o/n", CLAUDE)
        self.assertEqual(stale, 2)
        self.assertIn("gh issue edit 3 --repo o/n --remove-assignee hum", lines[0])
        self.assertIn("gh pr edit 7 --repo o/n --remove-assignee hum", lines[1])
        self.assertTrue(all(line.startswith("Stale") for line in lines))

    def test_waiting_lines(self):
        lines, stale = ht.report([issue(3, prs=[pr(7)])], HUMAN, "o/n", CLAUDE)
        self.assertEqual(stale, 0)
        self.assertTrue(lines[0].startswith("Waiting on hum: card #3"))
        self.assertIn("PR #7", lines[0])

    def test_nothing_assigned(self):
        self.assertEqual(ht.report([], HUMAN, "o/n", CLAUDE), (["Nothing is assigned to hum in o/n."], 0))


# ---------------------------------------------------------------- random boards, and an oracle

QUESTIONS = ["Which one, a or b?", "Ok to drop the old page?\n", "Plan below.\n\nWaiting for your go."]
NOTES = ["Build took 14 minutes.", "> Which one?\n\nDone.", "Started on b.", ""]


def random_board(rng):
    """A fake GitHub board: PRs and cards, some assigned to the human, some to others, some to nobody.
    Each comment carries "asks": whether the generator wrote a question, so the oracle needn't parse it."""
    prs = {}
    for n in range(100, 100 + rng.randint(0, 6)):
        prs[n] = {"number": n, "state": rng.choice(["OPEN", "MERGED", "CLOSED"]),
                  "threads": [rng.random() < 0.6 for _ in range(rng.randint(0, 3))],
                  "assignees": rng.sample([HUMAN, CLAUDE, "other"], rng.randint(0, 2))}
    issues = {}
    for n in range(1, 1 + rng.randint(0, 6)):
        comments = []
        for _ in range(rng.randint(0, 3)):
            claude = rng.random() < 0.5
            asks = rng.random() < 0.5
            body = rng.choice(QUESTIONS if asks else NOTES)
            signed = claude and rng.random() < 0.3
            author = HUMAN if (signed or not claude) else CLAUDE
            comments.append({"author": author, "body": body + (SIGN if signed else ""), "claude": claude,
                             "asks": asks})
        issues[n] = {"number": n, "state": rng.choice(["OPEN", "CLOSED"]), "comments": comments,
                     "closed_by": rng.sample(sorted(prs), rng.randint(0, min(2, len(prs)))),
                     "assignees": rng.sample([HUMAN, CLAUDE, "other"], rng.randint(0, 2))}
    return {"prs": prs, "issues": issues}


def oracle(board, login):
    """{("issue"|"pr", number): "waiting" | "stale"} for everything assigned to login, straight from the board."""
    def pr_open(p):
        return p["state"] == "OPEN" or not all(p["threads"])
    out = {}
    for p in board["prs"].values():
        if login in p["assignees"]:
            out[("pr", p["number"])] = "waiting" if pr_open(p) else "stale"
    for i in board["issues"].values():
        if login in i["assignees"]:
            last = i["comments"][-1] if i["comments"] else None
            question = i["state"] == "OPEN" and last is not None and last["claude"] and last["asks"]
            linked = any(pr_open(board["prs"][n]) for n in i["closed_by"])
            out[("issue", i["number"])] = "waiting" if (question or linked) else "stale"
    return out


def respond(query, variables, board):
    """GitHub's answer to the script's search, from the board: what a real `gh api graphql` prints.
    It answers only the fields the query asks for, so a query that leaves one out reads as nothing open."""
    q = variables["q"]
    assert "is:" not in q, "the search must not filter on state: a closed card can still hold the flag"
    terms = dict(t.split(":", 1) for t in q.split() if ":" in t)
    login, repo = terms.get("assignee"), terms.get("repo")
    nodes = []
    if repo != "o/n":
        return {"data": {"search": {"nodes": []}}}

    def threads(p):
        if "reviewThreads(first: 100) { nodes { isResolved } }" not in query:
            return {"nodes": []}
        return {"nodes": [{"isResolved": r} for r in p["threads"]]}

    for p in board["prs"].values():
        if login in p["assignees"]:
            nodes.append({"__typename": "PullRequest", "number": p["number"], "state": p["state"],
                          "reviewThreads": threads(p)})
    for i in board["issues"].values():
        if login in i["assignees"]:
            node = {"__typename": "Issue", "number": i["number"], "state": i["state"]}
            if "comments(last: 1)" in query:
                node["comments"] = {"nodes": [{"body": c["body"], "author": {"login": c["author"]}}
                                              for c in i["comments"][-1:]]}
            if "includeClosedPrs: true" in query:
                node["closedByPullRequestsReferences"] = {"nodes": [
                    {"number": n, "state": board["prs"][n]["state"], "reviewThreads": threads(board["prs"][n])}
                    for n in i["closed_by"]]}
            nodes.append(node)
    return {"data": {"search": {"nodes": nodes}}}


class Properties(unittest.TestCase):
    def test_random_boards_against_the_oracle(self):
        rng = random.Random(111)
        for _ in range(500):
            board = random_board(rng)
            got = ht.items(respond(ht.QUERY, {"q": ht.search_terms("o/n", HUMAN)}, board))
            verdicts = {(it["kind"], it["number"]): ht.verdict(it, CLAUDE)[0] for it in got}
            self.assertEqual(verdicts, oracle(board, HUMAN), json.dumps(board))

    def test_the_count_and_the_exit_agree(self):
        rng = random.Random(7)
        for _ in range(200):
            board = random_board(rng)
            got = ht.items(respond(ht.QUERY, {"q": ht.search_terms("o/n", HUMAN)}, board))
            lines, stale = ht.report(got, HUMAN, "o/n", CLAUDE)
            self.assertEqual(stale, list(oracle(board, HUMAN).values()).count("stale"))
            self.assertEqual(stale, sum(line.startswith("Stale") for line in lines))
            self.assertEqual(len(lines), max(1, len(got)))


# ---------------------------------------------------------------- the real script, with a fake gh

FAKE_GH = '''
import json, os, sys
d = os.environ["FAKE_GH_DIR"]
with open(os.path.join(d, "args"), "a") as f:
    f.write(json.dumps(sys.argv[1:]) + "\\n")
board = json.load(open(os.path.join(d, "board.json")))
board["prs"] = {int(k): v for k, v in board["prs"].items()}
if os.path.exists(os.path.join(d, "fail")):
    print("HTTP 502", file=sys.stderr); sys.exit(1)
if sys.argv[1:3] == ["repo", "view"]:
    print(json.dumps({"nameWithOwner": "o/n", "owner": {"login": "%s"}})); sys.exit(0)
args = sys.argv[3:]
fields = dict(args[i + 1].split("=", 1) for i, a in enumerate(args) if a in ("-f", "-F"))
query = fields.pop("query")
print(json.dumps(respond(query, fields, board)))
''' % HUMAN


class RealScript(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        bindir = os.path.join(self.dir, "bin")
        os.makedirs(bindir)
        gh = os.path.join(bindir, "gh")
        import inspect
        with open(gh, "w") as f:
            f.write("#!" + sys.executable + "\n" + inspect.getsource(respond) + FAKE_GH)
        os.chmod(gh, os.stat(gh).st_mode | stat.S_IEXEC)
        env = {k: v for k, v in os.environ.items() if k not in ("GH_TOKEN", "GIT_AUTHOR_NAME")}
        self.env = dict(env, PATH=bindir + os.pathsep + os.environ["PATH"], FAKE_GH_DIR=self.dir)

    def board(self, board):
        with open(os.path.join(self.dir, "board.json"), "w") as f:
            json.dump(board, f)

    def run_script(self, *extra):
        r = subprocess.run([sys.executable, SCRIPT, "--claude-login", CLAUDE] + list(extra),
                           capture_output=True, text=True, env=self.env, cwd=self.dir, timeout=30)
        return r.returncode, r.stdout.splitlines(), r.stderr

    def calls(self):
        with open(os.path.join(self.dir, "args")) as f:
            return [json.loads(line) for line in f.read().splitlines()]

    def test_a_stale_flag_exits_1_and_names_the_fix(self):
        self.board({"prs": {"7": {"number": 7, "state": "MERGED", "threads": [True], "assignees": [HUMAN]}},
                    "issues": {"3": {"number": 3, "state": "CLOSED", "comments": [], "closed_by": [7],
                                     "assignees": [HUMAN]}}})
        code, out, err = self.run_script("--repo", "o/n")
        self.assertEqual(code, 1, err)
        self.assertEqual(len(out), 2)
        self.assertTrue(any("gh issue edit 3 --repo o/n --remove-assignee hum" in line for line in out))
        self.assertTrue(any("gh pr edit 7 --repo o/n --remove-assignee hum" in line for line in out))

    def test_waiting_exits_0_and_takes_the_owner_as_the_human(self):
        self.board({"prs": {"7": {"number": 7, "state": "OPEN", "threads": [False], "assignees": [HUMAN]}},
                    "issues": {"3": {"number": 3, "state": "OPEN", "comments": [], "closed_by": [7],
                                     "assignees": [HUMAN]}}})
        code, out, err = self.run_script()
        self.assertEqual(code, 0, err)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(line.startswith("Waiting on hum") for line in out))
        self.assertEqual(self.calls()[0][:2], ["repo", "view"])

    def test_another_login(self):
        self.board({"prs": {}, "issues": {"3": {"number": 3, "state": "OPEN", "comments": [], "closed_by": [],
                                                "assignees": ["other"]}}})
        code, out, _ = self.run_script("--repo", "o/n", "--human", "other")
        self.assertEqual(code, 1)
        self.assertIn("--remove-assignee other", out[0])

    def test_random_boards_end_to_end(self):
        rng = random.Random(3)
        for _ in range(15):
            board = random_board(rng)
            self.board({"prs": {str(k): v for k, v in board["prs"].items()}, "issues": board["issues"]})
            code, out, err = self.run_script("--repo", "o/n", "--human", HUMAN)
            want = oracle(board, HUMAN)
            stale = list(want.values()).count("stale")
            self.assertEqual(code, 1 if stale else 0, err)
            self.assertEqual(sum(line.startswith("Stale") for line in out), stale)

    def test_gh_failing_exits_2(self):
        self.board({"prs": {}, "issues": {}})
        open(os.path.join(self.dir, "fail"), "w").close()
        code, out, err = self.run_script("--repo", "o/n")
        self.assertEqual(code, 2)
        self.assertIn("gh failed", err)

    def test_every_gh_call_has_a_time_limit(self):
        with open(SCRIPT, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("timeout=", source)
        self.assertNotIn("subprocess.call(", source)


if __name__ == "__main__":
    unittest.main()
