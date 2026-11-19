"""Tells Claude's GitHub comments from the human's. decision-check.py and card-times.py load it.

A comment is Claude's when:
- its author is Claude's own GitHub account (apb-setup step 5), signed or not; or
- it is signed "— Claude (<role>)" outside quoted lines. That covers comments posted before the
  account existed, projects without the account, where Claude uses the human's login, and the
  advisor, which posts through its own connection as the human, with its signature.
Every other comment is the human's. A quoted line (a GitHub "Quote reply") never makes a comment
Claude's, so a human reply that quotes Claude stays the human's.

Claude's login comes from, in order: the --claude-login option of the script; the project's
.claude/settings.local.json, when its env block holds GH_TOKEN and GIT_AUTHOR_NAME, as
claude-account.py writes them; the same two variables in the environment, as Claude Code sets
them from that file. None of these means there is no Claude account, and only signatures count.
A builder worktree has no .claude/settings.local.json, since it is git-ignored; there the login comes
from GH_TOKEN and GIT_AUTHOR_NAME in the environment, so never copy the settings file into worktrees.
"""
import json
import os
import re

SETTINGS = os.path.join(".claude", "settings.local.json")
SIGNATURE = re.compile(r"[—-]\s*Claude \(\w+\)")


def signed(body):
    """True if the comment is signed by Claude outside quoted lines."""
    own = "\n".join(line for line in (body or "").splitlines() if not line.lstrip().startswith(">"))
    return bool(SIGNATURE.search(own))


def is_claude(body, author=None, claude_login=None):
    """True if the comment is Claude's: written by Claude's account, or signed by Claude."""
    if claude_login and author and author.lower() == claude_login.lower():
        return True
    return signed(body)


def _from_env(env):
    if not isinstance(env, dict):
        return None
    login = env.get("GIT_AUTHOR_NAME")
    if env.get("GH_TOKEN") and isinstance(login, str) and login.strip():
        return login.strip()
    return None


def claude_login(given=None, settings=SETTINGS, environ=None):
    """Claude's GitHub login, or None when this project has no Claude account. The token is never read
    beyond checking that it is there."""
    if given:
        return given
    try:
        with open(settings, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = None
    found = _from_env(data.get("env")) if isinstance(data, dict) else None
    return found or _from_env(dict(os.environ if environ is None else environ))
