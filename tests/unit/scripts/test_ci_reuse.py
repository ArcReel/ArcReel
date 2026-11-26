from pathlib import Path
from typing import Any

import pytest
import yaml

from scripts import ci_reuse

_REPO_ROOT = Path(__file__).resolve().parents[3]
_TEST_WORKFLOW_PATH = _REPO_ROOT / ".github" / "workflows" / "test.yml"
_DOMAIN_FILTER_PATH = _REPO_ROOT / ".github" / "actions" / "domain-filter" / "action.yml"


def _load_yaml(path: Path) -> dict[str, Any]:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _build_pr_jobs_from_workflow(
    workflow: dict[str, Any],
    domains: dict[str, str],
    *,
    pr_number: int = 7,
    checkout_sha: str = "4" * 40,
) -> list[dict[str, Any]]:
    """Construct GitHub Actions job payloads directly from test.yml for a PR run."""
    enabled = {domain for domain, value in domains.items() if value == "true"}
    jobs: list[dict[str, Any]] = []
    for job_id, job_def in workflow["jobs"].items():
        raw_name = job_def.get("name", job_id)
        job_runs = _pr_job_should_run(job_id, enabled)
        if not job_runs:
            jobs.append({"name": raw_name, "conclusion": "skipped", "steps": []})
            continue
        matrix_instances = _expand_pr_matrix(job_id, job_def, raw_name)
        for display_name, matrix_ctx in matrix_instances:
            steps = [{"name": "Set up job", "conclusion": "success"}]
            for index, step_def in enumerate(job_def["steps"]):
                step_name = step_def.get("name", f"Run {step_def.get('uses', f'step-{index}')}")
                step_name = step_name.replace("${{ github.event.number }}", str(pr_number)).replace(
                    "${{ github.sha }}", checkout_sha
                )
                conclusion = _pr_step_conclusion(job_id, step_def, enabled, matrix_ctx)
                steps.append({"name": step_name, "conclusion": conclusion})
            steps.append({"name": "Complete job", "conclusion": "success"})
            jobs.append({"name": display_name, "conclusion": "success", "steps": steps})
    return jobs


def _pr_job_should_run(job_id: str, enabled: set[str]) -> bool:
    if job_id in {"changes", "conventions", "ci-required"}:
        return True
    if job_id == "release-integrity":
        return False
    if job_id in {"frontend-static", "test-lint", "e2e-live", "e2e-record-drift"}:
        return bool(enabled & {"backend", "frontend"})
    if job_id in {"backend-static", "backend-tests", "postgres-compat"}:
        return "backend" in enabled
    if job_id in {"frontend-tests", "frontend-e2e"}:
        return "frontend" in enabled
    if job_id == "workflow-static":
        return "workflow" in enabled
    if job_id == "website-checks":
        return "website" in enabled
    if job_id == "docker-verify":
        return "docker" in enabled
    raise AssertionError(f"Unhandled job in test.yml: {job_id}")


def _expand_pr_matrix(job_id: str, job_def: dict[str, Any], raw_name: str) -> list[tuple[str, dict[str, Any]]]:
    matrix = job_def.get("strategy", {}).get("matrix")
    if not matrix:
        return [(raw_name, {})]
    if job_id == "backend-tests":
        return [(raw_name.replace("${{ matrix.suite }}", str(item["suite"])), item) for item in matrix["include"]]
    if job_id == "postgres-compat":
        return [(raw_name.replace("${{ matrix.postgres }}", "16"), {"postgres": 16})]
    if job_id == "frontend-e2e":
        return [(raw_name.replace("${{ matrix.shard }}", str(shard)), {"shard": shard}) for shard in matrix["shard"]]
    raise AssertionError(f"Unhandled matrix job in test.yml: {job_id}")


