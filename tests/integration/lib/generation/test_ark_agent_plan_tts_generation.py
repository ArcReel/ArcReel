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

import pytest

from arcreel_catalog_core import parse_entry
from lib.backends.audio_backends.ark_agent_plan import ArkAgentPlanAudioBackend
from lib.backends.backend_assembly.specs import builtin_model_endpoint
from lib.billing.ledger import Ledger
from lib.catalog import query
from lib.db.repositories.usage_repo import UsageRepository
from lib.generation.media_generator import MediaGenerator
from lib.infra.api_errors import ApiError
from server.services.tasks.generation_tasks import voice_sample_resource_id
from tests.catalog_support import catalog_of, install_catalog
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


async def _generator(tmp_path, session_factory, server) -> MediaGenerator:
    await asyncio.to_thread(_write_project, tmp_path)
    generator = await asyncio.to_thread(
        MediaGenerator,
        tmp_path,
        audio_backend=ArkAgentPlanAudioBackend(api_key="plan-key", model=_MODEL, transport_layer=server.redirect()),
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
async def test_incomplete_stream_records_a_failed_call_and_keeps_no_version(
    tmp_path, session_factory, resource_id, target
):
    cut = _stream({"code": 0, "message": "", "data": base64.b64encode(_PCM).decode()})
    async with loopback_server([ScriptedReply(chunks=cut), ScriptedReply(chunks=_SUCCESS)]) as server:
        generator = await _generator(tmp_path, session_factory, server)
        with pytest.raises(ApiError) as caught:
            await generator.generate_audio_async(
                text="你好世界",
                resource_id=resource_id,
                voice="zh_female_vv_uranus_bigtts",
                task_id=_TASK_ID,
                target_resource=target,
            )

    assert caught.value.key == "ark_agent_plan_tts_incomplete"
    assert len(server.requests) == 1
    # 正式文件与暂存文件都没有留下，版本历史没有新增
    assert await asyncio.to_thread(lambda: sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*.wav"))) == []
    assert not await asyncio.to_thread((tmp_path / "versions").exists)
    record = await _only_record(session_factory)
    assert (record["status"], record["error_code"]) == ("failed", "ark_agent_plan_tts_incomplete")
