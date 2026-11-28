"""对象存储分级连接探针：依次验证桶探测、受管前缀写入、元信息核验、无登录态预签名下载与探针清理。

探针只在受管前缀 ``arcreel-relay/probes/`` 下写入当次唯一的临时对象，并在写入成功后始终尝试删除本次探针对象；
诊断信息与日志严格剥离预签名查询串与访问凭证。
"""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from typing import Literal
from urllib.parse import urlsplit
from xml.etree import ElementTree

import httpx

from lib.infra.logging_utils import redact_diagnostic_text
from lib.object_storage.client import (
    ObjectStorageClient,
    ObjectStorageError,
    classify_s3_error,
)
from lib.object_storage.relay import RELAY_PREFIX

PROBE_PREFIX = f"{RELAY_PREFIX}probes/"
PROBE_PRESIGN_EXPIRES_SECONDS = 300
PROBE_HTTP_TIMEOUT_SECONDS = 15.0

ProbeStepName = Literal["head_bucket", "put_object", "head_object", "presigned_get", "delete_object"]
PROBE_STEP_ORDER: tuple[ProbeStepName, ...] = (
    "head_bucket",
    "put_object",
    "head_object",
    "presigned_get",
    "delete_object",
)
ProbeStepStatus = Literal["passed", "failed", "skipped"]
ProbeErrorKind = Literal[
    "auth",
    "not_found",
    "denied",
    "network",
    "invalid_config",
    "provider",
    "metadata_mismatch",
    "digest_mismatch",
]

_URL_QUERY_RE = re.compile(r"(https?://[^\s?#\"'<>]+)\?[^\s#\"'<>]*", re.IGNORECASE)


@dataclass(frozen=True)
class ProbeStepResult:
    step: ProbeStepName
    status: ProbeStepStatus
    error_kind: ProbeErrorKind | None = None
    error_code: str = ""
    status_code: int | None = None
    error_message: str = ""


@dataclass(frozen=True)
class ObjectStorageProbeResult:
    success: bool
    steps: tuple[ProbeStepResult, ...]
    residual_object_key: str | None = None


def probe_object_key() -> str:
    """生成本次探针在受管前缀下的唯一临时对象 key。"""
    return f"{PROBE_PREFIX}{uuid.uuid4().hex}.bin"


def sanitize_probe_diagnostic(text: str, *, presigned_url: str | None = None) -> str:
    """移除预签名 URL 的查询串并对敏感凭据脱敏。"""
    cleaned = text
    if presigned_url:
        parsed = urlsplit(presigned_url)
        safe_base = f"{parsed.scheme}://{parsed.netloc}{parsed.path}" if parsed.scheme and parsed.netloc else ""
        cleaned = cleaned.replace(presigned_url, f"{safe_base}?<redacted>" if safe_base else "<redacted-url>")
        if parsed.query:
            cleaned = cleaned.replace(parsed.query, "<redacted>")
    cleaned = _URL_QUERY_RE.sub(r"\1?<redacted>", cleaned)
    return redact_diagnostic_text(cleaned).strip()


def _parse_s3_xml_error(body: bytes) -> tuple[str, str]:
    if not body or b"<" not in body[:64]:
        return "", ""
    try:
        root = ElementTree.fromstring(body[:8192])
    except ElementTree.ParseError:
        return "", ""
    code = ""
    message = ""
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        text = (elem.text or "").strip()
        if tag == "Code" and not code:
            code = text
        elif tag == "Message" and not message:
            message = text
    return code, message


def _classify_http_status(status_code: int, code: str) -> ProbeErrorKind:
    classified = classify_s3_error(status_code, code)
    if classified == "provider" and status_code in (301, 307, 400):
        return "invalid_config"
    return classified


def _failed_from_storage_error(step: ProbeStepName, exc: ObjectStorageError) -> ProbeStepResult:
    return ProbeStepResult(
        step=step,
        status="failed",
        error_kind=exc.kind,
        error_code=exc.code,
        status_code=exc.status_code,
        error_message=sanitize_probe_diagnostic(exc.message),
    )


