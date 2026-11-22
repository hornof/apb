"""Where apb's hooks act: an apb project, or the apb repo itself. Every hook in hooks/hooks.json uses
this one rule, so none skips the apb repo (issue #132; #118 fixed the watcher alone).

- An apb project has docs/MAP.md.
- The apb repo (or a fork or worktree of it) has no docs/MAP.md. It is the folder whose
  .claude-plugin/plugin.json names the plugin "apb" and that holds hooks/apb-watch-start.py. Claude
  Code's plugin names are the plugin's identity, so another plugin's repo has another name; and a repo
  that only borrowed the name has no hooks/apb-watch-start.py. Both must hold.

The apb repo has no board of its own, no LOG.md and, until a hook writes one, no .apb/events.jsonl.
Each hook copes: the events file is created on the first write, and a hook that reads it treats a
missing file as no events.

Nothing here raises: a folder that can't be read is no apb folder.
"""
import json
import os

HOOK = os.path.join("hooks", "apb-watch-start.py")  # the file that marks the apb repo
MANIFEST = os.path.join(".claude-plugin", "plugin.json")
MAP = os.path.join("docs", "MAP.md")


def is_apb_repo(folder):
    """True if folder is the apb repo itself (or a fork or worktree of it): its plugin manifest gives
    the name "apb", and it holds hooks/apb-watch-start.py."""
    if not isinstance(folder, str) or not folder or not os.path.isfile(os.path.join(folder, HOOK)):
        return False
    try:
        with open(os.path.join(folder, MANIFEST), encoding="utf-8") as f:
            manifest = json.load(f)
    except (OSError, ValueError):
        return False
    return isinstance(manifest, dict) and manifest.get("name") == "apb"


def is_apb_folder(folder):
    """True if apb's hooks act in folder: an apb project (docs/MAP.md) or the apb repo."""
    if not isinstance(folder, str) or not folder:
        return False
    return os.path.isfile(os.path.join(folder, MAP)) or is_apb_repo(folder)


def project_dir(p):
    """The folder a hook acts in for event payload p, or None: CLAUDE_PROJECT_DIR, else the event's
    cwd, when that is an apb project or the apb repo."""
    folder = os.environ.get("CLAUDE_PROJECT_DIR") or (p.get("cwd") if isinstance(p, dict) else None)
    return folder if is_apb_folder(folder) else None
