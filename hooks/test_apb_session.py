"""Tests for apb_session.py (issue #133, comment): each orchestrator session gets a name,
`apb orch N · <plugin version> · <start time PT>`, N going up by one per orchestrator, counted from .apb/.
Run from the repo root: python3 -m unittest discover -s hooks -t hooks

The oracles are independent of the module: the Pacific time is worked out from the US daylight-saving
rule (second Sunday of March to the first Sunday of November, at 2 AM local), not from zoneinfo; and
the numbers are checked against a plain replay of first appearances.
"""
import calendar
import json
import os
import random
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import apb_session  # noqa: E402

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def nth_sunday(year, month, n):
    """Day of the month of the nth Sunday."""
    first = calendar.weekday(year, month, 1)  # Monday is 0
    return 1 + (6 - first) % 7 + 7 * (n - 1)


def pt_oracle(t):
    """'Mon D H:MM AM' in US Pacific time, from the DST rule, not zoneinfo."""
    import datetime
    utc = datetime.datetime.fromtimestamp(t, datetime.timezone.utc).replace(tzinfo=None)
    y = utc.year
    # DST starts 2 AM PST (10:00 UTC) on the second Sunday of March, ends 2 AM PDT (09:00 UTC) on the
    # first Sunday of November.
    start = datetime.datetime(y, 3, nth_sunday(y, 3, 2), 10)
    end = datetime.datetime(y, 11, nth_sunday(y, 11, 1), 9)
    local = utc + datetime.timedelta(hours=-7 if start <= utc < end else -8)
    h = local.hour % 12 or 12
    return "%s %d %d:%02d %s" % (MONTHS[local.month - 1], local.day, h, local.minute,
                                 "AM" if local.hour < 12 else "PM")


def apb_dir(watcher_sessions=()):
    d = os.path.join(tempfile.mkdtemp(), ".apb")
    os.makedirs(d)
    if watcher_sessions:
        with open(os.path.join(d, "events.jsonl"), "w") as f:
            for s in watcher_sessions:
                f.write(json.dumps({"event": "watcher-start", "session": s}) + "\n")
                f.write(json.dumps({"event": "Stop", "session": "not-an-orchestrator-" + s}) + "\n")
    return d


class Clock(unittest.TestCase):
    def test_the_cards_example(self):
        # Nov 21 2026, 7:20 PM PST is 03:20 UTC on Nov 22.
        t = calendar.timegm((2026, 11, 22, 3, 20, 0))
        self.assertEqual(apb_session.pt_clock(t), "Nov 21 7:20 PM")
        self.assertEqual(apb_session.format_name(4, "0.3.59", t), "apb orch 4 · 0.3.59 · Nov 21 7:20 PM")

    def test_summer_time(self):
        self.assertEqual(apb_session.pt_clock(calendar.timegm((2026, 7, 4, 19, 5, 0))), "Jul 4 12:05 PM")
        self.assertEqual(apb_session.pt_clock(calendar.timegm((2026, 7, 4, 7, 5, 0))), "Jul 4 12:05 AM")

    def test_random_times_agree_with_the_dst_rule(self):
        rnd = random.Random(133)
        lo, hi = calendar.timegm((2024, 1, 1, 0, 0, 0)), calendar.timegm((2032, 1, 1, 0, 0, 0))
        for _ in range(3000):
            t = rnd.randrange(lo, hi)
            self.assertEqual(apb_session.pt_clock(t), pt_oracle(t), t)

    def test_times_around_each_switch_agree(self):
        for y in range(2025, 2031):
            for month, n, hour in ((3, 2, 10), (11, 1, 9)):
                edge = calendar.timegm((y, month, nth_sunday(y, month, n), hour, 0, 0))
                for dt in (-3601, -1, 0, 1, 3600):
                    self.assertEqual(apb_session.pt_clock(edge + dt), pt_oracle(edge + dt), (y, month, dt))


