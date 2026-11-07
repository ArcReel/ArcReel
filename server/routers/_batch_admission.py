"""Shared browser payloads for batch generation: whole-batch admission and enqueue failures, with explanations in the request language."""

from __future__ import annotations

import logging
from typing import Any

from lib.generation.batch_admission import BatchAdmission
from lib.generation.generation_queue_client import BatchTaskResult
from lib.generation.generation_result import enqueue_problem
from lib.i18n import render_message
from lib.i18n.display_names import DisplayNames
from server.i18n import Translator

logger = logging.getLogger(__name__)


def localized_admission_payload(admission: BatchAdmission, _t: Translator, names: DisplayNames) -> dict[str, Any]:
    """Localize the shared admission envelope for the browser.

    Only the message strings are added: codes, actions, tiers and costs stay
    exactly as the shared seam produced them, so Web and Agent never disagree
    about what happened — only about what language it is read in.
    """

    payload = admission.to_payload()
    units = payload.get("units")
    if isinstance(units, list):
        for unit in units:
            problems = unit.get("problems") if isinstance(unit, dict) else None
            if not isinstance(problems, list):
                continue
            for problem in problems:
                if isinstance(problem, dict):
                    params = problem.get("params")
                    problem["message"] = render_message(
                        str(problem.get("code")), params if isinstance(params, dict) else {}, _t, names
                    )
    return payload


def enqueue_failure_payload(failure: BatchTaskResult, _t: Translator) -> dict[str, Any]:
    """Present an unenqueued target to browsers using the shared problem contract.

    Problem codes and next actions share the Agent source, adding only localized explanation. Raw exception text (`detail`) comes from database and
    queue layers and may expose connection strings or internal topology; keep it in server logs, not browser responses, matching `localized_admission_payload`
    in presenting only controlled problem codes.
    """

    problem = enqueue_problem(failure.error, interrupted=failure.enqueue_interrupted)
    logger.warning("batch enqueue failed for unit %s: %s", failure.resource_id, problem.detail)
    return {
        "unit_id": failure.resource_id,
        "problem": {**problem.model_dump(mode="json", exclude={"detail"}), "message": _t(problem.code)},
    }


__all__ = ["enqueue_failure_payload", "localized_admission_payload"]
