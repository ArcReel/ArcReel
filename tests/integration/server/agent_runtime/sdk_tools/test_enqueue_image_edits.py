"""Tests for enqueue_image_edits."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib.artifact_manifest import ArtifactStatus
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.agent_runtime.sdk_tools._context import ToolContext
from server.agent_runtime.sdk_tools.enqueue_image_edits import edit_images_tool
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    _call,
    _fake_caps_resolver,
    _generation_result,
    _use_fake_caps,
)

pytestmark = pytest.mark.usefixtures("_stub_audio_switch_guard", "_stub_reference_request_projection")

# ---------------------------------------------------------------------------
# enqueue_image_edits
# ---------------------------------------------------------------------------


def test_edit_images_registered() -> None:
    """edit_images 必须同时进 MCP 工具 id 集（前端 chip 三语校验依赖它）。"""
    from server.agent_runtime.sdk_tools import ARCREEL_MCP_TOOL_IDS

    assert "edit_images" in ARCREEL_MCP_TOOL_IDS


async def test_edit_images_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"  # type: ignore[attr-defined]

    async def fake_batch(*, project_name, specs, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"characters/{s.resource_id}.png", "version": 2},
            )
            for s in specs
        ]
        return succ, []

    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(
        tool_obj,
        {"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]},
    )
    assert out.get("is_error") is not True, out
    text = out["content"][0]["text"]
    assert "成功 1 件" in text
    assert "张三" in text


async def test_edit_images_failure_preserves_the_untouched_source_path(fake_ctx: ToolContext, monkeypatch) -> None:
    """编辑任务失败时，源图未被覆盖——结果应带回编辑前的路径而不是 None。"""
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"  # type: ignore[attr-defined]

    async def fake_batch(*, project_name, specs, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        fail = [
            BatchTaskResult(resource_id=s.resource_id, task_id="t1", status="failed", error="provider rejected")
            for s in specs
        ]
        return [], fail

    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(
        tool_obj,
        {"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]},
    )

    result = _generation_result(out)
    assert result.failed == ["张三"]
    item = result.items[0]
    assert item.artifact_path == "characters/zhangsan.png"


async def test_edit_images_i2i_unavailable(fake_ctx: ToolContext) -> None:
    """i2i 不可用时直接报错，不创建任何任务（复用服务端 fail-fast 判断点）。"""
    _use_fake_caps(fake_ctx, image_backend_error=ValueError("未找到可用的 image 供应商"))
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(
        tool_obj,
        {"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]},
    )
    assert out.get("is_error") is True

    # i2i 不可用是入队前的共享前置条件，但调用方仍按逐 ID 契约读结果——每个
    # 请求到的 ID 各记一条 blocked，而不是只回一段无法编程消费的文本。
    result = _generation_result(out)
    assert result.blocked == ["张三"]
    item = result.items[0]
    assert item.problem is not None
    assert item.problem.code == "image_capability_missing_i2i"
    assert item.problem.action == "configure_provider"


async def test_edit_images_active_asset_without_a_manifest_claim_is_not_enqueued(
    fake_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lib.artifact_manifest import ArtifactComparison, ArtifactKey
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"  # type: ignore[attr-defined]
    comparisons = []

    class _Currency:
        def compare(self, key, *, artifact_path):
            comparisons.append((key, artifact_path))
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path)

        def resolve_usable_entry(self, key, *, artifact_path):
            self.compare(key, artifact_path=artifact_path)
            return None

    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "active_artifact_currency_resolver", lambda *_args: _Currency())
    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(
        edit_images_tool(fake_ctx),
        {"resource_type": "character", "edits": [{"id": "张三", "instruction": "换发色"}]},
    )

    assert out.get("is_error") is True
    assert comparisons == [(ArtifactKey.asset_sheet("character", "张三"), "characters/zhangsan.png")]
    enqueue.assert_not_awaited()


async def test_edit_images_one_manifest_fail_loud_error_does_not_abort_the_batch(
    fake_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """一条编辑的产物状态读取 fail-loud，不该把同批其它编辑的已算结果一起吞掉。

    ``resolve_usable_image_edit_source`` 在 Manifest 判定该条产物状态时抛
    ``ArtifactManifestError`` 是设计内行为（对应 BLOCKED）；``_build_specs`` 的
    per-edit 循环必须单独捕获它，否则会逃出循环、被 handler 级 ``except`` 接住变成
    整批不可读的纯文本错误——张三之外，李四这条本可正常入队的编辑也一起丢了结论。
    """
    from lib.artifact_manifest import ArtifactManifestError
    from server.services.image_edit_tasks import _ImageEditSource

    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan.png").write_bytes(b"png")
    (project_path / "characters" / "lisi.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["characters"]["李四"]["character_sheet"] = "characters/lisi.png"  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["characters"]["李四"]["description"] = "配角"  # type: ignore[attr-defined]

    async def fake_batch(*, project_name, specs, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"characters/{s.resource_id}.png", "version": 2},
            )
            for s in specs
        ]
        return succ, []

    def fake_resolve_source(*, project, project_path, resource_type, resource_id, script, artifact_episode, resolver):
        if resource_id == "张三":
            raise ArtifactManifestError("manifest sidecar unreadable")
        return _ImageEditSource(resource_id=resource_id, artifact_path="characters/lisi.png", formal_claims=())

    _use_fake_caps(fake_ctx)
    monkeypatch.setattr("server.agent_runtime.sdk_tools.enqueue_image_edits.batch_enqueue_and_wait", fake_batch)
    monkeypatch.setattr(
        "server.agent_runtime.sdk_tools.enqueue_image_edits.active_artifact_currency_resolver",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        "server.agent_runtime.sdk_tools.enqueue_image_edits.resolve_usable_image_edit_source",
        fake_resolve_source,
    )

    out = await _call(
        edit_images_tool(fake_ctx),
        {
            "resource_type": "character",
            "edits": [
                {"id": "张三", "instruction": "换发色"},
                {"id": "李四", "instruction": "换衣服"},
            ],
        },
    )

    result = _generation_result(out)
    assert result.succeeded == ["李四"]
    assert result.blocked == ["张三"]
    blocked_item = next(entry for entry in result.items if entry.unit_id == "张三")
    assert blocked_item.problem is not None
    assert blocked_item.problem.code == "generation_artifact_state_unavailable"


async def test_edit_images_storyboard_requires_script_file(fake_ctx: ToolContext, monkeypatch) -> None:

    _use_fake_caps(fake_ctx)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "storyboard", "edits": [{"id": "E1S01", "instruction": "去杂物"}]})
    assert out.get("is_error") is True
    assert "script_file" in out["content"][0]["text"]


async def test_edit_images_storyboard_rejects_an_unbound_script_before_provider(
    fake_ctx: ToolContext,
    monkeypatch,
) -> None:
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    fake_ctx.pm.project_payload["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION  # type: ignore[attr-defined]
    fake_ctx.pm.project_payload["episodes"] = []  # type: ignore[attr-defined]
    resolver = _use_fake_caps(fake_ctx)
    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", enqueue)

    out = await _call(
        edit_images_tool(fake_ctx),
        {
            "resource_type": "storyboard",
            "script_file": "episode_1.json",
            "edits": [{"id": "E1S01", "instruction": "去掉背景杂物"}],
        },
    )

    assert out.get("is_error") is True
    assert "not bound" in out["content"][0]["text"]
    # 剧本未绑定在供应商判定之前就拒：解析器一次都没被问过
    assert resolver.image_capability_calls == []
    enqueue.assert_not_awaited()


async def test_edit_images_rejects_unknown_resource_type(fake_ctx: ToolContext) -> None:
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "video", "edits": [{"id": "x", "instruction": "y"}]})
    assert out.get("is_error") is True


async def test_edit_images_skips_missing_current_image(fake_ctx: ToolContext, monkeypatch) -> None:
    """资产没有可编辑的当前图（sheet 字段未设置）时跳过并告警，不入队。"""

    _use_fake_caps(fake_ctx)
    tool_obj = edit_images_tool(fake_ctx)
    # 李四 没有 character_sheet
    out = await _call(tool_obj, {"resource_type": "character", "edits": [{"id": "李四", "instruction": "换发色"}]})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "李四" in text
    assert "没有可编辑的当前图" in text


async def test_edit_images_rejects_empty_edits(fake_ctx: ToolContext) -> None:
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "character", "edits": []})
    assert out.get("is_error") is True
    assert "edits 不能为空" in out["content"][0]["text"]


async def test_edit_images_build_specs_warnings(fake_ctx: ToolContext, monkeypatch) -> None:
    """畸形条目分两路：有 ID 的进逐 ID blocked，无 ID 可寻址的留在 warnings；合法条目仍正常入队。"""
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"  # type: ignore[attr-defined]

    async def fake_batch(*, project_name, specs, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"characters/{s.resource_id}.png", "version": 2},
            )
            for s in specs
        ]
        return succ, []

    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(
        tool_obj,
        {
            "resource_type": "character",
            "edits": [
                "not-a-dict",  # 非 dict 条目
                {"id": "", "instruction": "x"},  # 缺 id
                {"id": "张三", "instruction": "改发型"},  # 合法，唯一入队的一条
                {"id": "张三", "instruction": "again"},  # 重复 id
                {"id": "李四", "instruction": ""},  # 缺指令
                {"id": "王五", "instruction": "改"},  # 资源不存在
            ],
        },
    )
    text = out["content"][0]["text"]
    # 无 ID 可寻址的条目没有可报告的 unit，只能留在文本告警里。
    assert "非法条目" in text
    assert "缺少 id 的条目" in text
    assert "重复出现" in text

    result = _generation_result(out)
    assert result.succeeded == ["张三"]
    assert sorted(result.blocked) == ["李四", "王五"]
    problems = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert problems == {
        "李四": "generation_unit_request_invalid",
        "王五": "generation_unit_not_found",
    }


async def test_edit_images_storyboard_happy(fake_ctx: ToolContext, monkeypatch) -> None:
    """storyboard 分支带合法 script_file 时应正常解析剧本并入队（覆盖 validate_script_filename + load_script 调用）。"""
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    async def fake_batch(*, project_name, specs, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        succ = [
            BatchTaskResult(
                resource_id=s.resource_id,
                task_id="t1",
                status="succeeded",
                result={"file_path": f"storyboards/scene_{s.resource_id}.png", "version": 2},
            )
            for s in specs
        ]
        return succ, []

    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(
        tool_obj,
        {
            "resource_type": "storyboard",
            "script_file": "episode_1.json",
            "edits": [{"id": "E1S01", "instruction": "去掉背景杂物"}],
        },
    )
    assert out.get("is_error") is not True, out
    assert "成功 1 件" in out["content"][0]["text"]


async def test_edit_images_reports_failures(fake_ctx: ToolContext, monkeypatch) -> None:
    """批量入队返回失败项时，摘要与明细都要带上失败原因。"""
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"  # type: ignore[attr-defined]

    async def fake_batch(*, project_name, specs, **_batch_kwargs):
        from lib.generation_queue_client import BatchTaskResult

        fail = [
            BatchTaskResult(resource_id=s.resource_id, task_id="t1", status="failed", error="provider timeout")
            for s in specs
        ]
        return [], fail

    _use_fake_caps(fake_ctx)
    monkeypatch.setattr(mod, "batch_enqueue_and_wait", fake_batch)
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "character", "edits": [{"id": "张三", "instruction": "改发型"}]})
    assert out.get("is_error") is True
    text = out["content"][0]["text"]
    assert "成功 0 件、失败 1 件" in text
    assert "provider timeout" in text


async def test_edit_images_unexpected_exception(fake_ctx: ToolContext) -> None:
    """未预期的异常（如 pm 读取项目失败）要落到统一的 tool_error 兜底，而非向上抛出。"""

    def boom(_name: str) -> dict[str, Any]:
        raise RuntimeError("db down")

    fake_ctx.pm.load_project = boom  # type: ignore[method-assign]
    tool_obj = edit_images_tool(fake_ctx)
    out = await _call(tool_obj, {"resource_type": "character", "edits": [{"id": "张三", "instruction": "x"}]})
    assert out.get("is_error") is True
    assert "edit_images 失败" in out["content"][0]["text"]


async def test_i2i_provider_available_true() -> None:
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    resolver = _fake_caps_resolver()
    assert await mod._i2i_provider_available({}, config_resolver=resolver) is True
    # 判的是 i2i 槽位，不是项目默认图像槽
    assert resolver.image_capability_calls == ["i2i"]


async def test_i2i_provider_available_false_on_value_error() -> None:
    from server.agent_runtime.sdk_tools import enqueue_image_edits as mod

    resolver = _fake_caps_resolver(image_backend_error=ValueError("未找到可用的 image 供应商"))
    assert await mod._i2i_provider_available({}, config_resolver=resolver) is False