class Numbers(unittest.TestCase):
    def test_a_first_orchestrator_is_1(self):
        d = apb_dir()
        self.assertTrue(apb_session.session_name(d, "a", "0.3.60", 0).startswith("apb orch 1 · 0.3.60 · "))

    def test_orchestrators_before_naming_count_from_the_events_file(self):
        # Three sessions started watchers before names existed: the next one is 4, as in the card.
        d = apb_dir(["s1", "s2", "s1", "s3"])
        self.assertTrue(apb_session.session_name(d, "s4", "0.3.59", 0).startswith("apb orch 4 · "))

    def test_a_session_keeps_its_name(self):
        d = apb_dir()
        first = apb_session.session_name(d, "a", "0.3.60", 100)
        apb_session.session_name(d, "b", "0.3.60", 200)
        self.assertEqual(apb_session.session_name(d, "a", "0.3.61", 999999), first)

    def test_a_session_already_in_the_events_file_is_not_counted_twice(self):
        # The watcher hook may log this session's watcher-start before it is named.
        d = apb_dir(["s1", "s2", "me"])
        self.assertTrue(apb_session.session_name(d, "me", "1", 0).startswith("apb orch 3 · "))

    def test_no_session_id_gets_no_number(self):
        self.assertIsNone(apb_session.session_name(apb_dir(), None, "1", 0))
        self.assertIsNone(apb_session.session_name(apb_dir(), "", "1", 0))

    def test_an_unknown_version_says_so(self):
        self.assertIn(" · ? · ", apb_session.session_name(apb_dir(), "a", None, 0))

    def test_random_sequences_agree_with_a_replay(self):
        rnd = random.Random(7)
        for _ in range(60):
            before = [rnd.choice("pqrs") for _ in range(rnd.randrange(0, 6))]
            d = apb_dir(before)
            calls = [rnd.choice("abcdefpq") for _ in range(rnd.randrange(1, 15))]
            # Replay: earlier orchestrators are the distinct sessions in the events file; a new
            # session takes one more than everything counted so far; a known session keeps its number.
            given, top = {}, 0
            seeded = list(dict.fromkeys(before))
            for s in calls:
                if s not in given:
                    top = max(top, len([x for x in seeded if x != s])) + 1
                    given[s] = top
                name = apb_session.session_name(d, s, "9.9", 0)
                self.assertTrue(name.startswith("apb orch %d · 9.9 · " % given[s]), (before, calls, s, name))
            numbers = list(given.values())
            self.assertEqual(numbers, sorted(set(numbers)), "first-seen order, no repeats")

    def test_sessions_named_at_once_get_different_numbers(self):
        d = apb_dir()
        code = ("import sys; sys.path.insert(0, %r); import apb_session; "
                "print(apb_session.session_name(%r, sys.argv[1], '1', 0))" % (HERE, d))
        procs = [subprocess.Popen([sys.executable, "-c", code, "s%d" % i], stdout=subprocess.PIPE, text=True)
                 for i in range(8)]
        names = [p.communicate(timeout=30)[0].strip() for p in procs]
        numbers = sorted(int(n.split()[2]) for n in names)
        self.assertEqual(numbers, list(range(1, 9)))

    def test_a_garbled_sessions_file_starts_over_from_the_events_file(self):
        d = apb_dir(["x"])
        with open(os.path.join(d, apb_session.SESSIONS), "w") as f:
            f.write("{not json")
        self.assertTrue(apb_session.session_name(d, "a", "1", 0).startswith("apb orch 2 · "))


def sessions_bytes(d):
    path = os.path.join(d, apb_session.SESSIONS)
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return f.read()


