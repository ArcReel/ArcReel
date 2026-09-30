"""批量生成入口共用的浏览器载荷：整批准入结论与入队失败，按请求语言补上说明文字。"""

from __future__ import annotations

import logging
from typing import Any

from lib.generation.batch_admission import BatchAdmission
from lib.generation.generation_queue_client import BatchTaskResult
from lib.generation.generation_result import enqueue_problem
from server.i18n import Translator

logger = logging.getLogger(__name__)


def localized_admission_payload(admission: BatchAdmission, _t: Translator) -> dict[str, Any]:
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
                    problem["message"] = _t(str(problem.get("code")), **(params if isinstance(params, dict) else {}))
    return payload


def enqueue_failure_payload(failure: BatchTaskResult, _t: Translator) -> dict[str, Any]:
    """一个没能入队的目标，按共享契约的问题形状转述给浏览器。

    问题码与下一步动作与 Agent 侧同源，只多一句本地化说明。原始异常文本（`detail`）来自数据库与
    队列层，可能带出连接串或内部拓扑，因此只落服务端日志，不进浏览器响应体——与 `localized_admission_payload`
    只转述受控问题码的姿态一致。
    """

    problem = enqueue_problem(failure.error, interrupted=failure.enqueue_interrupted)
    logger.warning("batch enqueue failed for unit %s: %s", failure.resource_id, problem.detail)
    return {
        "unit_id": failure.resource_id,
        "problem": {**problem.model_dump(mode="json", exclude={"detail"}), "message": _t(problem.code)},
    }


__all__ = ["enqueue_failure_payload", "localized_admission_payload"]
