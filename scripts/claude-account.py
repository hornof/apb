"""Stores Claude's own GitHub token for one project, without ever showing it.

Usage, from the project's repo root:
    python3 <apb plugin folder>/scripts/claude-account.py <token file> [--settings .claude/settings.local.json] [--keep-file]

The human saves the token of Claude's GitHub account in a text file that holds nothing else. This
script:
1. reads the token from that file;
2. checks that the settings file is git-ignored, so the token can't be committed;
3. asks GitHub who the token signs in as, and which scopes it has. It needs a classic token with
   `repo`, `project`, `read:org` and `workflow`, and refuses the human's own token: it asks `gh`,
   without the token, who the human is, and refuses when the two logins match;
4. writes `env` entries into the settings file: GH_TOKEN, so Claude Code's `gh` acts as the account,
   and GIT_AUTHOR_NAME/EMAIL and GIT_COMMITTER_NAME/EMAIL, so commits show it too. Everything else in
   the file stays as it was;
5. deletes the token file, unless --keep-file is given.
In place of a token file, it takes an earlier project's settings file (any path ending in .json) and
copies the GH_TOKEN from its env block; that file is never deleted.
If any check fails, it writes nothing and keeps the token file. The token is passed to `gh` in its
environment, never on a command line, and never appears in what the script prints.
"""
import argparse
import json
import os
import subprocess
import sys

TIME_LIMIT = 30  # seconds for each gh or git call
NEEDED = ("repo", "project", "read:org", "workflow")
SETTINGS = os.path.join(".claude", "settings.local.json")
TOKEN_VARIABLES = ("GH_TOKEN", "GITHUB_TOKEN")  # unset these to reach the human's own stored login


def run(args, env=None, unset=()):
    full = dict(os.environ)
    for name in unset:
        full.pop(name, None)
    full.update(env or {})
    return subprocess.run(args, capture_output=True, text=True, timeout=TIME_LIMIT, env=full)


class Refused(Exception):
    pass


def call(run, args, **kwargs):
    """`run`, with a missing program or a call past the time limit turned into a refusal."""
    try:
        return run(args, **kwargs)
    except FileNotFoundError:
        raise Refused("Couldn't run `%s`: is it installed?" % args[0])
    except subprocess.TimeoutExpired:
        raise Refused("`%s` took longer than %d seconds." % (" ".join(args[:3]), TIME_LIMIT))


def load_json(path, text):
    try:
        return json.loads(text)
    except ValueError:
        raise Refused("%s isn't valid JSON." % path)


def split(answer):
    """The headers and the body of `gh api -i` output."""
    text = answer.replace("\r\n", "\n")
    head, _, rest = text.partition("\n\n")
    return head, rest


def scopes(answer):
    """The token's scopes from the X-OAuth-Scopes header; None when there is no such header, as with
    fine-grained tokens."""
    for line in split(answer)[0].splitlines():
        name, _, value = line.partition(":")
        if name.strip().lower() == "x-oauth-scopes":
            return {s.strip() for s in value.split(",") if s.strip()}
    return None


def body(answer):
    return load_json("GitHub's answer", split(answer)[1])


def from_settings(path):
    """True when the token comes from an earlier project's settings file rather than a token file."""
    return path.endswith(".json")


def read_token(path):
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except FileNotFoundError:
        raise Refused("There is no file at %s." % path)
    if from_settings(path):
        token = load_json(path, text).get("env", {}).get("GH_TOKEN")
        if not token:
            raise Refused("%s has no GH_TOKEN in its env block." % path)
        return token
    words = text.split()
    if len(words) != 1:
        raise Refused("%s must hold only the token, with nothing else." % path)
    return words[0]


def check_ignored(settings, run):
    folder = os.path.dirname(os.path.abspath(settings))
    while not os.path.isdir(folder):
        folder = os.path.dirname(folder)
    done = call(run, ["git", "-C", folder, "check-ignore", "-q", os.path.abspath(settings)])
    if done.returncode != 0:
        raise Refused("%s is not git-ignored, so the token could be committed. Add it to .gitignore first."
                      % settings)


def check_token(token, run):
    done = call(run, ["gh", "api", "-i", "user"], env={"GH_TOKEN": token})
    if done.returncode != 0:
        raise Refused("GitHub didn't accept the token: " + (done.stderr or done.stdout or "").strip())
    have = scopes(done.stdout)
    if have is None:
        raise Refused("This is not a classic token. Make a classic token: fine-grained tokens can't reach "
                      "Projects boards owned by a user account.")
    missing = [s for s in NEEDED if s not in have]
    if missing:
        raise Refused("The token lacks the scope%s %s. Tick %s on the token's page on GitHub and save."
                      % ("s" if len(missing) > 1 else "", ", ".join(missing), ", ".join(NEEDED)))
    user = body(done.stdout)
    return user["login"], user["id"], sorted(have)


def human_login(run):
    """The login of the human's own `gh`, asked without any token variable; None if it isn't logged in."""
    done = call(run, ["gh", "api", "user", "--jq", ".login"], unset=TOKEN_VARIABLES)
    if done.returncode != 0:
        return None
    return done.stdout.strip() or None


def check_not_human(login, run):
    human = human_login(run)
    if human == login:
        raise Refused("The token signs in as %s, which is your own account. Make the token while signed in "
                      "as Claude's account, in a private browser window." % login)
    return human


def store(settings, token, login, user_id):
    data = {}
    if os.path.exists(settings):
        with open(settings, encoding="utf-8") as f:
            data = load_json(settings, f.read())
    email = "%s+%s@users.noreply.github.com" % (user_id, login)
    env = data.setdefault("env", {})
    env.update({"GH_TOKEN": token, "GIT_AUTHOR_NAME": login, "GIT_AUTHOR_EMAIL": email,
                "GIT_COMMITTER_NAME": login, "GIT_COMMITTER_EMAIL": email})
    os.makedirs(os.path.dirname(os.path.abspath(settings)), exist_ok=True)
    fd = os.open(settings, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.chmod(settings, 0o600)
    return email


def main(argv, run=run):
    parser = argparse.ArgumentParser(description="Store Claude's own GitHub token for this project.")
    parser.add_argument("token_file")
    parser.add_argument("--settings", default=SETTINGS, help="default: " + SETTINGS)
    parser.add_argument("--keep-file", action="store_true", help="don't delete the token file")
    args = parser.parse_args(argv)
    token = None
    try:
        token = read_token(args.token_file)
        check_ignored(args.settings, run)
        login, user_id, have = check_token(token, run)
        human = check_not_human(login, run)
        email = store(args.settings, token, login, user_id)
    except Refused as e:
        message = str(e)
        if token:
            message = message.replace(token, "<token>")
        print(message + " Nothing was stored.", file=sys.stderr)
        return 1
    print("The token signs in as %s, with the scopes %s." % (login, ", ".join(have)))
    if human is None:
        print("Note: your own `gh` isn't logged in here, so I couldn't compare the token's account with yours.")
    else:
        print("Your own `gh` stays logged in as %s." % human)
    print("Stored GH_TOKEN, and the git author %s <%s>, in %s." % (login, email, args.settings))
    if not args.keep_file and not from_settings(args.token_file):
        os.remove(args.token_file)
        print("Deleted %s." % args.token_file)
    print("Check: in a new Claude Code session in this folder, `gh auth status` shows %s (GH_TOKEN)." % login)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