def _pr_step_conclusion(job_id: str, step_def: dict[str, Any], enabled: set[str], matrix_ctx: dict[str, Any]) -> str:
    cond = step_def.get("if")
    if cond is None:
        return "success"
    if cond in {"failure()", "failure() || cancelled()"}:
        return "skipped"
    if job_id == "changes" and "github.event_name == 'push'" in cond:
        return "skipped"
    if job_id == "changes" and cond == "github.event_name == 'pull_request'":
        return "success"
    if job_id == "conventions" and "env.COMMENT_BASE" in cond:
        return "success"
    if job_id == "backend-tests" and cond == "matrix.suite == 'integration'":
        return "success" if matrix_ctx.get("suite") == "integration" else "skipped"
    if job_id == "frontend-static" and cond == "needs.changes.outputs.frontend == 'true'":
        return "success" if "frontend" in enabled else "skipped"
    if job_id == "frontend-e2e" and cond == "matrix.shard == 1":
        return "success" if matrix_ctx.get("shard") == 1 else "skipped"
    raise AssertionError(f"Unhandled step condition in {job_id}: {step_def.get('name')!r} -> {cond!r}")


def _evidence_fixture(
    *,
    domains: dict[str, str] | None = None,
    diff_paths: str = "frontend/src/main.tsx\0lib/foo.py\0",
    pulls: list[dict[str, Any]] | None = None,
    workflow_runs: list[dict[str, Any]] | None = None,
    jobs: list[dict[str, Any]] | None = None,
    total_count_delta: int = 0,
    tested_base: str | None = None,
    tested_tree: str = "tree",
) -> tuple[str, str, dict[str, str], Any, Any]:
    sha, head, base, checkout = (digit * 40 for digit in "1234")
    active_domains = domains or {d: "true" if d in {"backend", "frontend"} else "false" for d in ci_reuse.DOMAINS}
    workflow = _load_yaml(_TEST_WORKFLOW_PATH)
    active_jobs = (
        _build_pr_jobs_from_workflow(workflow, active_domains, pr_number=7, checkout_sha=checkout)
        if jobs is None
        else jobs
    )
    default_pull = {
        "number": 7,
        "merged_at": "today",
        "merge_commit_sha": sha,
        "base": {"ref": "main", "repo": {"full_name": "a/b"}},
        "head": {"sha": head},
    }
    default_runs = [
        {
            "head_sha": head,
            "conclusion": "success",
            "event": "pull_request",
            "id": 42,
            "run_attempt": 2,
            "pull_requests": [],
        }
    ]
    endpoints: dict[str, Any] = {
        f"commits/{sha}/pulls": [default_pull] if pulls is None else pulls,
        f"actions/workflows/test.yml/runs?event=pull_request&head_sha={head}&per_page=10": {
            "workflow_runs": default_runs if workflow_runs is None else workflow_runs,
        },
        "actions/runs/42/attempts/2/jobs?per_page=100": {
            "jobs": active_jobs,
            "total_count": len(active_jobs) + total_count_delta,
        },
        f"git/commits/{checkout}": {
            "parents": [{"sha": base if tested_base is None else tested_base}, {"sha": head}],
            "tree": {"sha": tested_tree},
        },
    }

    def run_git(*args: str) -> str:
        if args[0] == "diff":
            return diff_paths
        return base if args[-1].endswith("^1") else "tree"

    return base, sha, active_domains, run_git, endpoints.__getitem__


def test_ci_reuse_matches_real_workflows() -> None:
    domain_filter = _load_yaml(_DOMAIN_FILTER_PATH)
    workflow = _load_yaml(_TEST_WORKFLOW_PATH)

    assert set(domain_filter["outputs"].keys()) == ci_reuse.DOMAINS

    backend_suites = tuple(item["suite"] for item in workflow["jobs"]["backend-tests"]["strategy"]["matrix"]["include"])
    assert backend_suites == ci_reuse.SUITES

    changes_steps = {step.get("name", "") for step in workflow["jobs"]["changes"]["steps"]}
    assert f"{ci_reuse.PR_CHECKOUT_STEP_PREFIX} ${{{{ github.event.number }}}} ${{{{ github.sha }}}}" in changes_steps

    domain_combinations = [
        {"backend"},
        {"frontend"},
        {"backend", "frontend"},
        {"website"},
        {"docker"},
        {"workflow", "backend", "frontend", "docker", "website"},
    ]
    for enabled in domain_combinations:
        domains = {d: "true" if d in enabled else "false" for d in ci_reuse.DOMAINS}
        jobs = _build_pr_jobs_from_workflow(workflow, domains)
        assert ci_reuse.successful_attempt(jobs, domains) is True, f"failed for enabled={sorted(enabled)}"

    conditional_steps = {
        step["name"]
        for job_def in workflow["jobs"].values()
        for step in job_def["steps"]
        if "if" in step and "name" in step
    }
    all_domains = dict.fromkeys(ci_reuse.DOMAINS, "true")
    backend_only = {d: "true" if d == "backend" else "false" for d in ci_reuse.DOMAINS}
    declared_optional = set()
    for job_name in ci_reuse.required_jobs(all_domains):
        declared_optional.update(ci_reuse.optional_steps_for_job(job_name, all_domains))
        declared_optional.update(ci_reuse.optional_steps_for_job(job_name, backend_only))
    assert declared_optional <= conditional_steps


