"""方舟 Agent Plan 语音合成经真实的音频生成路径：装配后端、记账、不可变版本与使用记录，上游是本机回环替身。

旁白配音与角色试音都走 ``MediaGenerator.generate_audio_async``，区别只在资源 ID 与产物所属资源，两种都跑一遍。
"""

from __future__ import annotations

import asyncio
import base64
import io
import json
import wave
from dataclasses import replace
from functools import partial

import httpx
import pytest

from arcreel_catalog_core import parse_entry
from lib.backends.audio_backends.ark_agent_plan import ArkAgentPlanAudioBackend
from lib.backends.backend_assembly.specs import builtin_model_endpoint
from lib.billing.ledger import Ledger
from lib.catalog import query
from lib.db.repositories.usage_repo import UsageRepository
from lib.generation.media_generator import MediaGenerator
from lib.generation.task_failure import render_failure
from lib.generation.task_failure_encoding import encode_task_failure_message
from lib.i18n import _ as translate
from lib.infra.api_errors import ApiError
from server.services.tasks.generation_tasks import voice_sample_resource_id
from tests.catalog_support import catalog_of, install_catalog
from tests.factories import make_display_names
from tests.fakes import write_generation_targets
from tests.loopback_http import ScriptedReply, loopback_server

pytestmark = pytest.mark.local_port

_MODEL = "seed-tts-2.0"
_PCM = b"\x10\x00\x20\x00\x30\x00"
_TASK_ID = "b" * 32
_ENTRY = {
    "maker": "bytedance",
    "name": _MODEL,
    "media_type": "audio",
    "display_name": "Doubao TTS 2.0",
    "facts": {},
    "price": {
        "currency": "CNY",
        "meters": [{"meter": "characters", "dims": [], "rows": [{"when": {}, "price": 3.0}]}],
    },
}


@pytest.fixture(autouse=True)
def _catalog_with_the_plan_tts_model(monkeypatch):
    """测试目录数据里的 ark-agent-plan 加上这条语音合成模型，与目录数据仓库的人工清单同形。"""
    data = query.catalog_data()
    entry = parse_entry(_ENTRY)
    added = catalog_of((entry,), {"ark-agent-plan": (entry.key.ref,)})
    listing = data.builtin_providers["ark-agent-plan"]
    install_catalog(
        monkeypatch,
        replace(
            data,
            entries=(*data.entries, entry),
            builtin_providers={
                **data.builtin_providers,
                "ark-agent-plan": replace(
                    listing, models=(*listing.models, *added.builtin_providers["ark-agent-plan"].models)
                ),
            },
        ),
    )


def _stream(*items: dict) -> list[bytes]:
    return [json.dumps(item).encode() + b"\n" for item in items]


_SUCCESS = _stream(
    {"code": 0, "message": "", "data": base64.b64encode(_PCM).decode()},
    {"code": 20000000, "message": "ok", "data": None, "usage": {"text_words": 7}},
)

_TARGETS = [
    pytest.param("E1S01", None, id="narration"),
    pytest.param(voice_sample_resource_id("艾莉", _TASK_ID), ("characters", "艾莉"), id="voice-sample"),
]


def _write_project(project_path) -> None:
    """第 1 集剧本里有 E1S01 这条分镜，角色列表里有艾莉：两种产物的所属资源都在。"""
    write_generation_targets(project_path, segments=("E1S01",))
    project = json.loads((project_path / "project.json").read_text(encoding="utf-8"))
    (project_path / "project.json").write_text(
        json.dumps({**project, "characters": {"艾莉": {}}}, ensure_ascii=False), encoding="utf-8"
    )


async def _generator(tmp_path, session_factory, server, transport_layer=None) -> MediaGenerator:
    await asyncio.to_thread(_write_project, tmp_path)
    layer = transport_layer if transport_layer is not None else server.redirect()
    generator = await asyncio.to_thread(
        MediaGenerator,
        tmp_path,
        audio_backend=ArkAgentPlanAudioBackend(api_key="plan-key", model=_MODEL, transport_layer=layer),
        audio_provider_id="ark-agent-plan",
    )
    generator.ledger = Ledger(session_factory=session_factory)
    return generator


async def _only_record(session_factory) -> dict:
    async with session_factory() as session:
        repository = UsageRepository(session)
        page = await repository.list_records()
        assert page["total"] == 1
        record = await repository.get_record(page["items"][0]["id"])
    assert record is not None
    return record


@pytest.mark.parametrize(("resource_id", "target"), _TARGETS)
async def test_successful_synthesis_becomes_a_version_and_records_billed_characters(
    tmp_path, session_factory, resource_id, target
):
    # 内置模型按目录挂到这个端点；后端照常拼出生产地址，经 transport 注入层改投到替身
    assert builtin_model_endpoint("ark-agent-plan", "audio", _MODEL) == "ark-agent-plan-tts"
    async with loopback_server([ScriptedReply(chunks=_SUCCESS)]) as server:
        generator = await _generator(tmp_path, session_factory, server)
        output, version = await generator.generate_audio_async(
            text="你好世界",
            resource_id=resource_id,
            voice="zh_female_vv_uranus_bigtts",
            task_id=_TASK_ID,
            target_resource=target,
        )

    assert len(server.requests) == 1
    assert version == 1
    with wave.open(io.BytesIO(await asyncio.to_thread(output.read_bytes))) as wav:
        assert wav.readframes(wav.getnframes()) == _PCM
    record = await _only_record(session_factory)
    assert (record["status"], record["provider"], record["model"]) == ("success", "ark-agent-plan", _MODEL)
    # 计费字符数取上游回报的 text_words，不是按文本统计的 4 个字符
    assert record["usage_tokens"] == 7
    # 套餐按订阅计费，默认倍率 0：参考费用记 0，币种取条目价目表
    assert (record["cost_amount"], record["currency"]) == (0, "CNY")


