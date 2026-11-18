"""Tests for how the docs describe the way the orchestrator and the agents communicate. Run from the
repo root: python3 -m unittest discover -s scripts -t scripts

The playbook has one section on it, right after "Who does what", so a reader meets it up front. The
README, the project CLAUDE.md template and both agent files point to it by its exact name.
"""
import importlib.util
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SECTION = "How the orchestrator and the agents talk"


def read(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
        return f.read()


def headings(text, level="## "):
    return [line[len(level):].strip() for line in text.splitlines() if line.startswith(level)]


def section(text, name, level="## "):
    """The body of the heading `name`, up to the next heading of the same level."""
    m = re.search(r"^%s%s\n(.*?)(?=^%s|\Z)" % (re.escape(level), re.escape(name), re.escape(level)),
                  text, re.S | re.M)
    return m.group(1) if m else ""


def flat(text):
    return " ".join(text.replace("*", "").split())


class PlaybookSection(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playbook = read("docs/playbook.md")
        cls.body = section(cls.playbook, SECTION)
        cls.text = flat(cls.body)

    def test_comes_right_after_who_does_what(self):
        names = headings(self.playbook)
        self.assertIn(SECTION, names)
        self.assertEqual(names[names.index(SECTION) - 1], "Who does what")

    def test_names_the_four_ways(self):
        for label in ("The brief.", "Messages while it runs.", "The final report.", "The full log."):
            self.assertIn("**%s**" % label, self.body, label)

    def test_says_why(self):
        for words in ("same laptop", "Cowork", "copy chats"):
            self.assertIn(words, self.text, words)

    def test_says_where_the_logs_are_and_how_to_drill_down(self):
        for words in (".apb/events.jsonl", "~/.claude/projects/", "/subagents/agent-", "is_error",
                      "failed", "can't see"):
            self.assertIn(words, self.text, words)

    def test_log_path_uses_keys_the_events_file_really_writes(self):
        # Independent check: run the events hook on an agent's start and read the keys it writes.
        spec = importlib.util.spec_from_file_location("apb_events", os.path.join(ROOT, "hooks", "apb-events.py"))
        events = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(events)
        line = events.record({"hook_event_name": "SubagentStart", "session_id": "s1", "agent_id": "a1",
                              "agent_type": "apb:builder"})
        path = re.search(r"`(~/\.claude/projects/[^`]*)`", self.body)
        self.assertIsNotNone(path, "the section gives the log's path in backticks")
        for key in re.findall(r"<(\w+)>", path.group(1)):  # <project folder> has a space: not a key
            self.assertIn(key, line, "the path's <%s> is not a key in the events file" % key)
        self.assertIn("<agent_id>", path.group(1))
        self.assertIn("<session>", path.group(1))

    def test_every_repo_file_it_names_exists(self):
        named = re.findall(r"`((?:agents|hooks|scripts|skills|templates|docs)/[\w./-]+)`", self.body)
        self.assertTrue(named)
        for rel in named:
            self.assertTrue(os.path.exists(os.path.join(ROOT, rel)), rel)

    def test_every_section_it_points_to_exists(self):
        names = headings(self.playbook)
        for ref in re.findall(r'see "([^"]+)"', self.body):
            self.assertIn(ref, names, ref)


class OtherDocsPointToIt(unittest.TestCase):
    def test_readme_has_it_before_the_list_of_pieces(self):
        readme = read("README.md")
        names = headings(readme)
        self.assertIn(SECTION, names)
        self.assertLess(names.index(SECTION), names.index("What's in the box"))
        self.assertIn('"%s"' % SECTION, section(readme, SECTION))

    def test_claude_template_orchestrator_line(self):
        line = next(l for l in read("templates/CLAUDE-template.md").splitlines() if l.startswith("- **Orchestrator**"))
        self.assertIn('"%s"' % SECTION, line)

    def test_each_agent_knows_it_is_seen(self):
        for rel in ("agents/builder.md", "agents/reviewer.md"):
            text = flat(read(rel))
            self.assertIn('"%s"' % SECTION, text, rel)
            self.assertRegex(text, r"orchestrator can message you", rel)
            self.assertRegex(text, r"full log", rel)

    def test_every_quoted_mention_uses_the_exact_name(self):
        # One name per thing: any quoted "How the orchestrator ... talk" is the section's own name.
        for rel in ("README.md", "docs/playbook.md", "templates/CLAUDE-template.md", "agents/builder.md",
                    "agents/reviewer.md", "skills/apb-build/SKILL.md"):
            for quoted in re.findall(r'"(How the orchestrator[^"]*)"', read(rel)):
                self.assertEqual(quoted, SECTION, rel)


if __name__ == "__main__":
    unittest.main()