def test_equivalent_merged_pr_reuses_with_empty_run_pull_requests() -> None:
    base, sha, domains, run_git, fetch_api = _evidence_fixture()
    assert ci_reuse.verified_run(base, sha, domains, run_git=run_git, fetch_api=fetch_api, repo="a/b") == 42


def test_skips_newer_cancelled_run_but_rejects_newer_failed_run() -> None:
    head = "2" * 40
    passing_run = {
        "head_sha": head,
        "conclusion": "success",
        "event": "pull_request",
        "id": 42,
        "run_attempt": 2,
        "pull_requests": [],
    }
    cancelled_run = {
        "head_sha": head,
        "conclusion": "cancelled",
        "event": "pull_request",
        "id": 99,
        "run_attempt": 1,
        "pull_requests": [],
    }
    failed_run = cancelled_run | {"conclusion": "failure"}

    base, sha, domains, run_git, fetch_api = _evidence_fixture(workflow_runs=[cancelled_run, passing_run])
    assert ci_reuse.verified_run(base, sha, domains, run_git=run_git, fetch_api=fetch_api, repo="a/b") == 42

    base, sha, domains, run_git, fetch_api = _evidence_fixture(workflow_runs=[failed_run, passing_run])
    assert ci_reuse.verified_run(base, sha, domains, run_git=run_git, fetch_api=fetch_api, repo="a/b") is None


@pytest.mark.parametrize(
    "overrides",
    [
        pytest.param({"pulls": []}, id="direct-push"),
        pytest.param({"tested_base": "old"}, id="advanced-base"),
        pytest.param({"tested_tree": "other"}, id="different-tree"),
        pytest.param({"diff_paths": ".github/workflows/test.yml\0"}, id="gate-change"),
        pytest.param({"total_count_delta": 1}, id="missing-page"),
    ],
)
def test_push_evidence_rejects_invalid_commit_or_scope(overrides: dict[str, Any]) -> None:
    base, sha, domains, run_git, fetch_api = _evidence_fixture(**overrides)
    assert ci_reuse.verified_run(base, sha, domains, run_git=run_git, fetch_api=fetch_api, repo="a/b") is None


@pytest.mark.parametrize("mutation", ["skipped-job", "skipped-step", "missing-job", "missing-checkout"])
def test_push_evidence_rejects_incomplete_pr_jobs(mutation: str) -> None:
    domains = {d: "true" if d in {"backend", "frontend"} else "false" for d in ci_reuse.DOMAINS}
    jobs = _build_pr_jobs_from_workflow(_load_yaml(_TEST_WORKFLOW_PATH), domains)
    if mutation == "skipped-job":
        next(j for j in jobs if j["name"] == "Backend tests (unit)")["conclusion"] = "skipped"
    elif mutation == "skipped-step":
        e2e_live = next(j for j in jobs if j["name"] == "e2e-live")
        next(s for s in e2e_live["steps"] if s["name"] == "Run live business flows")["conclusion"] = "skipped"
    elif mutation == "missing-job":
        jobs = [j for j in jobs if j["name"] != "frontend-e2e (2/2)"]
    elif mutation == "missing-checkout":
        changes = next(j for j in jobs if j["name"] == "changes")
        changes["steps"] = [s for s in changes["steps"] if not s["name"].startswith(ci_reuse.PR_CHECKOUT_STEP_PREFIX)]

    base, sha, active_domains, run_git, fetch_api = _evidence_fixture(domains=domains, jobs=jobs)
    assert ci_reuse.verified_run(base, sha, active_domains, run_git=run_git, fetch_api=fetch_api, repo="a/b") is None
