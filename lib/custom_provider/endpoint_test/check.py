"""验证响应：拿一份供应商的真实响应，逐条路径报告命中情况与最终判定，零费用。

逐条求值与运行时读的是同一份归一化规则（:func:`normalize_extract_spec`），最终判定则直接调运行时
按媒体类型选用的那一份判读函数（视频 :func:`extract_provider_state`、图片
:func:`extract_image_state`）——验证之所以能替代真花钱的调用，全靠这两处不另写一遍。
本模块产出的报告同时是测试连接结果体里的「逐阶段提取」段。

``image_b64`` 取到的是整张图的 base64，报告里只给解出的字节数（解不出图片时为 ``None``），
不回显原串。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from arcreel_market_core.endpoint_definition import (
    JsonPathEvaluationError,
    definition_media_type,
    extract_value,
    normalize_extract_spec,
)
from arcreel_market_core.video_backend_contract import ProviderJobStatus
from lib.custom_provider.declarative_backend import (
    DeclarativeRuntimeError,
    JobState,
    ProviderState,
    StateReader,
    extract_provider_state,
    text_or_none,
)
from lib.custom_provider.declarative_image_backend import ImageJobState, decode_image_b64, extract_image_state

from .errors import EndpointTestDefinitionError

#: 可验证的三节。``result`` 只有定义声明了二次取件节时才存在。
STAGES = ("submit", "poll", "result")

#: 媒体类型 → 运行时判读响应的函数。
_STATE_READERS: dict[str, StateReader[Any]] = {
    "video": extract_provider_state,
    "image": extract_image_state,
}


@dataclass(frozen=True)
class PathAttempt:
    """优先级数组里的一条路径的求值结果。"""

    path: str
    json_decode: bool
    matched: bool
    value: object | None


@dataclass(frozen=True)
class FieldExtraction:
    """一个 extract 键的完整报告：逐条路径 + 最终取到的值。"""

    key: str
    attempts: tuple[PathAttempt, ...]
    value: object | None


@dataclass(frozen=True)
class StageReport:
    """一节的验证结果。``status`` 与 ``verdict`` 取自运行时的判读实现。"""

    stage: str
    fields: tuple[FieldExtraction, ...]
    task_id: str | None
    raw_status: object | None
    status: str | None
    video_url: str | None
    image_url: str | None
    #: 按 ``image_b64`` 取到的图片解码后的字节数；没取到或解不出图片为 ``None``。
    image_bytes: int | None
    error: str | None
    result_id: str | None
    duration_seconds: int | None


def parse_response_body(raw: object) -> object:
    """把粘进来的响应体归一：字符串先按 JSON 解析，解析不了就按原始字符串验证。"""
    if not isinstance(raw, str):
        return raw
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def check_response(definition: Mapping[str, Any], stage: str, response_body: object) -> StageReport:
    """验证一节的取值路径与状态映射。定义须已过共享校验器。

    Raises:
        EndpointTestDefinitionError: 该节不存在，或提取规则在这份响应上无法求值。
    """
    section = definition.get(stage)
    if stage not in STAGES or not isinstance(section, Mapping):
        raise EndpointTestDefinitionError.from_render_failure(stage, f"definition has no {stage} section")
    extract = section["extract"]
    fields = tuple(_field_reports(extract, response_body))
    values = {field.key: field.value for field in fields}
    # 提交节没有状态可读：任何 2xx 都算提交成功，判据是能不能取到 task_id。二次取件节则由运行时
    # 按「已成功」读——那一节只在轮询判成功之后才发得出去。
    try:
        state = _runtime_state(definition, stage, extract, response_body)
    except DeclarativeRuntimeError as exc:
        raise EndpointTestDefinitionError.from_render_failure(f"{stage}.extract", str(exc)) from exc
    video = state if isinstance(state, ProviderState) else None
    image = state if isinstance(state, ImageJobState) else None
    return StageReport(
        stage=stage,
        fields=fields,
        task_id=_task_id(values.get("task_id")) if stage == "submit" else None,
        raw_status=values.get("status"),
        status=state.status.value if state else None,
        video_url=video.video_url if video else None,
        image_url=image.image_url if image else None,
        image_bytes=_image_bytes(image.image_b64) if image and image.image_b64 else None,
        error=state.error if state else text_or_none(values.get("error")),
        result_id=state.result_id if state else None,
        duration_seconds=video.duration_seconds if video else None,
    )


def stage_report_payload(report: StageReport) -> dict[str, Any]:
    """把一节提取报告拍平成接口契约的形状。验证响应与测试连接共用同一形状。"""
    return {
        "stage": report.stage,
        "fields": [
            {
                "key": field_report.key,
                "value": field_report.value,
                "attempts": [
                    {
                        "path": attempt.path,
                        "json_decode": attempt.json_decode,
                        "matched": attempt.matched,
                        "value": attempt.value,
                    }
                    for attempt in field_report.attempts
                ],
            }
            for field_report in report.fields
        ],
        "task_id": report.task_id,
        "raw_status": report.raw_status,
        "status": report.status,
        "video_url": report.video_url,
        "image_url": report.image_url,
        "image_bytes": report.image_bytes,
        "error": report.error,
        "result_id": report.result_id,
        "duration_seconds": report.duration_seconds,
    }


def _runtime_state(
    definition: Mapping[str, Any], stage: str, extract: Mapping[str, Any], body: object
) -> JobState | None:
    if stage == "submit":
        return None
    return _STATE_READERS[definition_media_type(definition)](
        body,
        extract,
        status_map=definition.get("status_map"),
        status=ProviderJobStatus.SUCCEEDED if stage == "result" else None,
    )


def _task_id(value: object | None) -> str | None:
    """与运行时同一条口径：列表与对象不算命中，标量按字符串化收。"""
    if isinstance(value, (list, dict)):
        return None
    return text_or_none(value)


def _image_bytes(text: str) -> int | None:
    try:
        return len(decode_image_b64(text))
    except ValueError:
        return None


def _image_summary(value: object | None) -> object | None:
    """``image_b64`` 的命中值换成字节数摘要。"""
    if value is None:
        return None
    text = text_or_none(value)
    return {"image_bytes": _image_bytes(text) if text else None}


def _summarized(report: FieldExtraction) -> FieldExtraction:
    return replace(
        report,
        attempts=tuple(replace(attempt, value=_image_summary(attempt.value)) for attempt in report.attempts),
        value=_image_summary(report.value),
    )


def _field_reports(extract: Mapping[str, Any], body: object):
    for key, spec in extract.items():
        if key == "usage" and isinstance(spec, Mapping):
            for usage_key, usage_spec in spec.items():
                yield _field_report(f"usage.{usage_key}", usage_spec, body)
            continue
        report = _field_report(key, spec, body)
        yield _summarized(report) if key == "image_b64" else report


def _field_report(key: str, spec: object, body: object) -> FieldExtraction:
    try:
        paths, accept = normalize_extract_spec(spec)
    except (TypeError, ValueError) as exc:
        raise EndpointTestDefinitionError.from_render_failure(key, str(exc)) from exc
    attempts: list[PathAttempt] = []
    value: object | None = None
    for item in paths:
        try:
            # 逐条走同一个 extract_value：优先级语义、json_decode 后缀与 accept 过滤都只有一份实现。
            hit = extract_value({"paths": [item], "accept": accept}, body)
        except JsonPathEvaluationError as exc:
            raise EndpointTestDefinitionError.from_render_failure(key, exc) from exc
        except (TypeError, ValueError) as exc:
            raise EndpointTestDefinitionError.from_render_failure(key, str(exc)) from exc
        attempts.append(
            PathAttempt(
                path=str(item["path"]) if isinstance(item, Mapping) else str(item),
                json_decode=isinstance(item, Mapping),
                matched=hit is not None,
                value=hit,
            )
        )
        if hit is not None and value is None:
            value = hit
    return FieldExtraction(key=key, attempts=tuple(attempts), value=value)
