#!/usr/bin/env python3
"""同步 org Project 看板的 Status 字段。

Status 由 repo 侧状态（triage 标签、assignee、关联 PR、开闭状态）单向派生，
派生规则见 derive_status。看板上的手动改动不保留，同步时按派生结果覆盖。

用法：
    GH_TOKEN=<org Projects RW token> python3 scripts/project_status_sync.py --issue 123 [456 ...]
    GH_TOKEN=... python3 scripts/project_status_sync.py --pr 789
    GH_TOKEN=... python3 scripts/project_status_sync.py --all
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from typing import Any

API = "https://api.github.com/graphql"
OWNER = "ArcReel"
REPO = "ArcReel"
PROJECT_NUMBER = 2
PROJECT_ID = "PVT_kwDOD3-zC84BQUFK"
STATUS_FIELD_ID = "PVTSSF_lADOD3-zC84BQUFKzg-eFX8"

STATUS_OPTIONS = {
    "Inbox": "f75ad846",
    "Needs info": "ad9eec1f",
    "Ready · agent": "61e4505c",
    "Ready · human": "22a5fcab",
    "In progress": "47fc9ee4",
    "In review": "df73e18b",
    "Done": "98236657",
}

# 带这些标签的 issue 不参与状态流转：Status 置空，视图用 filter 排除
OFF_BOARD_LABELS = {"Spec", "parked"}

MUTATION_BATCH = 20


def gql(query: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not token:
        sys.exit("GH_TOKEN 未设置")
    payload = json.dumps({"query": query, "variables": variables or {}}).encode()
    req = urllib.request.Request(
        API,
        data=payload,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req) as resp:
        body: dict[str, Any] = json.load(resp)
    if body.get("errors"):
        raise RuntimeError(f"GraphQL 错误: {json.dumps(body['errors'], ensure_ascii=False)}")
    return body["data"]


def derive_status(state: str, labels: set[str], has_assignee: bool, has_open_closing_pr: bool) -> str | None:
    """按优先级派生 Status；返回 None 表示置空。"""
    if labels & OFF_BOARD_LABELS:
        return None
    if state == "CLOSED":
        return "Done"
    if has_open_closing_pr:
        return "In review"
    if has_assignee:
        return "In progress"
    if "ready-for-agent" in labels:
        return "Ready · agent"
    if "ready-for-human" in labels:
        return "Ready · human"
    if "needs-info" in labels:
        return "Needs info"
    return "Inbox"


def _issue_signals(issue: dict[str, Any]) -> tuple[str, set[str], bool, bool]:
    labels = {n["name"] for n in issue["labels"]["nodes"]}
    has_assignee = issue["assignees"]["totalCount"] > 0
    has_open_pr = any(n["state"] == "OPEN" for n in issue["closedByPullRequestsReferences"]["nodes"])
    return issue["state"], labels, has_assignee, has_open_pr


def _set_status_part(item_id: str, status: str | None) -> str:
    if status is None:
        return (
            f'clearProjectV2ItemFieldValue(input: {{projectId: "{PROJECT_ID}", '
            f'itemId: "{item_id}", fieldId: "{STATUS_FIELD_ID}"}}) {{ clientMutationId }}'
        )
    option_id = STATUS_OPTIONS[status]
    return (
        f'updateProjectV2ItemFieldValue(input: {{projectId: "{PROJECT_ID}", itemId: "{item_id}", '
        f'fieldId: "{STATUS_FIELD_ID}", value: {{singleSelectOptionId: "{option_id}"}}}}) {{ clientMutationId }}'
    )


def _run_mutation_parts(parts: list[str]) -> None:
    for start in range(0, len(parts), MUTATION_BATCH):
        chunk = parts[start : start + MUTATION_BATCH]
        gql("mutation { " + " ".join(f"m{i}: {p}" for i, p in enumerate(chunk)) + " }")


ISSUE_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    issue(number: $number) {
      id state
      labels(first: 50) { nodes { name } }
      assignees(first: 1) { totalCount }
      closedByPullRequestsReferences(first: 20, includeClosedPrs: true) { nodes { state } }
      projectItems(first: 10, includeArchived: true) {
        nodes {
          id isArchived
          project { id }
          status: fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
        }
      }
    }
  }
}
"""


