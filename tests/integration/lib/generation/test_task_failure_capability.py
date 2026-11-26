"""能力类失败原因的落库编码与多语言渲染。"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from arcreel_market_core.video_backend_contract import VideoCapabilityError
from lib.backends.http_status_errors import ArtifactDownloadError, ProviderRejectedError
from lib.backends.image_backends.base import ImageCapabilityError
from lib.config.resolver import ImageBucketCapabilityError, VideoBucketCapabilityError, VideoGenerationType
from lib.custom_provider.comfyui.failures import ComfyuiError
from lib.db.repositories.task_repo import _encode_bounded_cascade_failure
from lib.generation import task_failure
from lib.generation.task_failure import (
    CAPABILITY_FAILURE_CODES,
    FAILURE_CODE_KEYS,
    REFERENCE_PROJECTION_FAILURE_CODES,
    VIDEO_REQUEST_FACTS_FAILURE_CODES,
    bound_reason,
    collapse_cascade_reason,
    encode_failure,
    render_failure,
)
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.generation.video_request_facts import VideoRequestFactsError, VideoRequestFactsFailure
from lib.i18n import MESSAGES
from lib.i18n import _ as i18n_translate
from lib.infra import error_params
from lib.references.reference_compression import ReferencePayloadFloorError
from lib.script.reference_video.request_projection import ProjectionProblem, ReferenceProjectionBlockedError
from tests.factories import make_display_names


@pytest.mark.parametrize("code", sorted(CAPABILITY_FAILURE_CODES))
def test_capability_code_has_all_three_locales(code):
    """每个 code 就是 errors 目录的 key（identity 映射），三语都必须有模板。"""
    assert FAILURE_CODE_KEYS[code] == code
    for locale in ("zh", "en", "vi"):
        assert code in MESSAGES[locale], f"{locale} 缺少 {code}"


def _translator(locale: str):
    """与 ``lib.i18n.get_translator`` 同构：把 locale 绑进去，读侧就是这么调的。"""

    def translate(key: str, **params) -> str:
        return i18n_translate(key, locale=locale, **params)

    return translate


def test_stored_reason_renders_per_locale():
    """同一条落库 error_message 在三语下渲染成三种文案。"""
    stored = encode_task_failure_message(
        VideoCapabilityError("video_duration_not_supported", duration=7, supported="5, 10")
    )
    assert stored == '[video_duration_not_supported] {"duration": 7, "supported": "5, 10"}'

    for locale in ("zh", "en", "vi"):
        expected = MESSAGES[locale]["video_duration_not_supported"].format(duration=7, supported="5, 10")
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == expected


def test_artifact_download_failure_is_eligible_for_retry_download():
    stored = encode_task_failure_message(ArtifactDownloadError(detail="cdn unavailable"))

    assert stored == '[artifact_download_failed] {"detail": "cdn unavailable"}'
    for locale in ("zh", "en", "vi"):
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == MESSAGES[locale][
            "task_fail_artifact_download_failed"
        ].format(detail="cdn unavailable")


def test_an_endpoint_whose_media_runtime_is_missing_fails_in_the_reader_s_language():
    """媒体类型不是 ComfyUI 端点认得的那两个（schema 之外，手工改库才会出现）时构造 backend 即失败。

    那一笔必须落成结构化失败：落一段裸文本的话，非中文用户在任务列表里看到的是一句中文。
    """
    stored = encode_task_failure_message(
        ComfyuiError("provider_unsupported_media", provider_id="custom-1", media_type="audio")
    )

    assert stored.startswith("[provider_unsupported_media]")
    for locale in ("zh", "en", "vi"):
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == MESSAGES[locale][
            "task_fail_provider_unsupported_media"
        ].format(**make_display_names(locale).apply({"provider_id": "custom-1", "media_type": "audio"}))


def test_encode_covers_every_capability_exception_type():
    """三类能力异常都走结构化编码，不再落成烘焙文案。"""
    cases = [
        ImageCapabilityError("image_endpoint_mismatch_no_t2i", model="gpt-image-1"),
        VideoCapabilityError("video_start_image_unreadable", model="kling-v1", name="a.png"),
        ReferencePayloadFloorError(),
    ]
    for exc in cases:
        stored = encode_task_failure_message(exc)
        assert stored.startswith(f"[{exc.code}]")
        assert render_failure(stored, _translator("en"), make_display_names("en")) != stored


@pytest.mark.parametrize("code", sorted(REFERENCE_PROJECTION_FAILURE_CODES))
def test_projection_failure_codes_are_machine_encodable_in_all_locales(code: str):
    assert FAILURE_CODE_KEYS[code] == code
    for locale in ("zh", "en", "vi"):
        assert code in MESSAGES[locale], f"{locale} 缺少 {code}"


@pytest.mark.parametrize(
    ("problem", "expected_params"),
    [
        (
            ProjectionProblem(
                code="reference_supported_durations_invalid",
                blocking=True,
                params=(("provider", "fake"), ("model", "bad-model")),
            ),
            {"provider": "fake", "model": "bad-model"},
        ),
        (
            ProjectionProblem(
                code="reference_asset_missing",
                blocking=True,
                params=(
                    ("missing", (("character", "张三"),)),
                    ("missing_text", "character: 张三"),
                ),
            ),
            {
                "missing": (("character", "张三"),),
                "missing_text": "character: 张三",
            },
        ),
    ],
)
def test_projection_failure_preserves_canonical_code_and_params_for_localized_tasks(
    problem: ProjectionProblem, expected_params: dict[str, object]
):
    stored = encode_task_failure_message(ReferenceProjectionBlockedError(problem))
    assert json.loads(stored.split("] ", 1)[1]) == json.loads(json.dumps(expected_params, ensure_ascii=False))
    for locale in ("zh", "en", "vi"):
        expected = MESSAGES[locale][problem.code].format(**make_display_names(locale).apply(expected_params))
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == expected


@pytest.mark.parametrize("code", sorted(VIDEO_REQUEST_FACTS_FAILURE_CODES))
def test_video_request_facts_failure_codes_are_machine_encodable_in_all_locales(code: str) -> None:
    assert FAILURE_CODE_KEYS[code] == code
    for locale in ("zh", "en", "vi"):
        assert code in MESSAGES[locale], f"{locale} 缺少 {code}"


def test_video_request_facts_rejection_keeps_code_and_params_for_localized_tasks() -> None:
    params = {"provider": "gemini-aistudio", "model": "veo", "resolution": "1080p", "capability": "i2v"}
    failure = VideoRequestFactsFailure("video_supported_durations_incompatible", tuple(params.items()))

    stored = encode_task_failure_message(VideoRequestFactsError(failure))

    assert stored.startswith("[video_supported_durations_incompatible]")
    assert json.loads(stored.split("] ", 1)[1]) == params
    for locale in ("zh", "en", "vi"):
        expected = MESSAGES[locale]["video_supported_durations_incompatible"].format(
            **make_display_names(locale).apply(params)
        )
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == expected


_RETIRED_NARRATED_VIDEO_CODES = (
    "tts_duration_endpoint_fixed",
    "video_shorter_than_tts",
    "video_duration_unavailable",
    "video_request_cost_unavailable",
    "tts_conflicts_with_active_narrated_video",
    "tts_missing",
    "tts_generating",
    "tts_stale",
    "tts_state_unavailable",
    "tts_duration_unavailable",
    "tts_not_configured",
)


@pytest.mark.parametrize("code", _RETIRED_NARRATED_VIDEO_CODES)
def test_retired_narrated_video_codes_are_gone_and_historic_rows_pass_through(code: str) -> None:
    assert code not in FAILURE_CODE_KEYS
    for locale in ("zh", "en", "vi"):
        assert code not in MESSAGES[locale]
    stored = f'[{code}] {{"resource_id": "E1U01"}}'
    assert render_failure(stored, _translator("zh"), make_display_names("zh")) == stored


@pytest.mark.parametrize(
    ("code", "generation_type"),
    [
        ("video_capability_missing_i2v", "i2v"),
        ("video_capability_missing_r2v", "r2v"),
        ("video_capability_reference_unavailable", "r2v"),
    ],
)
def test_encode_video_bucket_capability_error_renders_per_locale(code: str, generation_type: VideoGenerationType):
    """视频解析闸异常（VideoBucketCapabilityError）同走结构化编码，读侧三语渲染。

    三个 code 逐个断言渲染结果：只覆盖其中一个的话，另两条模板的占位符拼错或参数缺失
    仍会通过 —— key 齐备性由 ``test_video_bucket_codes_registered_with_all_three_locales`` 单独保证。
    """
    exc = VideoBucketCapabilityError(
        code=code,
        generation_type=generation_type,
        provider_id="minimax",
        model_id="MiniMax-Hailuo-2.3",
        message=f"lacks {generation_type}",
    )
    stored = encode_task_failure_message(exc)
    assert stored.startswith(f"[{code}]")
    for locale in ("zh", "en", "vi"):
        expected = MESSAGES[locale][code].format(
            **make_display_names(locale).apply({"provider": "minimax", "model": "MiniMax-Hailuo-2.3"})
        )
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == expected


@pytest.mark.parametrize("generation_type", ["t2i", "i2i"])
def test_encode_image_bucket_capability_error_renders_per_locale(generation_type: str):
    """图片解析闸异常（ImageBucketCapabilityError）与执行层 ImageCapabilityError 共用 code，同走结构化编码。"""
    exc = ImageBucketCapabilityError(generation_type=generation_type, provider_id="custom-3", model_id="relay-img")
    stored = encode_task_failure_message(exc)
    code = f"image_capability_missing_{generation_type}"
    assert stored.startswith(f"[{code}]")
    for locale in ("zh", "en", "vi"):
        expected = MESSAGES[locale][code].format(
            **make_display_names(locale).apply({"provider": "custom-3", "model": "relay-img"})
        )
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == expected


@pytest.mark.parametrize(
    "code",
    [
        "video_capability_missing_i2v",
        "video_capability_missing_r2v",
        "video_capability_reference_unavailable",
    ],
)
def test_video_bucket_codes_registered_with_all_three_locales(code):
    """解析闸的 code 是 errors 目录 key 的 identity 映射，三语模板齐备。"""
    assert FAILURE_CODE_KEYS[code] == code
    for locale in ("zh", "en", "vi"):
        assert code in MESSAGES[locale], f"{locale} 缺少 {code}"


def test_encode_stringifies_non_json_params():
    """params 值不是 JSON 原生类型时降级为字符串，编码不得在失败路径里抛错。

    ``video_duration_invalid`` 回显的是调用方拒绝的原始值，类型不可控。
    """
    stored = encode_task_failure_message(VideoCapabilityError("video_duration_invalid", duration=Path("weird")))
    assert json.loads(stored.split("] ", 1)[1]) == {"duration": "weird"}


def test_bound_reason_preserves_scalar_param_types():
    """裁剪不得改掉同条失败里其它参数的类型。

    裁剪是整个 params 一起过一遍的：只要有任一参数超长触发裁剪，同条失败携带的全部标量都会
    跟着走一遍归一。把 JSON 原生标量转成文本会在重新编码时被加上引号存回去（``42`` 变
    ``"42"``、``True`` 变 ``"true"``、``None`` 变 ``"null"``），落库信封的参数类型就变了；
    模板若用带类型说明符的占位符（如 ``{n:d}``），``str.format`` 抛出的异常会被
    ``lib/i18n/__init__.py::_`` 吞掉，最终把裸占位符显示给用户。
    """
    stored = encode_failure("resume_expired_detail", detail="x" * 3000, retries=3, ratio=1.5, expired=True, note=None)

    bounded = bound_reason(stored, 2000)

    assert len(bounded) <= 2000
    params = json.loads(bounded.split("] ", 1)[1])
    assert params["retries"] == 3
    assert isinstance(params["retries"], int)
    assert params["ratio"] == 1.5
    assert isinstance(params["ratio"], float)
    assert params["expired"] is True
    assert params["note"] is None
    assert len(params["detail"]) < 3000


def test_unregistered_capability_code_degrades_to_passthrough_text():
    """code 未登记时退回非结构化文本，读侧原样透传——失败原因不丢，任务不卡 running。"""
    exc = VideoCapabilityError("video_totally_new_code", model="x")
    stored = encode_task_failure_message(exc)
    assert stored == "video_totally_new_code"
    assert render_failure(stored, _translator("zh"), make_display_names("zh")) == stored


def test_non_capability_exception_still_passes_through():
    stored = encode_task_failure_message(RuntimeError("provider socket closed"))
    assert stored == "provider socket closed"
    assert render_failure(stored, _translator("en"), make_display_names("en")) == stored


def test_provider_rejection_stores_status_and_reason_as_separate_params():
    """上游确定性 4xx：状态码进译文，拒因摘要以独立参数原样留在信封里。"""
    request = httpx.Request("POST", "https://x/v2?api_key=SECRETKEY")
    exc = ProviderRejectedError(
        "400 response for https://x/v2",
        request=request,
        response=httpx.Response(400, request=request),
        provider_reason="InvalidParameter: prompt violates the content policy",
    )

    stored = encode_task_failure_message(exc)

    assert json.loads(stored.split("] ", 1)[1]) == {
        "provider_reason": "InvalidParameter: prompt violates the content policy",
        "status": 400,
    }
    assert "SECRETKEY" not in stored
    for locale in ("zh", "en", "vi"):
        rendered = render_failure(stored, _translator(locale), make_display_names(locale))
        assert rendered == MESSAGES[locale]["task_fail_provider_rejected"].format(status=400)
        assert "InvalidParameter" not in rendered


def test_provider_rejection_without_a_reason_still_stores_the_status():
    """摘要缺席（认证类不透传、空响应体）时仍走结构化信封，读侧照常拿到本地化文案。"""
    request = httpx.Request("POST", "https://x/v2?api_key=SECRETKEY")
    exc = ProviderRejectedError(
        "401 response for https://x/v2",
        request=request,
        response=httpx.Response(401, request=request),
    )

    stored = encode_task_failure_message(exc)

    assert json.loads(stored.split("] ", 1)[1]) == {"status": 401}
    assert "SECRETKEY" not in stored
    for locale in ("zh", "en", "vi"):
        assert render_failure(stored, _translator(locale), make_display_names(locale)) == MESSAGES[locale][
            "task_fail_provider_rejected"
        ].format(status=401)


@pytest.mark.parametrize(
    "legacy",
    [
        None,
        "",
        "<AioRpcError of RPC that terminated with:\n\tstatus = StatusCode.OUT_OF_RANGE>",
        "不支持的资源类型: reference_videos",
        "[video_duration_not_supported] not-json",
        "[some_retired_code] {}",
    ],
)
def test_legacy_rows_render_without_error(legacy):
    """存量行（空值 / 纯文本 / 无法解析的伪结构化）读取不炸，一律原样返回。"""
    assert render_failure(legacy, _translator("en"), make_display_names("en")) == legacy


def _encode_cascade(dependency_task_id: str, reason: str) -> str:
    return encode_failure("cascade_blocked_dependency", dependency_task_id=dependency_task_id, reason=reason)


def test_cascade_reason_renders_upstream_reason_per_locale():
    """被上游连坐的任务不能退化成裸 [code]：级联包裹与其内层原因都按读侧语言渲染。"""
    upstream = encode_task_failure_message(
        VideoCapabilityError("video_duration_not_supported", duration=7, supported="5, 10")
    )
    stored = _encode_cascade("5a38f38e", upstream)

    rendered = {
        locale: render_failure(stored, _translator(locale), make_display_names(locale)) for locale in ("zh", "en", "vi")
    }
    assert len({*rendered.values()}) == 3, rendered
    for locale, text in rendered.items():
        assert "video_duration_not_supported" not in text
        assert "5a38f38e" in text
        inner = MESSAGES[locale]["video_duration_not_supported"].format(duration=7, supported="5, 10")
        assert inner in text


def test_deep_cascade_chain_keeps_root_cause_renderable():
    """长依赖链上每层都要渲染成本地化文案，而不是嵌一段被切坏的内层信封。

    逐层包裹近指数增长：每层把上一层整个信封重新编码进 JSON，转义使长度翻倍，七层左右撑破
    落库预算，裁剪只能从尾部切字符，把内层切在 JSON 中途——外层仍可解析，读侧却把残缺内层
    当普通文本原样嵌进本地化文案。编码前折叠掉中间层后，串长与链深无关。
    """
    upstream = encode_task_failure_message(
        VideoCapabilityError("video_duration_not_supported", duration=7, supported="5, 10")
    )
    inner = MESSAGES["zh"]["video_duration_not_supported"].format(duration=7, supported="5, 10")

    stored = upstream
    lengths = set()
    for depth in range(1, 21):
        stored = _encode_bounded_cascade_failure(dependency_task_id=f"{depth:032d}", reason=stored)
        lengths.add(len(stored))
        rendered = render_failure(stored, _translator("zh"), make_display_names("zh"))
        assert rendered is not None
        assert f"{depth:032d}" in rendered
        assert inner in rendered
        assert "[cascade_blocked_dependency]" not in rendered
        assert "video_duration_not_supported" not in rendered

    assert len(lengths) == 1, f"折叠后串长应与链深无关，实际出现多种长度: {sorted(lengths)}"


def test_collapse_cascade_reason_leaves_non_cascade_reason_untouched():
    """非级联原因（含 provider 原始文本）不该被折叠动到。"""
    upstream = encode_task_failure_message(VideoCapabilityError("video_duration_invalid", duration="x"))
    assert collapse_cascade_reason(upstream) == upstream
    assert collapse_cascade_reason("provider rejected") == "provider rejected"


def test_cascade_reason_with_unstructured_upstream_still_renders():
    """上游原因是 provider 原始异常文本时，包裹层仍本地化，内层原样透传。"""
    stored = _encode_cascade("5a38f38e", "provider rejected")
    rendered = render_failure(stored, _translator("zh"), make_display_names("zh"))
    assert rendered != stored
    assert "provider rejected" in (rendered or "")


def test_render_degrades_when_json_hits_recursion_limit(monkeypatch):
    """params 短而深时 ``json.loads`` 自身会撞递归上限。

    渲染发生在 HTTP 请求内，异常逸出就是 500，因此与畸形 JSON 同样处置：原样透传，原因不丢。
    """
    stored = '[video_reference_images_unreadable] {"names": [[[1]]]}'

    def _boom(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(task_failure.json, "loads", _boom)

    assert render_failure(stored, _translator("en"), make_display_names("en")) == stored


def test_bound_reason_shrinks_oversized_non_string_params():
    """非字符串参数撑爆预算时也要留下可解析的信封。

    ``video_duration_invalid`` 回显调用方拒绝的原始配置值，导入或手工编辑的 project.json
    能把它写成一个大数组；JSON 原生类型不经 ``default=str``，没有字符串参数可收窄时旧路径
    会退到裸切片，把信封切在 JSON 中途，读侧解析失败后原样吐出整串机器码。
    """
    stored = encode_task_failure_message(VideoCapabilityError("video_duration_invalid", duration=list(range(2000))))
    assert len(stored) > 2000

    bounded = bound_reason(stored, 2000)

    assert len(bounded) <= 2000
    rendered = render_failure(bounded, _translator("en"), make_display_names("en"))
    assert rendered is not None
    assert rendered != bounded
    assert "video_duration_invalid" not in rendered


def test_bound_reason_shrinks_escape_heavy_params():
    """转义后翻倍的参数也要收窄成合法信封，而不是被判定「砍不动」退回裸切片。

    超额长度以编码后的字符计，而 ``"`` / ``\\`` 经 JSON 转义占两列：拿它直接当要砍的原始
    字符数比较，会把原始长度小于超额长度、但实际收窄后装得下的参数误判成无从收窄。
    """
    names = "\\" * 900
    stored = encode_failure("image_reference_images_unreadable", names=names)
    assert len(stored) > 2 * len(names)

    bounded = bound_reason(stored, 900)

    assert len(bounded) <= 900
    rendered = render_failure(bounded, _translator("en"), make_display_names("en"))
    assert rendered is not None
    assert "image_reference_images_unreadable" not in rendered
    assert len(json.loads(bounded.split(" ", 1)[1])["names"]) > 100


def _oversized_envelope() -> str:
    """一个超出 2000 字符预算、且整体仍是合法 ``[code] {params}`` 的信封。

    超长部分必须落在 JSON 内部：拼在 ``}`` 之后的尾巴会让 ``_STRUCTURED_RE`` 失配，裁剪在
    进入 ``json`` 之前就返回，测不到本节要测的降级路径。
    """
    stored = encode_failure("video_duration_invalid", duration="x" * 3000)
    assert len(stored) > 2000
    return stored


def test_as_shrinkable_degrades_when_param_json_hits_recursion_limit(monkeypatch):
    """嵌套过深的参数量不出长度时降级为省略号，而不是把异常抛进落库路径。

    只断言 ``as_shrinkable`` 本身：经它归一后 ``params`` 全是扁平字符串，``encode_failure``
    的 ``dumps`` 已无从撞上限，再去断言 ``bound_reason`` 只能靠全局打桩造出不可达的场景。
    """

    def _boom(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(error_params.json, "dumps", _boom)

    assert error_params.as_shrinkable([[[1]]]) == "…"


def test_bound_reason_degrades_when_params_json_load_hits_recursion_limit(monkeypatch):
    """params 深到 ``json.loads`` 自身撞递归上限时退回字符裁剪，而不是把异常抛进落库路径。"""
    stored = _oversized_envelope()

    def _boom(*_args, **_kwargs):
        raise RecursionError("maximum recursion depth exceeded")

    monkeypatch.setattr(task_failure.json, "loads", _boom)

    assert bound_reason(stored, 2000) == stored[:2000]
