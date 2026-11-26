import json
from pathlib import Path

import pytest
import yaml

from scripts import nightly_report
from scripts.nightly_report import signature


def issue(run: int) -> dict:
    return {
        "title": f"Nightly CI 运行告警 #{run}",
        "body": f"<!-- nightly-full:refs/heads/main:{run} -->\n运行失败",
        "user": {"login": "github-actions[bot]"},
        "labels": [{"name": "ci"}],
    }


@pytest.mark.parametrize("result", ["failure", "cancelled", "skipped"])
@pytest.mark.parametrize("job", ["backend-exploration", "frontend-exploration", "e2e-live"])
def test_failed_run_rerun_and_next_green(monkeypatch, result, job):
    capacity = issue(10) | {"number": 4093, "title": "Nightly full CI 失败", "body": "运行超时"}
    confirmed = issue(11) | {"number": 41, "labels": [{"name": "ci"}, {"name": "bug"}]}
    alerts = [
        capacity,
        confirmed,
        issue(13) | {"number": 43, "user": {"login": "contributor"}},
        issue(15) | {"number": 45},
        issue(13) | {"number": 46, "body": "<!-- nightly-full:refs/heads/test:13 -->"},
    ]
    writes = []

    def github(command, *args):
        if command == "api":
            if "/issues?" in args[-1]:
                return json.dumps([[capacity], alerts[1:]])
            if "/jobs?" in args[-1]:
                return json.dumps(
                    [
                        {
                            "jobs": [
                                {
                                    "id": 1,
                                    "name": "integration",
                                    "conclusion": "failure",
                                    "html_url": "https://example.org/job/1",
                                    "steps": [],
                                }
                            ]
                        }
                    ]
                )
            assert "--allow-escape-sequences" in args
            return "The action 'Run tests' has timed out after 20 minutes."
        assert command == "issue"
        writes.append(list(args))
        return ""

    monkeypatch.setattr(nightly_report, "gh", github)
    for key, value in {
        "GITHUB_REPOSITORY": "owner/repo",
        "GITHUB_REF": "refs/heads/main",
        "GITHUB_RUN_ID": "12",
        "GITHUB_RUN_ATTEMPT": "1",
        "GITHUB_SERVER_URL": "https://github.com",
        "DEEP_JOBS": json.dumps({"browser-build": {"result": "success"}, job: {"result": result}}),
    }.items():
        monkeypatch.setenv(key, value)
    assert nightly_report.main() == 1
    (created,) = writes
    assert created[0] == "create"
    body = created[created.index("--body") + 1]
    assert f"{job}: {result}" in body
    assert "容量" in body
    assert "integration" in body
    alerts.append(issue(12) | {"number": 42, "body": body, "title": created[created.index("--title") + 1]})

    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    assert nightly_report.main() == 1
    assert writes[1][:2] == ["comment", "42"]
    monkeypatch.setenv("GITHUB_RUN_ID", "14")
    monkeypatch.setenv("DEEP_JOBS", json.dumps({"browser-build": {"result": "success"}, job: {"result": "success"}}))
    assert nightly_report.main() == 0
    assert [args[:2] for args in writes[2:]] == [["close", "42"]]


@pytest.mark.parametrize(
    ("conclusion", "log", "kind"),
    [
        ("timed_out", "", "容量"),
        ("failure", "FATAL ERROR: Reached heap limit", "容量"),
        ("failure", "Failed: Test timed out in 5000ms", "测试竞态"),
        ("failure", "E   AssertionError: expected 4", "产品断言"),
        ("failure", "Error: Property failed after 1 tests", "产品断言"),
        ("failure", "connection reset by peer", "基础设施"),
    ],
)
def test_failed_job_signature(conclusion, log, kind):
    assert signature(conclusion, log) == kind


def test_step_timeout_routes_to_capacity():
    assert signature("failure", "##[error]The action 'Run tests' has timed out after 20 minutes.") == "容量"


def test_nightly_explorations_report_and_keep_failure_logs():
    path = Path(__file__).resolve().parents[3] / ".github/workflows/nightly-full.yml"
    jobs = yaml.safe_load(path.read_text(encoding="utf-8"))["jobs"]
    assert set(jobs["report"]["needs"]) == jobs.keys() - {"report"}
    assert jobs["report"]["if"] == "always()"
    assert jobs["report"]["steps"][-1]["env"]["DEEP_JOBS"] == "${{ toJSON(needs) }}"
    strategy = jobs["backend-exploration"]["strategy"]
    assert set(strategy["matrix"]["suite"]) == {"backend", "archives"}
    assert strategy["fail-fast"] is False
    for name in ("backend-exploration", "frontend-exploration"):
        steps = jobs[name]["steps"]
        run = next(step for step in steps if "nightly_invariants.py" in step.get("run", ""))
        assert run["shell"] == "bash"
        assert not run.get("continue-on-error")
        upload = next(step for step in steps if "upload-artifact" in step.get("uses", ""))
        assert upload["if"] == "failure() || cancelled()"
        assert upload["with"]["path"] in run["run"]


def test_nightly_live_build_restore_and_reporting():
    github = Path(__file__).resolve().parents[3] / ".github"
    jobs = yaml.safe_load((github / "workflows/nightly-full.yml").read_text())["jobs"]
    build = jobs["browser-build"]
    assert "if" not in build

    def artifact(steps, action):
        return next(step for step in steps if step.get("uses", "").startswith(f"actions/{action}-artifact@"))

    upload = artifact(build["steps"], "upload")
    restore = yaml.safe_load((github / "actions/restore-browser-build/action.yml").read_text())["runs"]["steps"]
    download = artifact(restore, "download")
    assert upload["with"]["name"] == download["with"]["name"] == "browser-build-${{ github.run_id }}-${{ github.sha }}"
    live = jobs["e2e-live"]
    assert live["needs"] == "browser-build"
    assert "if" not in live
    steps = live["steps"]
    restore_step = next(step for step in steps if step.get("uses") == "./.github/actions/restore-browser-build")
    run = next(step for step in steps if "--project=live" in step.get("run", ""))
    diagnostics = artifact(steps, "upload")
    assert steps.index(restore_step) < steps.index(run) < steps.index(diagnostics)
    assert run["env"]["E2E_LIVE"] == "1"
    assert not run.get("continue-on-error")
    assert diagnostics["if"] == "failure()"
    assert set(diagnostics["with"]["path"].splitlines()) == {
        "${{ runner.temp }}/e2e-live.log",
        "frontend/test-results/",
        "frontend/playwright-report/",
    }
    report = jobs["report"]
    assert report["if"] == "always()"
    assert set(report["needs"]) == jobs.keys() - {"report"}
    assert report["steps"][-1]["env"]["DEEP_JOBS"] == "${{ toJSON(needs) }}"
