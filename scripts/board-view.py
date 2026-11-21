#!/usr/bin/env python3
"""Give a project board apb's kanban view: one view, "kanban", board layout, its columns the Status
field's Todo / In progress / In review / Done. apb-setup runs it in step 3, right after it creates
the board:

    python3 board-view.py <owner> <board number>

It runs `gh` as the human's own stored login: it drops GH_TOKEN for every call, because GitHub's
create-view endpoint refuses fine-grained and GitHub App tokens on user-owned boards.

What it does, each part only if it isn't so already, so a second run changes nothing:
1. Sets Status's options to the four columns, in order, in apb's board colours. An option whose name matches one of them
   (any case, such as GitHub's "In Progress") is renamed in place and keeps its id, so cards keep
   their column. Any other option is kept, after the four.
2. Creates the "kanban" view (REST: POST /users|orgs/<owner>/projectsV2/<n>/views).
3. Only once kanban exists, deletes GitHub's default views ("View 1", "View 2", ...). A view a
   person named is kept and listed.

It ends with "Manual steps: none" or one "Manual step:" line per thing left for a person.
Exit 0 when the board has the kanban view; 1 when it doesn't; 2 on bad arguments.
"""
import json
import os
import re
import subprocess
import sys

TIMEOUT = 60
VIEW = "kanban"
COLUMNS = [("Todo", "GRAY"), ("In progress", "YELLOW"), ("In review", "BLUE"), ("Done", "GREEN")]
DEFAULT_VIEW = re.compile(r"^View \d+$")

READ = """query($owner: String!, $number: Int!) {
  repositoryOwner(login: $owner) {
    __typename
    ... on ProjectV2Owner {
      projectV2(number: $number) {
        id url
        fields(first: 100) { nodes { ... on ProjectV2SingleSelectField {
          id databaseId name options { id name color description } } } }
        views(first: 100) { nodes { id name layout
          verticalGroupByFields(first: 5) { nodes { ... on ProjectV2FieldCommon { name } } } } }
      }
    }
  }
}"""

UPDATE = """mutation($fieldId: ID!, $options: [ProjectV2SingleSelectFieldOptionInput!]) {
  updateProjectV2Field(input: {fieldId: $fieldId, singleSelectOptions: $options}) { clientMutationId }
}"""

DELETE = """mutation($viewId: ID!) {
  deleteProjectV2View(input: {viewId: $viewId}) { clientMutationId }
}"""


class Failed(Exception):
    pass


def human_env():
    env = dict(os.environ)
    env.pop("GH_TOKEN", None)
    return env


def gh(args, input=None, env=None, timeout=TIMEOUT):
    return subprocess.run(args, input=input, env=human_env() if env is None else env,
                          capture_output=True, text=True, timeout=timeout)


def call(run, args, body):
    try:
        res = run(args, input=json.dumps(body), env=human_env(), timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise Failed("`%s` timed out after %ds" % (" ".join(args), TIMEOUT))
    except OSError as e:
        raise Failed("`%s` could not run: %s" % (" ".join(args), e))
    if res.returncode != 0:
        raise Failed((res.stderr or res.stdout or "exit %d" % res.returncode).strip())
    data = json.loads(res.stdout or "{}")
    if isinstance(data, dict) and data.get("errors"):
        raise Failed("; ".join(e.get("message", str(e)) for e in data["errors"]))
    return data


def graphql(run, query, variables):
    return call(run, ["gh", "api", "graphql", "--input", "-"], {"query": query, "variables": variables})


def read_board(run, owner, number):
    data = graphql(run, READ, {"owner": owner, "number": number})
    who = (data.get("data") or {}).get("repositoryOwner")
    if not who or not who.get("projectV2"):
        raise Failed("no board %d found for %s" % (number, owner))
    project = who["projectV2"]
    status = [f for f in project["fields"]["nodes"] if f.get("name") == "Status" and "options" in f]
    if not status:
        raise Failed("the board has no Status field")
    views = [{"id": v["id"], "name": v["name"], "layout": v["layout"],
              "group": [g.get("name") for g in v["verticalGroupByFields"]["nodes"]]}
             for v in project["views"]["nodes"]]
    return who["__typename"], project["url"], status[0], views


def wanted_options(options):
    """The Status options the board should have, or None when it has them already."""
    by_name = {}
    for o in options:
        by_name.setdefault(o["name"].lower(), o)
    out, used = [], set()
    for name, color in COLUMNS:
        o = by_name.get(name.lower())
        if o:
            used.add(o["id"])
            out.append({"id": o["id"], "name": name, "color": color, "description": o["description"]})
        else:
            out.append({"name": name, "color": color, "description": ""})
    out += [{k: o[k] for k in ("id", "name", "color", "description")} for o in options if o["id"] not in used]
    if [(o["name"], o["color"]) for o in options] == [(o["name"], o["color"]) for o in out]:
        return None
    return out


def is_kanban(view):
    return view["name"] == VIEW and view["layout"] == "BOARD_LAYOUT" and view["group"] == ["Status"]


def main(argv, run=gh):
    if len(argv) != 2 or not argv[1].isdigit():
        print("usage: board-view.py <owner> <board number>", file=sys.stderr)
        return 2
    owner, number = argv[0], int(argv[1])
    manual = []
    url = "the board"
    try:
        kind, url, status, views = read_board(run, owner, number)

        options = wanted_options(status["options"])
        if options is None:
            print("Status columns: already %s" % " / ".join(n for n, _ in COLUMNS))
        else:
            try:
                graphql(run, UPDATE, {"fieldId": status["id"], "options": options})
                print("Status columns: set to %s" % " / ".join(o["name"] for o in options))
            except Failed as e:
                manual.append("set the Status field's options to %s on %s (GitHub said: %s)"
                              % (" / ".join(n for n, _ in COLUMNS), url, e))

        if any(is_kanban(v) for v in views):
            print("View %s: already there" % VIEW)
        else:
            path = "%s/%s/projectsV2/%d/views" % ("orgs" if kind == "Organization" else "users", owner, number)
            try:
                call(run, ["gh", "api", "-X", "POST", path, "--input", "-"],
                     {"name": VIEW, "layout": "board", "vertical_group_by": [status["databaseId"]]})
            except Failed as e:
                manual.append("on %s, add a view named %s with the Board layout, its columns grouped "
                              "by Status, then delete View 1 (GitHub said: %s)" % (url, VIEW, e))
                raise
            kind, url, status, views = read_board(run, owner, number)
            if not any(is_kanban(v) for v in views):
                manual.append("on %s, check the %s view: GitHub took it but it isn't a board grouped "
                              "by Status" % (url, VIEW))
                raise Failed("the new view isn't a board grouped by Status")
            print("View %s: created, board layout, grouped by Status" % VIEW)

        for v in views:
            if is_kanban(v):
                continue
            if DEFAULT_VIEW.match(v["name"]):
                try:
                    graphql(run, DELETE, {"viewId": v["id"]})
                    print("Deleted GitHub's default view %s" % v["name"])
                except Failed as e:
                    manual.append("on %s, delete the view %s (GitHub said: %s)" % (url, v["name"], e))
            else:
                print("Kept the view %s, which a person made; kanban is not the only view" % v["name"])
    except Failed as e:
        print("board-view.py: %s" % e, file=sys.stderr)
        if not manual:
            manual.append("on %s, add a view named %s with the Board layout, its columns grouped by "
                          "Status (Todo / In progress / In review / Done)" % (url, VIEW))
        for m in manual:
            print("Manual step: " + m)
        return 1
    for m in manual:
        print("Manual step: " + m)
    if not manual:
        print("Manual steps: none")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
