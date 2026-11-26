import json
from pathlib import Path


def test_frozen_peak_reports_record_manifest_lock_wait():
    reports = Path(__file__).resolve().parents[3] / "scripts/perf/before"
    for path in reports.glob("*.json"):
        report = json.loads(path.read_bytes())
        if "peak" in report:
            wait = report["peak"].get("manifest_lock_wait_s")
            assert type(wait) in (float, int), path.name
            assert wait >= 0, path.name
