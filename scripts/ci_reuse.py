"""Prove a main push equivalent to one successful Tests PR attempt."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

DOMAINS = {"backend", "frontend", "workflow", "website", "docker"}
SUITES = ("unit", "integration", "market-core", "catalog-core")


def product_paths_only(paths: list[str]) -> bool:
    return bool(paths) and all(
        path.startswith(("lib/", "server/", "frontend/src/", "website/docs/"))
        and path.endswith((".py", ".ts", ".tsx", ".css", ".md"))
        and not path.startswith(("frontend/src/test/", "frontend/src/__mocks__/"))
        and Path(path).name != "conftest.py"
        and not any(part.startswith(".") for part in Path(path).parts)
        for path in paths
    )


def required_jobs(domains: dict[str, str]) -> set[str]:
    if domains.keys() != DOMAINS or not set(domains.values()) <= {"true", "false"}:
        raise ValueError("incomplete domain selection")
    jobs = {"changes", "conventions", "ci-required"}
    enabled = {d for d in domains if domains[d] == "true"}
    if enabled & {"backend", "frontend"}:
        jobs.update({"frontend-static", "test-lint", "e2e-live", "E2E fixture recording drift"})
    if "backend" in enabled:
        jobs.update({"backend-static", "postgres-compat (16)", *(f"Backend tests ({suite})" for suite in SUITES)})
    if "frontend" in enabled:
        jobs.update({"frontend-tests", "frontend-e2e (1/2)", "frontend-e2e (2/2)"})
    for domain, name in (("workflow", "workflow-static"), ("website", "website-checks"), ("docker", "docker-verify")):
        if domain in enabled:
            jobs.add(name)
    return jobs


def successful_attempt(jobs: list[dict], domains: dict) -> bool:
    by_name = {job["name"]: job for job in jobs}
    expected = required_jobs(domains)
    if len(jobs) != len(by_name) or not expected <= by_name.keys():
        return False
    allowed_skips = required_jobs(dict.fromkeys(DOMAINS, "true")) - expected
    allowed_skips.add("release-integrity")
    if domains["backend"] == "false":
        allowed_skips.update({"Backend tests (${{ matrix.suite }})", "postgres-compat (${{ matrix.postgres }})"})
    if domains["frontend"] == "false":
        allowed_skips.add("frontend-e2e (${{ matrix.shard }}/2)")
    for name in by_name.keys() - expected:
        conclusion = by_name[name]["conclusion"]
        if conclusion != "success" and (conclusion != "skipped" or name not in allowed_skips):
            return False
    for name in expected:
        job = by_name[name]
        steps = job["steps"]
        if job["conclusion"] != "success" or len(steps) < 3:
            return False
        if steps[0]["name"] != "Set up job" or steps[-1]["name"] != "Complete job":
            return False
        optional = {"Hang and crash diagnostics", "Upload Playwright report", "Upload live diagnostics"}
        if name == "changes":
            optional.add("Verify equivalent PR checks")
        if name.startswith("Backend tests (") and name != "Backend tests (integration)":
            optional.add("Install system dependencies")
        if name == "frontend-static" and domains["frontend"] == "false":
            optional.update({"Lint", "Knip"})
        if name == "frontend-e2e (2/2)":
            optional.add("Run single-worker viewport contracts")
        for step in steps:
            if step["conclusion"] == "skipped" and step["name"] in optional:
                continue
            if step["conclusion"] != "success":
                return False
    return True


def executable(name: str) -> str:
    path = shutil.which(name)
    if path is None:
        raise FileNotFoundError(name)
    return path


def git(*args: str) -> str:
    return subprocess.check_output([executable("git"), *args], text=True).rstrip("\n")


def api(endpoint: str):
    repo = os.environ["GITHUB_REPOSITORY"]
    return json.loads(subprocess.check_output([executable("gh"), "api", f"repos/{repo}/{endpoint}"], timeout=20))


def verified_run(before: str, sha: str, domains: dict) -> int | None:
    if git("rev-parse", f"{sha}^1") != before:
        return None
    paths = git("diff", "--no-renames", "--name-only", "-z", before, sha).rstrip("\0").split("\0")
    if not product_paths_only(paths):
        return None
    pulls = [
        pr
        for pr in api(f"commits/{sha}/pulls")
        if pr["merged_at"]
        and pr["merge_commit_sha"] == sha
        and pr["base"]["ref"] == "main"
        and pr["base"]["repo"]["full_name"] == os.environ["GITHUB_REPOSITORY"]
    ]
    if len(pulls) != 1:
        return None
    pr = pulls[0]
    head = pr["head"]["sha"]
    runs = api(f"actions/workflows/test.yml/runs?event=pull_request&head_sha={head}&per_page=1")["workflow_runs"]
    if not runs:
        return None
    run = runs[0]
    if run["conclusion"] != "success" or run["head_sha"] != head or run["event"] != "pull_request":
        return None
    if not any(link["number"] == pr["number"] and link["base"]["ref"] == "main" for link in run["pull_requests"]):
        return None
    page = api(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
    jobs = page["jobs"]
    if page["total_count"] != len(jobs) or not successful_attempt(jobs, domains):
        return None
    changes = next(job for job in jobs if job["name"] == "changes")
    markers = [
        match[1]
        for step in changes["steps"]
        if (match := re.fullmatch(rf"PR checkout {pr['number']} ([0-9a-f]{{40}})", step["name"]))
        and step["conclusion"] == "success"
    ]
    if len(markers) != 1:
        return None
    tested = api(f"git/commits/{markers[0]}")
    if [p["sha"] for p in tested["parents"]] == [before, head] and tested["tree"]["sha"] == git(
        "rev-parse", f"{sha}^{{tree}}"
    ):
        return run["id"]
    return None


def main() -> None:
    try:
        run = verified_run(os.environ["BEFORE"], os.environ["GITHUB_SHA"], json.loads(os.environ["DOMAINS"]))
    except (OSError, ValueError, TypeError, KeyError, subprocess.SubprocessError) as exc:
        print(f"PR evidence unavailable ({type(exc).__name__})")
        run = None
    print(f"Reusing Tests run {run}" if run else "Running affected domains")
    with Path(os.environ["GITHUB_OUTPUT"]).open("a", encoding="utf-8") as output:
        print(f"reused={'true' if run else 'false'}", file=output)


if __name__ == "__main__":
    main()
