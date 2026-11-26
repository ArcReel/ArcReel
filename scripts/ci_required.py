"""Check the results of the jobs selected for this Tests run."""

import json
import os
from typing import Any

from scripts.ci_reuse import DOMAINS, required_job_ids


def failures(needs: dict[str, Any]) -> list[str]:
    if needs.get("changes", {}).get("result") != "success":
        return ["changes did not succeed"]
    outputs = needs["changes"]["outputs"]
    required = required_job_ids(
        {domain: outputs[domain] for domain in DOMAINS},
        full=outputs["full"] == "true",
        reused=outputs.get("reused") == "true",
    )
    errors = []
    for job in sorted(required | needs.keys()):
        result = needs.get(job, {}).get("result", "missing")
        if result != "success" and (job in required or result != "skipped"):
            errors.append(f"{job}: {result}")
    return errors


if __name__ == "__main__":
    problems = failures(json.loads(os.environ["JOBS"]))
    for problem in problems:
        print(problem)
    raise SystemExit(bool(problems))
