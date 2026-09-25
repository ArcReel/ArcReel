"""edit_images handler 的 ``ToolOutcome``。"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib.artifacts.artifact_manifest import ArtifactStatus
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.media_tools.context import ToolContext
from tests.integration.server.agent_runtime.sdk_tools.sdk_tools_support import (
    fake_caps_resolver,
    read_generation_result,
    run_declared_tool,
    use_fake_caps,
)

_EDIT_ZHANGSAN = {"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]}


def _give_zhangsan_a_sheet(fake_ctx: ToolContext) -> None:
    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"


async def _succeed_every_spec(*, specs, **_batch_kwargs):
    from lib.generation.generation_queue_client import BatchTaskResult

    return [
        BatchTaskResult(
            resource_id=spec.resource_id,
            task_id="t1",
            status="succeeded",
            result={"file_path": f"characters/{spec.resource_id}.png", "version": 2},
        )
        for spec in specs
    ], []


async def _fail_every_spec(*, specs, **_batch_kwargs):
    from lib.generation.generation_queue_client import BatchTaskResult

    return [], [
        BatchTaskResult(resource_id=spec.resource_id, task_id="t1", status="failed", error="provider rejected")
        for spec in specs
    ]


async def test_edit_images_happy(fake_ctx: ToolContext) -> None:
    _give_zhangsan_a_sheet(fake_ctx)
    use_fake_caps(fake_ctx)
    fake_ctx.pm.mirror_to_disk()

    out = await run_declared_tool("edit_images", fake_ctx, _EDIT_ZHANGSAN, batch_waiter=_succeed_every_spec)

    assert read_generation_result(out).succeeded == ["张三"]


async def test_edit_images_failure_preserves_the_untouched_source_path(fake_ctx: ToolContext) -> None:
    """编辑任务失败时，源图未被覆盖——结果应带回编辑前的路径而不是 None。"""
    _give_zhangsan_a_sheet(fake_ctx)
    use_fake_caps(fake_ctx)
    fake_ctx.pm.mirror_to_disk()

    out = await run_declared_tool("edit_images", fake_ctx, _EDIT_ZHANGSAN, batch_waiter=_fail_every_spec)

    result = read_generation_result(out)
    assert result.failed == ["张三"]
    item = result.items[0]
    assert item.artifact_path == "characters/zhangsan.png"


async def test_edit_images_i2i_unavailable(fake_ctx: ToolContext) -> None:
    """i2i 不可用时不创建任何任务（复用服务端 fail-fast 判断点）。"""
    use_fake_caps(fake_ctx, image_backend_error=ValueError("未找到可用的 image 供应商"))
    fake_ctx.pm.mirror_to_disk()
    enqueue = AsyncMock(return_value=([], []))

    out = await run_declared_tool("edit_images", fake_ctx, _EDIT_ZHANGSAN, batch_waiter=enqueue)

    # i2i 不可用是入队前的共享前置条件，但调用方仍按逐 ID 契约读结果——每个
    # 请求到的 ID 各记一条 blocked，而不是只回一段无法编程消费的文本。
    result = read_generation_result(out)
    assert result.blocked == ["张三"]
    item = result.items[0]
    assert item.problem is not None
    assert item.problem.code == "image_capability_missing_i2i"
    assert item.problem.action == "configure_provider"
    enqueue.assert_not_awaited()


async def test_edit_images_active_asset_without_a_manifest_claim_is_not_enqueued(
    fake_ctx: ToolContext,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from lib.artifacts.artifact_manifest import ArtifactComparison, ArtifactKey

    _give_zhangsan_a_sheet(fake_ctx)
    fake_ctx.pm.project_payload["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
    comparisons = []

    class _Currency:
        def compare(self, key, *, artifact_path):
            comparisons.append((key, artifact_path))
            return ArtifactComparison(status=ArtifactStatus.MISSING, artifact_path=artifact_path)

        def resolve_usable_entry(self, key, *, artifact_path):
            self.compare(key, artifact_path=artifact_path)
            return

    enqueue = AsyncMock(return_value=([], []))
    monkeypatch.setattr("server.media_tools.image_edits.active_artifact_currency_resolver", lambda *_args: _Currency())
    use_fake_caps(fake_ctx)

    out = await run_declared_tool(
        "edit_images",
        fake_ctx,
        {"resource_type": "character", "edits": [{"id": "张三", "instruction": "换发色"}]},
        batch_waiter=enqueue,
    )

    assert read_generation_result(out).blocked == ["张三"]
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
    from lib.artifacts.artifact_manifest import ArtifactManifestError
    from server.services.tasks.image_edit_tasks import _ImageEditSource

    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan.png").write_bytes(b"png")
    (project_path / "characters" / "lisi.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
    fake_ctx.pm.project_payload["characters"]["张三"]["character_sheet"] = "characters/zhangsan.png"
    fake_ctx.pm.project_payload["characters"]["李四"]["character_sheet"] = "characters/lisi.png"
    fake_ctx.pm.project_payload["characters"]["李四"]["description"] = "配角"

    def fake_resolve_source(*, project, project_path, resource_type, resource_id, script, artifact_episode, resolver):
        if resource_id == "张三":
            raise ArtifactManifestError("manifest sidecar unreadable")
        return _ImageEditSource(resource_id=resource_id, artifact_path="characters/lisi.png", formal_claims=())

    use_fake_caps(fake_ctx)
    monkeypatch.setattr(
        "server.media_tools.image_edits.active_artifact_currency_resolver",
        lambda *_args: object(),
    )
    monkeypatch.setattr(
        "server.media_tools.image_edits.resolve_usable_image_edit_source",
        fake_resolve_source,
    )

    out = await run_declared_tool(
        "edit_images",
        fake_ctx,
        {
            "resource_type": "character",
            "edits": [
                {"id": "张三", "instruction": "换发色"},
                {"id": "李四", "instruction": "换衣服"},
            ],
        },
        batch_waiter=_succeed_every_spec,
    )

    result = read_generation_result(out)
    assert result.succeeded == ["李四"]
    assert result.blocked == ["张三"]
    blocked_item = next(entry for entry in result.items if entry.unit_id == "张三")
    assert blocked_item.problem is not None
    assert blocked_item.problem.code == "generation_artifact_state_unavailable"


@pytest.mark.parametrize(
    "arguments",
    [
        pytest.param(
            {"resource_type": "storyboard", "edits": [{"id": "E1S01", "instruction": "去杂物"}]},
            id="storyboard-without-script-file",
        ),
        pytest.param({"resource_type": "video", "edits": [{"id": "x", "instruction": "y"}]}, id="unknown-type"),
        pytest.param({"resource_type": "character", "edits": []}, id="empty-edits"),
        pytest.param({"resource_type": "character", "edits": ["not-a-dict"]}, id="malformed-entry"),
    ],
)
async def test_edit_images_rejects_a_malformed_request_before_enqueue(
    fake_ctx: ToolContext, arguments: dict[str, Any]
) -> None:
    use_fake_caps(fake_ctx)
    enqueue = AsyncMock(return_value=([], []))

    out = await run_declared_tool("edit_images", fake_ctx, arguments, batch_waiter=enqueue)

    assert out.problem is not None
    assert out.problem.code == "invalid_request"
    enqueue.assert_not_awaited()


async def test_edit_images_storyboard_rejects_an_unbound_script_before_provider(fake_ctx: ToolContext) -> None:
    fake_ctx.pm.project_payload["schema_version"] = CURRENT_PROJECT_SCHEMA_VERSION
    fake_ctx.pm.project_payload["episodes"] = []
    resolver = use_fake_caps(fake_ctx)
    enqueue = AsyncMock(return_value=([], []))

    out = await run_declared_tool(
        "edit_images",
        fake_ctx,
        {
            "resource_type": "storyboard",
            "script_file": "episode_1.json",
            "edits": [{"id": "E1S01", "instruction": "去掉背景杂物"}],
        },
        batch_waiter=enqueue,
    )

    assert out.problem is not None
    assert "not bound" in out.problem.detail
    # 剧本未绑定在供应商判定之前就拒：解析器一次都没被问过
    assert resolver.image_generation_type_calls == []
    enqueue.assert_not_awaited()


async def test_edit_images_skips_missing_current_image(fake_ctx: ToolContext) -> None:
    """资产没有可编辑的当前图（sheet 字段未设置）时逐 ID 阻断，不入队。"""
    use_fake_caps(fake_ctx)
    fake_ctx.pm.mirror_to_disk()
    enqueue = AsyncMock(return_value=([], []))

    # 李四 没有 character_sheet
    out = await run_declared_tool(
        "edit_images",
        fake_ctx,
        {"resource_type": "character", "edits": [{"id": "李四", "instruction": "换发色"}]},
        batch_waiter=enqueue,
    )

    result = read_generation_result(out)
    assert result.blocked == ["李四"]
    problem = result.items[0].problem
    assert problem is not None
    assert problem.code == "generation_unit_input_unusable"
    enqueue.assert_not_awaited()


async def test_edit_images_build_specs_warnings(fake_ctx: ToolContext) -> None:
    """畸形条目分两路：有 ID 的进逐 ID blocked，无 ID 可寻址的留在 warnings；合法条目仍正常入队。"""
    _give_zhangsan_a_sheet(fake_ctx)
    use_fake_caps(fake_ctx)
    fake_ctx.pm.mirror_to_disk()

    out = await run_declared_tool(
        "edit_images",
        fake_ctx,
        {
            "resource_type": "character",
            "edits": [
                {"id": " ", "instruction": "x"},  # 空白 id
                {"id": "张三", "instruction": "改发型"},  # 合法，唯一入队的一条
                {"id": "张三", "instruction": "again"},  # 重复 id
                {"id": "李四", "instruction": ""},  # 缺指令
                {"id": "王五", "instruction": "改"},  # 资源不存在
            ],
        },
        batch_waiter=_succeed_every_spec,
    )

    # 无 ID 可寻址的条目没有可报告的 unit，只能留在摘要告警里。
    assert isinstance(out.value, dict)
    summary = out.value["summary"]
    assert "缺少 id 的条目" in summary
    assert "重复出现" in summary

    result = read_generation_result(out)
    assert result.succeeded == ["张三"]
    assert sorted(result.blocked) == ["李四", "王五"]
    problems = {item.unit_id: item.problem.code for item in result.items if item.problem is not None}
    assert problems == {
        "李四": "generation_unit_request_invalid",
        "王五": "generation_unit_not_found",
    }


async def test_edit_images_storyboard_happy(fake_ctx: ToolContext) -> None:
    """storyboard 分支带合法 script_file 时应正常解析剧本并入队。"""
    use_fake_caps(fake_ctx)

    out = await run_declared_tool(
        "edit_images",
        fake_ctx,
        {
            "resource_type": "storyboard",
            "script_file": "episode_1.json",
            "edits": [{"id": "E1S01", "instruction": "去掉背景杂物"}],
        },
        batch_waiter=_succeed_every_spec,
    )

    assert read_generation_result(out).succeeded == ["E1S01"]


async def test_edit_images_reports_failures(fake_ctx: ToolContext) -> None:
    """批量入队返回失败项时，失败明细要带上失败原因。"""
    _give_zhangsan_a_sheet(fake_ctx)
    use_fake_caps(fake_ctx)
    fake_ctx.pm.mirror_to_disk()

    out = await run_declared_tool("edit_images", fake_ctx, _EDIT_ZHANGSAN, batch_waiter=_fail_every_spec)

    result = read_generation_result(out)
    assert result.failed == ["张三"]
    problem = result.items[0].problem
    assert problem is not None
    assert "provider rejected" in problem.detail


async def test_edit_images_unexpected_exception(fake_ctx: ToolContext) -> None:
    """未预期的异常（如 pm 读取项目失败）要落到统一的 tool_error 兜底，而非向上抛出。"""

    def boom(_name: str) -> dict[str, Any]:
        raise RuntimeError("db down")

    fake_ctx.pm.load_project = boom

    out = await run_declared_tool(
        "edit_images", fake_ctx, {"resource_type": "character", "edits": [{"id": "张三", "instruction": "x"}]}
    )

    assert out.problem is not None
    assert out.problem.code == "internal_error"


async def test_i2i_provider_available_true() -> None:
    from server.media_tools import image_edits as mod

    resolver = fake_caps_resolver()
    assert await mod._i2i_provider_available({}, config_resolver=resolver) is True
    # 判的是 i2i 槽位，不是项目默认图像槽
    assert resolver.image_generation_type_calls == ["i2i"]


async def test_i2i_provider_available_false_on_value_error() -> None:
    from server.media_tools import image_edits as mod

    resolver = fake_caps_resolver(image_backend_error=ValueError("未找到可用的 image 供应商"))
    assert await mod._i2i_provider_available({}, config_resolver=resolver) is False