class Reopened(unittest.TestCase):
    """Issue #146: a reopened (resumed) session gets no orchestrator number."""

    def test_an_unnamed_session_asked_without_assign_gets_none_and_nothing_is_written(self):
        d = apb_dir(["s1"])
        apb_session.session_name(d, "s1", "1", 0)
        before = sessions_bytes(d)
        self.assertIsNone(apb_session.session_name(d, "old", "1", 0, assign=False))
        self.assertEqual(sessions_bytes(d), before)
        # The next new session still takes the next number: the reopened one used none.
        self.assertTrue(apb_session.session_name(d, "new", "1", 0).startswith("apb orch 2 · "))

    def test_a_named_session_asked_without_assign_keeps_its_name(self):
        d = apb_dir()
        name = apb_session.session_name(d, "a", "1", 0)
        apb_session.session_name(d, "b", "1", 0)
        before = sessions_bytes(d)
        self.assertEqual(apb_session.session_name(d, "a", "2", 999, assign=False), name)
        self.assertEqual(sessions_bytes(d), before)

    def test_without_assign_an_empty_folder_stays_without_a_sessions_file(self):
        d = apb_dir()
        self.assertIsNone(apb_session.session_name(d, "a", "1", 0, assign=False))
        self.assertIsNone(sessions_bytes(d))

    def test_a_marked_session_is_resumed_and_others_are_not(self):
        d = apb_dir()
        self.assertFalse(apb_session.is_resumed(d, "a"))
        apb_session.mark_resumed(d, "a")
        self.assertTrue(apb_session.is_resumed(d, "a"))
        self.assertFalse(apb_session.is_resumed(d, "b"))
        apb_session.mark_resumed(d, "a")  # twice is fine
        self.assertTrue(apb_session.is_resumed(d, "a"))
        self.assertFalse(apb_session.is_resumed(d, None))
        apb_session.mark_resumed(d, None)  # no session id: nothing to mark
        self.assertIsNone(sessions_bytes(d), "marking never touches sessions.json")

    def test_a_garbled_resumed_file_counts_as_no_marks(self):
        d = apb_dir()
        with open(os.path.join(d, apb_session.RESUMED), "w") as f:
            f.write("[not a dict")
        self.assertFalse(apb_session.is_resumed(d, "a"))
        apb_session.mark_resumed(d, "a")
        self.assertTrue(apb_session.is_resumed(d, "a"))

    def test_marks_from_many_hooks_at_once_are_all_kept(self):
        d = apb_dir()
        code = ("import sys; sys.path.insert(0, %r); import apb_session; "
                "apb_session.mark_resumed(%r, sys.argv[1])" % (HERE, d))
        procs = [subprocess.Popen([sys.executable, "-c", code, "s%d" % i]) for i in range(8)]
        for p in procs:
            p.wait(timeout=30)
        self.assertTrue(all(apb_session.is_resumed(d, "s%d" % i) for i in range(8)))


def write_events(d, sessions):
    with open(os.path.join(d, "events.jsonl"), "w", encoding="utf-8") as f:
        for i, s in enumerate(sessions):
            f.write(json.dumps({"time": "2026-11-21T00:00:%02d.000Z" % (i % 60), "event": "Stop", "session": s})
                    + "\n")


class FirstActivity(unittest.TestCase):
    """Issue #146: a session whose first activity in this folder is older than the holder's was reopened."""

    def test_the_cards_case(self):
        d = apb_dir()
        write_events(d, ["bf4e8d74", "x", "orch6", "bf4e8d74", "orch6"])
        self.assertTrue(apb_session.active_before(d, "bf4e8d74", "orch6"))
        self.assertFalse(apb_session.active_before(d, "orch6", "bf4e8d74"))

    def test_a_session_with_no_activity_is_not_before_anyone(self):
        d = apb_dir()
        write_events(d, ["holder"])
        self.assertFalse(apb_session.active_before(d, "new", "holder"))
        self.assertFalse(apb_session.active_before(d, "new", None))
        self.assertFalse(apb_session.active_before(apb_dir(), "a", "b"), "no events file")

    def test_a_holder_with_no_activity_is_after_any_active_session(self):
        d = apb_dir()
        write_events(d, ["old"])
        self.assertTrue(apb_session.active_before(d, "old", "holder"))

    def test_an_id_inside_another_id_does_not_count(self):
        d = apb_dir()
        write_events(d, ["abc-1", "holder", "abc"])
        self.assertFalse(apb_session.active_before(d, "abc", "holder"))

    def test_random_logs_agree_with_a_replay(self):
        rnd = random.Random(146)
        for _ in range(300):
            log = [rnd.choice(["a", "b", "c", "ab", "a b"]) for _ in range(rnd.randrange(0, 10))]
            d = apb_dir()
            write_events(d, log)
            if rnd.random() < 0.2:
                with open(os.path.join(d, "events.jsonl"), "a") as f:
                    f.write("{garbled\n")
            mine, theirs = rnd.choice("abcd"), rnd.choice("abcd")
            first = {}
            for i, s in enumerate(log):
                first.setdefault(s, i)
            expected = mine != theirs and mine in first and first[mine] < first.get(theirs, len(log))
            self.assertEqual(apb_session.active_before(d, mine, theirs), expected, (log, mine, theirs))


if __name__ == "__main__":
    unittest.main()
