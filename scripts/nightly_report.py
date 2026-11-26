"""Manage run-scoped Nightly alerts from the Actions report job."""

import contextlib
import json
import os
import re
import shutil
import subprocess

_ALERT_MARKER_PREFIX = "nightly-full"


def _alert_title(run_id: int) -> str:
    return f"Nightly CI 运行告警 #{run_id}"


def _alert_marker(ref: str, run_id: int) -> str:
    return f"<!-- {_ALERT_MARKER_PREFIX}:{ref}:{run_id} -->"


def gh(*args: str) -> str:
    executable = shutil.which("gh")
    if not executable:
        raise RuntimeError("GitHub CLI is required")
    return subprocess.check_output([executable, *args], text=True, errors="replace")


def alert_run(issue: dict, ref: str) -> int | None:
    if issue["user"]["login"] != "github-actions[bot]" or {label["name"] for label in issue["labels"]} != {"ci"}:
        return None
    marker = re.match(
        rf"<!-- {re.escape(_ALERT_MARKER_PREFIX)}:{re.escape(ref)}:(\d+) -->(?:\r?\n|$)",
        issue["body"] or "",
    )
    if marker and issue["title"] == _alert_title(int(marker[1])):
        return int(marker[1])
    return None


def recovery_targets(issues: list[dict], ref: str, run_id: int) -> list[int]:
    return [i["number"] for i in issues if (owned := alert_run(i, ref)) is not None and owned <= run_id]


def signature(conclusion: str, log: str) -> str:
    if conclusion == "timed_out" or re.search(
        r"Reached heap limit|No space left on device|The action .+ has timed out after \d+ minutes|Out of memory: Killed process",
        log,
        re.I,
    ):
        return "容量"
    if re.search(r"Test timed out in \d+ms|TimeoutError: (?:locator|page)\.|Flaky Tests? ", log, re.I):
        return "测试竞态"
    if re.search(
        r"AssertionError[:\s]|TestingLibraryElementError:|Property failed after \d+ tests?\b|\bFAILED \S+::", log
    ):
        return "产品断言"
    return "基础设施"


def pages(endpoint: str) -> list:
    return json.loads(gh("api", "--paginate", "--slurp", endpoint))


def failure_details(repo: str, run_id: int, attempt: str) -> str:
    rows = []
    try:
        job_pages = pages(f"repos/{repo}/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100")
    except subprocess.CalledProcessError:
        return "- 基础设施 · 读取作业列表失败，请检查运行记录。"
    for page in job_pages:
        for job in page["jobs"]:
            if job["name"].endswith(" / ci-required"):
                continue
            conclusion = job["conclusion"]
            if conclusion not in {"failure", "timed_out", "cancelled", "startup_failure", "action_required"}:
                continue
            log = ""
            if conclusion in {"failure", "cancelled"}:
                with contextlib.suppress(subprocess.CalledProcessError):
                    log = gh("api", "--allow-escape-sequences", f"repos/{repo}/actions/jobs/{job['id']}/logs")
            failed_steps = ", ".join(s["name"] for s in job["steps"] if s["conclusion"] == "failure")
            rows.append(
                f"- {signature(conclusion, log)} · [{job['name']}]({job['html_url']}) · {failed_steps or conclusion}"
            )
    return "\n".join(rows) or "- 基础设施 · 未取得失败作业签名，请检查运行记录。"


def main() -> int:
    repo, ref = os.environ["GITHUB_REPOSITORY"], os.environ["GITHUB_REF"]
    run_id, attempt = int(os.environ["GITHUB_RUN_ID"]), os.environ["GITHUB_RUN_ATTEMPT"]
    url = f"{os.environ['GITHUB_SERVER_URL']}/{repo}/actions/runs/{run_id}/attempts/{attempt}"
    issues = [
        i
        for page in pages(f"repos/{repo}/issues?state=open&labels=ci&per_page=100")
        for i in page
        if "pull_request" not in i
    ]
    results = json.loads(os.environ["DEEP_JOBS"])
    incomplete = [f"{job}: {state['result']}" for job, state in results.items() if state["result"] != "success"]
    if results and not incomplete:
        for number in recovery_targets(issues, ref, run_id):
            gh("issue", "close", str(number), "-R", repo, "--comment", f"Nightly CI 已恢复：{url}")
        return 0

    summary = ", ".join(incomplete) or "缺少作业结果"
    report = f"Nightly CI 运行失败：{url}\n\n{summary}\n\n{failure_details(repo, run_id, attempt)}"
    for issue in issues:
        if alert_run(issue, ref) == run_id:
            gh("issue", "comment", str(issue["number"]), "-R", repo, "--body", report)
            return 1
    gh(
        "issue",
        "create",
        "-R",
        repo,
        "--label",
        "ci",
        "--title",
        _alert_title(run_id),
        "--body",
        f"{_alert_marker(ref, run_id)}\n{report}",
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
