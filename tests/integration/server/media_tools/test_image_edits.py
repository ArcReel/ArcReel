"""edit_images handler ``ToolOutcome``."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from lib.artifacts.artifact_manifest import ArtifactStatus
from lib.project.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from tests.integration.server.agent_tool_support import (
    ToolHarness,
    fake_caps_resolver,
    read_generation_result,
    run_declared_tool,
    use_fake_caps,
)

_EDIT_ZHANGSAN = {"resource_type": "character", "edits": [{"id": "张三", "instruction": "把头发改成红色"}]}


def _give_zhangsan_a_sheet(fake_ctx: ToolHarness) -> None:
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


async def test_edit_images_happy(fake_ctx: ToolHarness) -> None:
    _give_zhangsan_a_sheet(fake_ctx)
    use_fake_caps(fake_ctx)
    fake_ctx.pm.mirror_to_disk()

    out = await run_declared_tool("edit_images", fake_ctx, _EDIT_ZHANGSAN, batch_waiter=_succeed_every_spec)

    assert read_generation_result(out).succeeded == ["张三"]


async def test_edit_images_failure_preserves_the_untouched_source_path(fake_ctx: ToolHarness) -> None:
    """Failed edits do not overwrite source images; return original paths, not None."""
    _give_zhangsan_a_sheet(fake_ctx)
    use_fake_caps(fake_ctx)
    fake_ctx.pm.mirror_to_disk()

    out = await run_declared_tool("edit_images", fake_ctx, _EDIT_ZHANGSAN, batch_waiter=_fail_every_spec)

    result = read_generation_result(out)
    assert result.failed == ["张三"]
    item = result.items[0]
    assert item.artifact_path == "characters/zhangsan.png"


async def test_edit_images_i2i_unavailable(fake_ctx: ToolHarness) -> None:
    """Unavailable i2i creates no tasks, sharing server fail-fast criteria."""
    use_fake_caps(fake_ctx, image_backend_error=ValueError("未找到可用的 image 供应商"))
    fake_ctx.pm.mirror_to_disk()
    enqueue = AsyncMock(return_value=([], []))

    out = await run_declared_tool("edit_images", fake_ctx, _EDIT_ZHANGSAN, batch_waiter=enqueue)

    # Unavailable i2i is a shared preenqueue condition, but callers still consume per-ID results;
    # record blocked for every requested ID, not unprogrammable text alone.
    result = read_generation_result(out)
    assert result.blocked == ["张三"]
    item = result.items[0]
    assert item.problem is not None
    assert item.problem.code == "image_capability_missing_i2i"
    assert item.problem.action == "configure_provider"
    enqueue.assert_not_awaited()


async def test_edit_images_active_asset_without_a_manifest_claim_is_not_enqueued(
    fake_ctx: ToolHarness,
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
    fake_ctx: ToolHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One unreadable edit artifact must not swallow other computed batch results.

    ``resolve_usable_image_edit_source`` intentionally raises ``ArtifactManifestError`` for BLOCKED
    manifest states. ``_build_specs`` must catch per edit, or handler-level ``except`` turns the
    escaped error into unreadable whole-batch text, losing 李四's otherwise admissible edit
    together with 张三's outcome.
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


async def test_edit_images_malformed_derivative_id_is_not_found_without_aborting_the_batch(
    fake_ctx: ToolHarness,
) -> None:
    """Malformed base/derivative IDs mark only that entry missing; valid batch derivatives submit normally."""
    project_path = fake_ctx.project_path
    (project_path / "characters").mkdir()
    (project_path / "characters" / "zhangsan-battle.png").write_bytes(b"png")
    fake_ctx.pm.project_payload["characters"]["张三"]["derivatives"] = {
        "战斗装": {"description": "重甲", "character_sheet": "characters/zhangsan-battle.png"}
    }
    use_fake_caps(fake_ctx)
    fake_ctx.pm.mirror_to_disk()

    out = await run_declared_tool(
        "edit_images",
        fake_ctx,
        {
            "resource_type": "character_derivative",
            "edits": [
                {"id": "战斗装", "instruction": "换发色"},
                {"id": "张三/战斗装", "instruction": "换衣服"},
            ],
        },
        batch_waiter=_succeed_every_spec,
    )

    result = read_generation_result(out)
    assert result.succeeded == ["张三/战斗装"]
    assert result.blocked == ["战斗装"]
    blocked_item = next(entry for entry in result.items if entry.unit_id == "战斗装")
    assert blocked_item.problem is not None
    assert blocked_item.problem.code == "generation_unit_not_found"


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
    fake_ctx: ToolHarness, arguments: dict[str, Any]
) -> None:
    use_fake_caps(fake_ctx)
    enqueue = AsyncMock(return_value=([], []))

    out = await run_declared_tool("edit_images", fake_ctx, arguments, batch_waiter=enqueue)

    assert out.problem is not None
    assert out.problem.code == "invalid_request"
    enqueue.assert_not_awaited()


async def test_edit_images_storyboard_rejects_an_unbound_script_before_provider(fake_ctx: ToolHarness) -> None:
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
    # Unbound scripts reject before providers; resolvers are never queried.
    assert resolver.image_generation_type_calls == []
    enqueue.assert_not_awaited()


async def test_edit_images_skips_missing_current_image(fake_ctx: ToolHarness) -> None:
    """Assets without current editable images, with unset sheet fields, block by ID without enqueue."""
    use_fake_caps(fake_ctx)
    fake_ctx.pm.mirror_to_disk()
    enqueue = AsyncMock(return_value=([], []))

    # 李四 lacks character_sheet.
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


async def test_edit_images_build_specs_warnings(fake_ctx: ToolHarness) -> None:
    """Malformed entries with IDs become blocked; unaddressable entries stay warnings, while valid entries enqueue normally."""
    _give_zhangsan_a_sheet(fake_ctx)
    use_fake_caps(fake_ctx)
    fake_ctx.pm.mirror_to_disk()

    out = await run_declared_tool(
        "edit_images",
        fake_ctx,
        {
            "resource_type": "character",
            "edits": [
                {"id": " ", "instruction": "x"},  # Whitespace ID.
                {"id": "张三", "instruction": "改发型"},  # Valid, the sole queued entry.
                {"id": "张三", "instruction": "again"},  # Duplicate ID.
                {"id": "李四", "instruction": ""},  # Missing instructions.
                {"id": "王五", "instruction": "改"},  # Resource absent.
            ],
        },
        batch_waiter=_succeed_every_spec,
    )

    # Entries without addressable IDs have no reportable units, only summary warnings.
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


async def test_edit_images_storyboard_happy(fake_ctx: ToolHarness) -> None:
    """Valid storyboard script_file resolves scripts and enqueues normally."""
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


async def test_edit_images_reports_failures(fake_ctx: ToolHarness) -> None:
    """Batch enqueue failure details include reasons."""
    _give_zhangsan_a_sheet(fake_ctx)
    use_fake_caps(fake_ctx)
    fake_ctx.pm.mirror_to_disk()

    out = await run_declared_tool("edit_images", fake_ctx, _EDIT_ZHANGSAN, batch_waiter=_fail_every_spec)

    result = read_generation_result(out)
    assert result.failed == ["张三"]
    problem = result.items[0].problem
    assert problem is not None
    assert "provider rejected" in problem.detail


async def test_edit_images_unexpected_exception(fake_ctx: ToolHarness) -> None:
    """Unexpected errors such as project-read failures become common tool_error fallbacks instead of propagating."""

    def boom(_name: str) -> dict[str, Any]:
        raise RuntimeError("db down")

    fake_ctx.pm.load_project = boom

    out = await run_declared_tool(
        "edit_images", fake_ctx, {"resource_type": "character", "edits": [{"id": "张三", "instruction": "x"}]}
    )

    assert out.problem is not None
    assert out.problem.code == "internal_error"


async def test_i2i_unavailable_problem_none_when_available() -> None:
    from server.media_tools import image_edits as mod

    resolver = fake_caps_resolver()
    assert await mod._i2i_unavailable_problem({}, config_resolver=resolver) is None
    # Evaluate i2i slots, not project-default image slots.
    assert resolver.image_generation_type_calls == ["i2i"]


async def test_i2i_unavailable_problem_on_value_error() -> None:
    from server.media_tools import image_edits as mod

    resolver = fake_caps_resolver(image_backend_error=ValueError("未找到可用的 image 供应商"))
    problem = await mod._i2i_unavailable_problem({}, config_resolver=resolver)
    assert problem is not None
    assert problem.code == "image_capability_missing_i2i"


async def test_i2i_unavailable_problem_model_not_in_catalog_guides_reselect() -> None:
    from lib.catalog.errors import ModelNotInCatalogError
    from server.media_tools import image_edits as mod

    resolver = fake_caps_resolver(
        image_backend_error=ModelNotInCatalogError(provider_id="ark", model_id="old-img", media_type="image")
    )
    problem = await mod._i2i_unavailable_problem({}, config_resolver=resolver)
    assert problem is not None
    assert problem.code == "model_not_in_provider_catalog"
    assert problem.action == "configure_provider"
    assert problem.params == {"provider": "ark", "model": "old-img"}
