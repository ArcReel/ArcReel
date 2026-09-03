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

    @staticmethod
    def _project_with_derivatives(tmp_path) -> dict:
        """张三有本体图与两个各自出过图的衍生；李四只有本体图。"""
        derivatives_dir = tmp_path / "characters" / "derivatives" / "张三"
        derivatives_dir.mkdir(parents=True)
        sheets = {
            ("character", "张三"): "张三.png",
            ("character", "张三/劲装"): "characters/derivatives/张三/劲装.png",
            ("character", "张三/兽化"): "characters/derivatives/张三/兽化.png",
            ("character", "李四"): "李四.png",
        }
        for relative in sheets.values():
            path = tmp_path / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fake-image")
        project = {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "characters": {
                "张三": {
                    "character_sheet": "张三.png",
                    "derivatives": {
                        "劲装": {"description": "黑色劲装", "character_sheet": sheets[("character", "张三/劲装")]},
                        "兽化": {"description": "半兽化", "character_sheet": sheets[("character", "张三/兽化")]},
                    },
                },
                "李四": {"character_sheet": "李四.png"},
            },
            "scenes": {},
            "props": {},
        }
        for (asset_type, name), relative in sheets.items():
            register_stale_visual_claim(tmp_path, ArtifactKey.asset_sheet(asset_type, name), relative)
        return project

    def _collect(self, tmp_path, project: dict, names: list[str]) -> list[dict]:
        from server.services.generation_tasks import _collect_sheet_references

        refs, _seen = _collect_sheet_references(
            project,
            tmp_path,
            [{"characters_in_segment": names}],
            char_field="characters_in_segment",
            scene_field="scenes",
            prop_field="props",
            currency_resolver=build_currency_resolver(tmp_path, project),
        )
        return refs

    def test_a_derivative_reference_collects_the_derivative_sheet(self, tmp_path):
        project = self._project_with_derivatives(tmp_path)

        refs = self._collect(tmp_path, project, ["张三/劲装"])

        assert [ref["image"].name for ref in refs] == ["劲装.png"]
        assert [ref["label"] for ref in refs] == ["张三/劲装"]

    def test_ontology_and_derivative_together_each_collect_one_sheet(self, tmp_path):
        project = self._project_with_derivatives(tmp_path)

        refs = self._collect(tmp_path, project, ["张三", "张三/劲装"])

        assert [ref["image"].name for ref in refs] == ["张三.png", "劲装.png"]

    def test_several_derivatives_of_one_character_each_collect_one_sheet(self, tmp_path):
        project = self._project_with_derivatives(tmp_path)

        refs = self._collect(tmp_path, project, ["张三/劲装", "张三/兽化"])

        assert [ref["image"].name for ref in refs] == ["劲装.png", "兽化.png"]

    def test_a_derivative_without_a_sheet_collects_nothing_for_it(self, tmp_path):
        """衍生还没出图：不回退到本体图，由准入报出 `character: 张三/劲装`（ADR 0073）。"""
        project = self._project_with_derivatives(tmp_path)
        project["characters"]["张三"]["derivatives"]["劲装"]["character_sheet"] = ""

        refs = self._collect(tmp_path, project, ["张三/劲装"])

        assert refs == []

    def test_an_unregistered_derivative_collects_nothing(self, tmp_path):
        project = self._project_with_derivatives(tmp_path)

        assert self._collect(tmp_path, project, ["李四/劲装"]) == []

    def test_a_derivative_sheet_is_claimed_under_its_own_manifest_key(self, tmp_path):
        """清单键是 `asset_sheet("character", "本体/衍生")`：登记缺失时该图不进请求。"""
        project = self._project_with_derivatives(tmp_path)
        project["characters"]["张三"]["derivatives"]["劲装"]["character_sheet"] = (
            "characters/derivatives/张三/未登记.png"
        )
        (tmp_path / "characters" / "derivatives" / "张三" / "未登记.png").write_bytes(b"fake-image")

        assert self._collect(tmp_path, project, ["张三/劲装"]) == []
