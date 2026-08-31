"""Tests for collect_sheet_references."""

from lib.artifact_manifest import (
    ArtifactKey,
)
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from tests.integration.server.services.generation_tasks_support import (
    build_currency_resolver,
    register_stale_visual_claim,
)


class TestCollectSheetReferences:
    def test_max_count_truncates_refs_from_single_item(self, tmp_path):
        # 单个 item 内的角色数就超过 max_count 时，_group 内层三段循环不会在
        # item 中途触发外层 break，需要在返回前再做一次显式切片。
        from server.services.generation_tasks import _collect_sheet_references

        characters = {}
        char_names = [f"char{i}" for i in range(8)]
        for name in char_names:
            sheet_path = tmp_path / f"{name}.png"
            sheet_path.write_bytes(b"fake-image")
            characters[name] = {"character_sheet": f"{name}.png"}

        project = {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "characters": characters,
            "scenes": {},
            "props": {},
        }
        items = [{"characters_in_segment": char_names}]
        for name in char_names:
            register_stale_visual_claim(
                tmp_path,
                ArtifactKey.asset_sheet("character", name),
                f"{name}.png",
            )

        refs, seen = _collect_sheet_references(
            project,
            tmp_path,
            items,
            char_field="characters_in_segment",
            scene_field="scenes",
            prop_field="props",
            max_count=6,
            currency_resolver=build_currency_resolver(tmp_path, project),
        )

        assert len(refs) == 6
        assert len(seen) == 8