async def _fetch_presigned(url: str, http_client: httpx.AsyncClient | None) -> httpx.Response:
    if http_client is not None:
        return await http_client.get(url)
    timeout = httpx.Timeout(PROBE_HTTP_TIMEOUT_SECONDS, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as ephemeral:
        return await ephemeral.get(url)


async def probe_connection(
    client: ObjectStorageClient,
    *,
    http_client: httpx.AsyncClient | None = None,
) -> ObjectStorageProbeResult:
    """对对象存储连接执行 5 步分级探针并返回完整分项结果。"""
    key = probe_object_key()
    payload = f"arcreel-s3-probe-{uuid.uuid4().hex}\n".encode()
    expected_sha256 = hashlib.sha256(payload).hexdigest()

    results: dict[ProbeStepName, ProbeStepResult] = {}

    try:
        await client.check()
    except ObjectStorageError as exc:
        results["head_bucket"] = _failed_from_storage_error("head_bucket", exc)
        return _finalize(results, residual_object_key=None)
    results["head_bucket"] = ProbeStepResult(step="head_bucket", status="passed")

    try:
        await client.put_bytes(key, payload, content_type="application/octet-stream")
    except ObjectStorageError as exc:
        results["put_object"] = _failed_from_storage_error("put_object", exc)
        return _finalize(results, residual_object_key=None)
    results["put_object"] = ProbeStepResult(step="put_object", status="passed")

    residual_object_key: str | None = None
    try:
        try:
            info = await client.head(key)
        except ObjectStorageError as exc:
            results["head_object"] = _failed_from_storage_error("head_object", exc)
        else:
            if info.size != len(payload):
                results["head_object"] = ProbeStepResult(
                    step="head_object",
                    status="failed",
                    error_kind="metadata_mismatch",
                    error_code="ContentLengthMismatch",
                    error_message=f"expected={len(payload)} actual={info.size}",
                )
            else:
                results["head_object"] = ProbeStepResult(step="head_object", status="passed")

        if results["head_object"].status == "passed":
            results["presigned_get"] = await _probe_presigned_get(
                client,
                key,
                expected_sha256=expected_sha256,
                expected_size=len(payload),
                http_client=http_client,
            )
    finally:
        try:
            await client.delete(key)
        except ObjectStorageError as exc:
            results["delete_object"] = _failed_from_storage_error("delete_object", exc)
            residual_object_key = key
        else:
            results["delete_object"] = ProbeStepResult(step="delete_object", status="passed")

    return _finalize(results, residual_object_key=residual_object_key)


async def _probe_presigned_get(
    client: ObjectStorageClient,
    key: str,
    *,
    expected_sha256: str,
    expected_size: int,
    http_client: httpx.AsyncClient | None,
) -> ProbeStepResult:
    try:
        presigned_url = await client.presign_get(key, expires_seconds=PROBE_PRESIGN_EXPIRES_SECONDS)
    except ObjectStorageError as exc:
        return _failed_from_storage_error("presigned_get", exc)

    try:
        response = await _fetch_presigned(presigned_url, http_client)
    except httpx.HTTPError as exc:
        return ProbeStepResult(
            step="presigned_get",
            status="failed",
            error_kind="network",
            error_code=type(exc).__name__,
            error_message=sanitize_probe_diagnostic(str(exc), presigned_url=presigned_url),
        )

    if response.status_code != 200:
        xml_code, xml_message = _parse_s3_xml_error(response.content)
        code = xml_code or f"HTTP_{response.status_code}"
        raw_message = xml_message or response.reason_phrase or ""
        return ProbeStepResult(
            step="presigned_get",
            status="failed",
            error_kind=_classify_http_status(response.status_code, xml_code),
            error_code=code,
            status_code=response.status_code,
            error_message=sanitize_probe_diagnostic(raw_message, presigned_url=presigned_url),
        )

    actual_sha256 = hashlib.sha256(response.content).hexdigest()
    if actual_sha256 != expected_sha256:
        return ProbeStepResult(
            step="presigned_get",
            status="failed",
            error_kind="digest_mismatch",
            error_code="Sha256Mismatch",
            status_code=response.status_code,
            error_message=f"expected_bytes={expected_size} actual_bytes={len(response.content)}",
        )
    return ProbeStepResult(step="presigned_get", status="passed")


def _finalize(
    results: dict[ProbeStepName, ProbeStepResult],
    *,
    residual_object_key: str | None,
) -> ObjectStorageProbeResult:
    ordered = tuple(results.get(step, ProbeStepResult(step=step, status="skipped")) for step in PROBE_STEP_ORDER)
    return ObjectStorageProbeResult(
        success=all(item.status == "passed" for item in ordered),
        steps=ordered,
        residual_object_key=residual_object_key,
    )
