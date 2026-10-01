"""媒体无关的供应商任务契约：状态、响应阶段与续跑过期错误。"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

ProviderResponseStage = Literal["submit", "poll", "result"]


class ResumeExpiredError(RuntimeError):
    """Provider 端 job 已过期或未找到——重启自愈无法接续，须走 mark_failed。

    Worker finally 据 ``isinstance(exc, ResumeExpiredError)`` 给 error_message
    加 ``[resume_expired]`` 前缀（agent-facing，i18n 豁免），运维分析可见。
    """

    def __init__(self, *, job_id: str, provider: str, message: str = "") -> None:
        self.job_id = job_id
        self.provider = provider
        super().__init__(message or f"resume job {job_id} expired or not found on provider {provider}")


class ProviderJobStatus(StrEnum):
    """供应商异步任务状态的 canonical 分档。

    ``EXPIRED`` 独立于 ``FAILED``：调用方据其按 generate / resume 上下文分流抛
    ``RuntimeError`` / ``ResumeExpiredError``，后者驱动 worker 的 ``[resume_expired]``
    前缀与「不再尝试重启自愈」判定。折进 failed 会静默吃掉这条分流。没有过期语义的端点
    在本分档之上自行折叠。
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    EXPIRED = "expired"


TERMINAL_PROVIDER_STATUSES: frozenset[ProviderJobStatus] = frozenset(
    {ProviderJobStatus.SUCCEEDED, ProviderJobStatus.FAILED, ProviderJobStatus.EXPIRED}
)

# 跨厂商状态同义词表（lowercase + strip 后查表）。兼容代理网关会透传底层厂商状态串。
_PROVIDER_STATUS_SYNONYMS: dict[str, ProviderJobStatus] = {
    "completed": ProviderJobStatus.SUCCEEDED,
    "succeeded": ProviderJobStatus.SUCCEEDED,
    "succeed": ProviderJobStatus.SUCCEEDED,
    "success": ProviderJobStatus.SUCCEEDED,
    "failed": ProviderJobStatus.FAILED,
    "fail": ProviderJobStatus.FAILED,
    "error": ProviderJobStatus.FAILED,
    "canceled": ProviderJobStatus.FAILED,
    "cancelled": ProviderJobStatus.FAILED,
    "expired": ProviderJobStatus.EXPIRED,
    "generating": ProviderJobStatus.RUNNING,
    "in_progress": ProviderJobStatus.RUNNING,
    "running": ProviderJobStatus.RUNNING,
    "processing": ProviderJobStatus.RUNNING,
    "queued": ProviderJobStatus.QUEUED,
    "queueing": ProviderJobStatus.QUEUED,
    "preparing": ProviderJobStatus.QUEUED,
    "submitted": ProviderJobStatus.QUEUED,
    "pending": ProviderJobStatus.QUEUED,
    "created": ProviderJobStatus.QUEUED,
}


def normalize_provider_status(raw: object) -> ProviderJobStatus:
    """任意供应商状态值 → canonical 分档（大小写与首尾空白无关）。

    未登记的状态串一律当 ``RUNNING`` 继续轮询：把未知串判成终态，会让返回非标进行中状态
    （如 ``NOT_START``）的网关触发"下载未就绪任务"。非字符串（缺字段 / None）同理。
    """
    if not isinstance(raw, str):
        return ProviderJobStatus.RUNNING
    return _PROVIDER_STATUS_SYNONYMS.get(raw.strip().lower(), ProviderJobStatus.RUNNING)


__all__ = [
    "TERMINAL_PROVIDER_STATUSES",
    "ProviderJobStatus",
    "ProviderResponseStage",
    "ResumeExpiredError",
    "normalize_provider_status",
]
