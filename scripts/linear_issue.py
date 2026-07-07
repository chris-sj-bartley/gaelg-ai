#!/usr/bin/env python3
"""Create a Linear issue on the CHR board from ops scripts (stdlib only, no jq).

Used by health-watch.sh (incident escalation) and housekeeping.sh (dep-bump
reports). Labels that don't exist yet are created on first use. Issues land in
the team's default (triage/backlog) state — a human moves them to Todo, which
is what symphony polls, so nothing runs unreviewed.

Usage:
  linear_issue.py --title "..." --labels incident,agent-ready \
      [--dedupe-label incident] [--dry-run] < description.md

  --dedupe-label L : exit 0 without creating if an open (not completed/
                     cancelled) CHR issue already carries label L.
  --dry-run        : resolve everything, create nothing.

Reads LINEAR_API_KEY from the environment, falling back to symphony/.env.
"""

import argparse
import json
import os
import sys
import urllib.request

API_URL = "https://api.linear.app/graphql"
ENV_FILE = "/exp/exp1/acp24csb/symphony/.env"
TEAM_KEY = "CHR"


def api_key():
    key = os.environ.get("LINEAR_API_KEY")
    if not key and os.path.isfile(ENV_FILE):
        with open(ENV_FILE) as f:
            for line in f:
                if line.startswith("LINEAR_API_KEY="):
                    key = line.split("=", 1)[1].strip()
    if not key:
        sys.exit("linear_issue.py: LINEAR_API_KEY not set and not in symphony/.env")
    return key


def gql(key, query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API_URL, data=body,
        headers={"Authorization": key, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        out = json.load(resp)
    if out.get("errors"):
        sys.exit(f"linear_issue.py: Linear API error: {out['errors']}")
    return out["data"]


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--title", required=True)
    p.add_argument("--labels", default="", help="comma-separated label names")
    p.add_argument("--dedupe-label")
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    key = api_key()
    description = sys.stdin.read() if not sys.stdin.isatty() else ""

    teams = gql(key, "{ teams { nodes { id key } } }")["teams"]["nodes"]
    team_id = next((t["id"] for t in teams if t["key"] == TEAM_KEY), None)
    if not team_id:
        sys.exit(f"linear_issue.py: team {TEAM_KEY} not found")

    if args.dedupe_label:
        dupes = gql(key, """
            query($team: String!, $label: String!) {
              issues(first: 1, filter: {
                team: { key: { eq: $team } },
                labels: { name: { eq: $label } },
                state: { type: { nin: ["completed", "canceled"] } }
              }) { nodes { identifier title } }
            }""", {"team": TEAM_KEY, "label": args.dedupe_label})["issues"]["nodes"]
        if dupes:
            print(f"SKIP: open '{args.dedupe_label}' issue exists: "
                  f"{dupes[0]['identifier']} \"{dupes[0]['title']}\"")
            return

    wanted = [l.strip() for l in args.labels.split(",") if l.strip()]
    existing = {l["name"]: l["id"] for l in
                gql(key, "{ issueLabels { nodes { id name } } }")["issueLabels"]["nodes"]}
    label_ids = []
    for name in wanted:
        if name not in existing:
            if args.dry_run:
                print(f"DRY-RUN: would create label '{name}'")
                continue
            created = gql(key, """
                mutation($name: String!, $teamId: String!) {
                  issueLabelCreate(input: { name: $name, teamId: $teamId }) {
                    issueLabel { id }
                  }
                }""", {"name": name, "teamId": team_id})
            existing[name] = created["issueLabelCreate"]["issueLabel"]["id"]
        label_ids.append(existing.get(name))
    label_ids = [i for i in label_ids if i]

    if args.dry_run:
        print(f"DRY-RUN: would create issue \"{args.title}\" "
              f"labels={wanted} team={TEAM_KEY}")
        return

    issue = gql(key, """
        mutation($input: IssueCreateInput!) {
          issueCreate(input: $input) { issue { identifier url } }
        }""", {"input": {
            "teamId": team_id, "title": args.title,
            "description": description, "labelIds": label_ids,
        }})["issueCreate"]["issue"]
    print(f"CREATED: {issue['identifier']} {issue['url']}")


if __name__ == "__main__":
    main()
