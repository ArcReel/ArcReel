"""Prove a main push equivalent to one successful Tests PR attempt."""

import json
import os
import re
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

DOMAINS = {"recording", "backend", "frontend", "workflow", "website", "docker"}
SUITES = ("unit", "integration", "market-core", "catalog-core")
PR_CHECKOUT_STEP_PREFIX = "PR checkout"
FAILURE_ONLY_OPTIONAL_STEPS = frozenset(
    {"Hang and crash diagnostics", "Upload Playwright report", "Upload live diagnostics"}
)


def _enabled_domains(domains: dict[str, str]) -> set[str]:
    if domains.keys() != DOMAINS or not set(domains.values()) <= {"true", "false"}:
        raise ValueError("incomplete domain selection")
    return {domain for domain, value in domains.items() if value == "true"}


def product_paths_only(paths: list[str]) -> bool:
    return bool(paths) and all(
        path.startswith(("lib/", "server/", "frontend/src/", "website/docs/"))
        and path.endswith((".py", ".ts", ".tsx", ".css", ".md"))
        and not path.startswith(("frontend/src/test/", "frontend/src/__mocks__/"))
        and Path(path).name != "conftest.py"
        and not any(part.startswith(".") for part in Path(path).parts)
        for path in paths
    )


def required_job_ids(domains: dict[str, str], *, full: bool = False, reused: bool = False) -> set[str]:
    enabled = _enabled_domains(domains)
    jobs = {"changes"}
    if not reused:
        jobs.update({"conventions", "e2e-record-drift"})
    if full:
        jobs.add("release-integrity")
    if enabled & {"backend", "frontend"}:
        jobs.update({"frontend-static", "test-lint", "e2e-live"})
    if "backend" in enabled:
        jobs.update({"backend-static", "postgres-compat", "backend-tests"})
    if "frontend" in enabled:
        jobs.update({"frontend-tests", "frontend-e2e"})
    for domain, name in (("workflow", "workflow-static"), ("website", "website-checks"), ("docker", "docker-verify")):
        if domain in enabled:
            jobs.add(name)
    return jobs


def required_jobs(domains: dict[str, str]) -> set[str]:
    display_names = {
        "backend-tests": [f"Backend tests ({suite})" for suite in SUITES],
        "postgres-compat": ["postgres-compat (16)"],
        "frontend-e2e": ["frontend-e2e (1/2)", "frontend-e2e (2/2)"],
        "e2e-record-drift": ["E2E fixture freshness and determinism"],
    }
    names = {"ci-required"}
    for job in required_job_ids(domains):
        names.update(display_names.get(job, [job]))
    return names


def optional_steps_for_job(job_name: str, domains: dict[str, str]) -> set[str]:
    enabled = _enabled_domains(domains)
    optional = set(FAILURE_ONLY_OPTIONAL_STEPS)
    if job_name == "changes":
        optional.add("Verify equivalent PR checks")
    if job_name == "E2E fixture freshness and determinism" and "recording" not in enabled:
        optional.add("Record twice and compare")
    if job_name.startswith("Backend tests (") and job_name != "Backend tests (integration)":
        optional.add("Install system dependencies")
    if job_name == "frontend-static" and "frontend" not in enabled:
        optional.update({"Lint", "Knip"})
    if job_name == "frontend-e2e (2/2)":
        optional.add("Run single-worker viewport contracts")
    return optional


def successful_attempt(jobs: list[dict[str, Any]], domains: dict[str, str]) -> bool:
    enabled = _enabled_domains(domains)
    by_name = {job["name"]: job for job in jobs}
    expected = required_jobs(domains)
    if len(jobs) != len(by_name) or not expected <= by_name.keys():
        return False
    allowed_skips = required_jobs(dict.fromkeys(DOMAINS, "true")) - expected
    allowed_skips.add("release-integrity")
    if "backend" not in enabled:
        allowed_skips.update({"Backend tests (${{ matrix.suite }})", "postgres-compat (${{ matrix.postgres }})"})
    if "frontend" not in enabled:
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
        optional = optional_steps_for_job(name, domains)
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


def api(endpoint: str) -> Any:
    repo = os.environ["GITHUB_REPOSITORY"]
    return json.loads(subprocess.check_output([executable("gh"), "api", f"repos/{repo}/{endpoint}"], timeout=20))


def verified_run(
    before: str,
    sha: str,
    domains: dict[str, str],
    *,
    run_git: Callable[..., str] = git,
    fetch_api: Callable[[str], Any] = api,
    repo: str | None = None,
) -> int | None:
    target_repo = os.environ["GITHUB_REPOSITORY"] if repo is None else repo
    if run_git("rev-parse", f"{sha}^1") != before:
        return None
    paths = run_git("diff", "--no-renames", "--name-only", "-z", before, sha).rstrip("\0").split("\0")
    if not product_paths_only(paths):
        return None
    pulls = [
        pr
        for pr in fetch_api(f"commits/{sha}/pulls")
        if pr["merged_at"]
        and pr["merge_commit_sha"] == sha
        and pr["base"]["ref"] == "main"
        and pr["base"]["repo"]["full_name"] == target_repo
    ]
    if len(pulls) != 1:
        return None
    pr = pulls[0]
    head = pr["head"]["sha"]
    runs = fetch_api(f"actions/workflows/test.yml/runs?event=pull_request&head_sha={head}&per_page=10")["workflow_runs"]
    run = next((candidate for candidate in runs if candidate["conclusion"] != "cancelled"), None)
    if run is None or run["conclusion"] != "success" or run["head_sha"] != head or run["event"] != "pull_request":
        return None
    page = fetch_api(f"actions/runs/{run['id']}/attempts/{run['run_attempt']}/jobs?per_page=100")
    jobs = page["jobs"]
    if page["total_count"] != len(jobs) or not successful_attempt(jobs, domains):
        return None
    changes = next(job for job in jobs if job["name"] == "changes")
    markers = [
        match[1]
        for step in changes["steps"]
        if (match := re.fullmatch(rf"{PR_CHECKOUT_STEP_PREFIX} {pr['number']} ([0-9a-f]{{40}})", step["name"]))
        and step["conclusion"] == "success"
    ]
    if len(markers) != 1:
        return None
    tested = fetch_api(f"git/commits/{markers[0]}")
    if [p["sha"] for p in tested["parents"]] == [before, head] and tested["tree"]["sha"] == run_git(
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
