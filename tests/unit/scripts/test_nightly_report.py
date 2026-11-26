import json

import pytest

from scripts import nightly_report
from scripts.nightly_report import signature


def issue(run: int) -> dict:
    return {
        "title": f"Nightly CI 运行告警 #{run}",
        "body": f"<!-- nightly-full:refs/heads/main:{run} -->\n运行失败",
        "user": {"login": "github-actions[bot]"},
        "labels": [{"name": "ci"}],
    }


def test_failed_run_rerun_and_next_green(monkeypatch):
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
        "RESULT": "failure",
    }.items():
        monkeypatch.setenv(key, value)
    nightly_report.main()
    (created,) = writes
    assert created[0] == "create"
    body = created[created.index("--body") + 1]
    assert "容量" in body
    assert "integration" in body
    alerts.append(issue(12) | {"number": 42, "body": body, "title": created[created.index("--title") + 1]})

    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")
    nightly_report.main()
    assert writes[1][:2] == ["comment", "42"]
    monkeypatch.setenv("GITHUB_RUN_ID", "14")
    monkeypatch.setenv("RESULT", "success")
    nightly_report.main()
    assert [args[:2] for args in writes[2:]] == [["close", "42"]]


@pytest.mark.parametrize(
    ("conclusion", "log", "kind"),
    [
        ("timed_out", "", "容量"),
        ("failure", "FATAL ERROR: Reached heap limit", "容量"),
        ("failure", "Failed: Test timed out in 5000ms", "测试竞态"),
        ("failure", "E   AssertionError: expected 4", "产品断言"),
        ("failure", "connection reset by peer", "基础设施"),
    ],
)
def test_failed_job_signature(conclusion, log, kind):
    assert signature(conclusion, log) == kind


def test_step_timeout_routes_to_capacity():
    assert signature("failure", "##[error]The action 'Run tests' has timed out after 20 minutes.") == "容量"
