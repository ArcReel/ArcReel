import pytest

from scripts import ci_reuse


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("equivalent", 42),
        ("direct", None),
        ("advanced-base", None),
        ("different-tree", None),
        ("gate-change", None),
        ("skipped-job", None),
        ("skipped-step", None),
        ("missing-job", None),
        ("missing-page", None),
        ("missing-checkout", None),
    ],
)
def test_push_evidence(monkeypatch, case, expected):
    sha, head, base, checkout = (digit * 40 for digit in "1234")
    domains = {d: "true" if d in {"backend", "frontend"} else "false" for d in ci_reuse.DOMAINS}
    names = {
        "changes",
        "ci-required",
        "conventions",
        "test-lint",
        "frontend-static",
        "frontend-tests",
        "e2e-live",
        "E2E fixture recording drift",
        "frontend-e2e (1/2)",
        "frontend-e2e (2/2)",
        "Backend tests (unit)",
        "Backend tests (integration)",
        "Backend tests (market-core)",
        "Backend tests (catalog-core)",
        "backend-static",
        "postgres-compat (16)",
    }
    jobs = []
    for name in sorted(names):
        step = f"PR checkout 7 {checkout}" if name == "changes" and case != "missing-checkout" else "Run tests"
        result = "skipped" if case == "skipped-job" and name == "Backend tests (unit)" else "success"
        status = "skipped" if case == "skipped-step" and name == "e2e-live" else "success"
        if case != "missing-job" or name != "frontend-e2e (2/2)":
            jobs.append(
                {
                    "name": name,
                    "conclusion": result,
                    "steps": [
                        {"name": "Set up job", "conclusion": "success"},
                        {"name": step, "conclusion": status},
                        {"name": "Complete job", "conclusion": "success"},
                    ],
                }
            )
    pull = {
        "number": 7,
        "merged_at": "today",
        "merge_commit_sha": sha,
        "base": {"ref": "main", "repo": {"full_name": "a/b"}},
        "head": {"sha": head},
    }
    endpoints = {
        f"commits/{sha}/pulls": [] if case == "direct" else [pull],
        f"actions/workflows/test.yml/runs?event=pull_request&head_sha={head}&per_page=1": {
            "workflow_runs": [
                {
                    "head_sha": head,
                    "conclusion": "success",
                    "event": "pull_request",
                    "id": 42,
                    "run_attempt": 2,
                    "pull_requests": [{"number": 7, "base": {"ref": "main"}}],
                }
            ]
        },
        "actions/runs/42/attempts/2/jobs?per_page=100": {
            "jobs": jobs,
            "total_count": len(jobs) + (case == "missing-page"),
        },
        f"git/commits/{checkout}": {
            "parents": [{"sha": "old" if case == "advanced-base" else base}, {"sha": head}],
            "tree": {"sha": "other" if case == "different-tree" else "tree"},
        },
    }

    def git(*args):
        if args[0] == "diff":
            return ".github/workflows/test.yml\0" if case == "gate-change" else "frontend/src/main.tsx\0lib/foo.py\0"
        return base if args[-1].endswith("^1") else "tree"

    monkeypatch.setattr(ci_reuse, "git", git)
    monkeypatch.setattr(ci_reuse, "api", endpoints.__getitem__)
    monkeypatch.setenv("GITHUB_REPOSITORY", "a/b")
    assert ci_reuse.verified_run(base, sha, domains) == expected