def sync_issue(number: int) -> None:
    data = gql(ISSUE_QUERY, {"owner": OWNER, "repo": REPO, "number": number})
    issue = data["repository"]["issue"]
    state, labels, has_assignee, has_open_pr = _issue_signals(issue)
    desired = derive_status(state, labels, has_assignee, has_open_pr)

    item = next((n for n in issue["projectItems"]["nodes"] if n["project"]["id"] == PROJECT_ID), None)
    if item is None:
        if state != "OPEN":
            print(f"#{number}: 已关闭且不在板上，跳过")
            return
        # 内置 auto-add 未覆盖时补充添加
        added = gql(
            "mutation($pid: ID!, $cid: ID!) {"
            " addProjectV2ItemById(input: {projectId: $pid, contentId: $cid}) { item { id } } }",
            {"pid": PROJECT_ID, "cid": issue["id"]},
        )
        item = {"id": added["addProjectV2ItemById"]["item"]["id"], "isArchived": False, "status": None}
    elif item["isArchived"] and state == "OPEN":
        gql(
            "mutation($pid: ID!, $iid: ID!) {"
            " unarchiveProjectV2Item(input: {projectId: $pid, itemId: $iid}) { item { id } } }",
            {"pid": PROJECT_ID, "iid": item["id"]},
        )

    current = (item.get("status") or {}).get("name")
    if current == desired:
        print(f"#{number}: Status 已是 {desired}，无需变更")
        return
    _run_mutation_parts([_set_status_part(item["id"], desired)])
    print(f"#{number}: {current} -> {desired}")


PR_QUERY = """
query($owner: String!, $repo: String!, $number: Int!) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $number) {
      closingIssuesReferences(first: 20) { nodes { number } }
    }
  }
}
"""


def sync_pr(number: int) -> None:
    data = gql(PR_QUERY, {"owner": OWNER, "repo": REPO, "number": number})
    numbers = [n["number"] for n in data["repository"]["pullRequest"]["closingIssuesReferences"]["nodes"]]
    if not numbers:
        print(f"PR #{number}: 无 closing reference，跳过")
        return
    for n in numbers:
        sync_issue(n)


ALL_ITEMS_QUERY = """
query($org: String!, $projectNumber: Int!, $cursor: String) {
  organization(login: $org) {
    projectV2(number: $projectNumber) {
      items(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes {
          id type
          status: fieldValueByName(name: "Status") {
            ... on ProjectV2ItemFieldSingleSelectValue { name }
          }
          content {
            ... on Issue {
              number state
              labels(first: 50) { nodes { name } }
              assignees(first: 1) { totalCount }
              closedByPullRequestsReferences(first: 20, includeClosedPrs: true) { nodes { state } }
            }
          }
        }
      }
    }
  }
}
"""


def sync_all() -> None:
    items: list[dict[str, Any]] = []
    cursor: str | None = None
    while True:
        data = gql(ALL_ITEMS_QUERY, {"org": OWNER, "projectNumber": PROJECT_NUMBER, "cursor": cursor})
        page = data["organization"]["projectV2"]["items"]
        items.extend(page["nodes"])
        if not page["pageInfo"]["hasNextPage"]:
            break
        cursor = page["pageInfo"]["endCursor"]

    parts: list[str] = []
    changed = deleted = 0
    for item in items:
        if item["type"] == "PULL_REQUEST":
            # 看板不收 PR item，对账时清除误入项
            parts.append(
                f'deleteProjectV2Item(input: {{projectId: "{PROJECT_ID}", itemId: "{item["id"]}"}})'
                " { clientMutationId }"
            )
            deleted += 1
            continue
        if item["type"] != "ISSUE":
            continue
        state, labels, has_assignee, has_open_pr = _issue_signals(item["content"])
        desired = derive_status(state, labels, has_assignee, has_open_pr)
        current = (item.get("status") or {}).get("name")
        if current != desired:
            parts.append(_set_status_part(item["id"], desired))
            changed += 1
            print(f"#{item['content']['number']}: {current} -> {desired}")

    _run_mutation_parts(parts)
    print(f"对账完成：{len(items)} 个 item，纠正 {changed} 个 Status，清除 {deleted} 个 PR item")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--issue", type=int, nargs="+", help="同步指定 issue")
    group.add_argument("--pr", type=int, help="同步该 PR closing reference 指向的 issue")
    group.add_argument("--all", action="store_true", help="全量对账")
    args = parser.parse_args()

    if args.all:
        sync_all()
    elif args.pr is not None:
        sync_pr(args.pr)
    else:
        for number in args.issue:
            sync_issue(number)


if __name__ == "__main__":
    main()