@pytest.mark.parametrize(("resource_id", "target"), _TARGETS)
@pytest.mark.parametrize(
    ("reply", "key"),
    [
        pytest.param(ScriptedReply(chunks=_SUCCESS[:1]), "ark_agent_plan_tts_incomplete", id="missing-end"),
        pytest.param(
            ScriptedReply(chunks=_stream({"code": 45000000, "message": "private upstream plan-key"})),
            "ark_agent_plan_tts_access_denied",
            id="business-error",
        ),
        pytest.param(
            ScriptedReply(status=429, chunks=_stream({"code": 45000000, "message": "private upstream plan-key"})),
            "ark_agent_plan_tts_access_denied",
            id="concurrency-rejected",
        ),
        pytest.param(
            ScriptedReply(chunks=_stream({"code": 0, "data": "%%%"}, {"code": 20000000})),
            "ark_agent_plan_tts_incomplete",
            id="invalid-base64",
        ),
        pytest.param(
            ScriptedReply(chunks=_stream({"code": 20000000})), "ark_agent_plan_tts_incomplete", id="empty-audio"
        ),
        pytest.param(
            ScriptedReply(chunks=_stream({"code": 0, "data": "AQ=="}, {"code": 20000000})),
            "ark_agent_plan_tts_incomplete",
            id="partial-frame",
        ),
        pytest.param(
            ScriptedReply(chunks=[*_SUCCESS, b'{"code":45000000}']),
            "ark_agent_plan_tts_incomplete",
            id="error-after-end",
        ),
        pytest.param(
            ScriptedReply(chunks=[*_SUCCESS[:1], b'{"code":20000000']),
            "ark_agent_plan_tts_incomplete",
            id="truncated-json",
        ),
    ],
)
async def test_incomplete_stream_records_a_failed_call_and_keeps_no_version(
    tmp_path, session_factory, resource_id, target, reply, key, caplog, poll_clock
):
    async with loopback_server([reply, ScriptedReply(chunks=_SUCCESS)]) as server:
        generator = await _generator(tmp_path, session_factory, server)
        with pytest.raises(ApiError) as caught:
            await generator.generate_audio_async(
                text="你好世界",
                resource_id=resource_id,
                voice="zh_female_vv_uranus_bigtts",
                task_id=_TASK_ID,
                target_resource=target,
            )

    assert caught.value.key == key
    assert len(server.requests) == 1
    # 正式文件与暂存文件都没有留下，版本历史没有新增
    assert await asyncio.to_thread(lambda: sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.wav"))) == []
    assert not await asyncio.to_thread((tmp_path / "versions").exists)
    record = await _only_record(session_factory)
    # 记账沿用供应商 HTTP 分类优先级，任务失败仍保留上面的领域错误。
    record_code = "rate_limited" if reply.status == 429 else key
    assert (record["status"], record["error_code"]) == ("failed", record_code)
    assert "private upstream" not in json.dumps(record) + caplog.text
    assert "plan-key" not in json.dumps(record) + caplog.text


class _ConnectTimeoutLayer(httpx.AsyncBaseTransport):
    """每次发送都在建立连接阶段超时。"""

    def __call__(self, inner: httpx.AsyncBaseTransport, /) -> httpx.AsyncBaseTransport:
        return self

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("timed out")


@pytest.mark.parametrize(("resource_id", "target"), _TARGETS)
@pytest.mark.parametrize(
    ("connect_timeout", "record_code"),
    [(False, "ark_agent_plan_tts_unreachable"), (True, "timeout")],
    ids=["refused", "connect-timeout"],
)
async def test_unreachable_service_fails_with_a_localized_reason_and_keeps_no_version(
    tmp_path, session_factory, resource_id, target, connect_timeout, record_code, caplog, poll_clock
):
    async with loopback_server([]) as closed:
        pass
    # 替身已关闭，端口不再监听：连接被拒绝；或者在建立连接阶段超时
    layer = _ConnectTimeoutLayer() if connect_timeout else closed.redirect()
    generator = await _generator(tmp_path, session_factory, closed, transport_layer=layer)
    with pytest.raises(ApiError) as caught:
        await generator.generate_audio_async(
            text="你好世界",
            resource_id=resource_id,
            voice="zh_female_vv_uranus_bigtts",
            task_id=_TASK_ID,
            target_resource=target,
        )

    # 任务失败信息：落库的是领域码，按界面语言渲染成可读原因，不带底层异常原文
    stored = encode_task_failure_message(caught.value)
    assert stored == "[ark_agent_plan_tts_unreachable]"
    for locale in ("zh", "en", "ru", "vi"):
        text = render_failure(stored, partial(translate, locale=locale), make_display_names(locale))
        assert text
        assert not text.startswith("[")
    assert await asyncio.to_thread(lambda: sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.wav"))) == []
    assert not await asyncio.to_thread((tmp_path / "versions").exists)
    # 使用记录：失败，机器码可按语言渲染（参数为空对象，前端据此取领域文案），原文里没有底层异常与 Key
    record = await _only_record(session_factory)
    assert (record["status"], record["error_code"]) == ("failed", record_code)
    if record_code == "ark_agent_plan_tts_unreachable":
        assert record["error_params"] == {}
    dumped = json.dumps(record, ensure_ascii=False)
    for raw in ("Errno", "refused", "connection attempts", "timed out", "plan-key"):
        assert raw not in dumped
    assert "plan-key" not in caplog.text
