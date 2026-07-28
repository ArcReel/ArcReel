"""资产名路径安全校验。

资产名全链路被当作单段路径组件使用：文件名（characters/{name}.png、
versions/{type}/{name}_v{n}_{ts}.png）与 REST 路由的单段路径参数
（PATCH/DELETE /projects/{p}/characters/{name}、POST .../generate/character/{name}）。
含路径分隔符的名字会产生嵌套目录（版本登记 shutil.copy2 因父目录缺失而失败）和
无法匹配的 URL（uvicorn 把 %2F 解码回 / 后单段参数 404），因此须在所有创建入口拒绝。
"""

import pytest

from lib.asset_types import validate_asset_name
from lib.project_manager import ProjectManager


class TestValidateAssetName:
    def test_valid_names_pass_and_are_stripped(self):
        assert validate_asset_name("李白") == "李白"
        assert validate_asset_name("  李白  ") == "李白"
        assert validate_asset_name("Mr. Smith-2") == "Mr. Smith-2"

    @pytest.mark.parametrize(
        "bad",
        [
            "李白/诗人",
            "a\\b",
            "..",
            "a/../b",
            "x\0y",
            "",
            "   ",
            None,
            123,
        ],
    )
    def test_illegal_names_rejected(self, bad):
        with pytest.raises(ValueError):
            validate_asset_name(bad)

    @pytest.mark.parametrize(
        "bad",
        [
            "a:b",
            "a*b",
            "a?b",
            'a"b',
            "a<b",
            "a>b",
            "a|b",
            "a\nb",
            "a\rb",
            "a\tb",
            "a\x1fb",
            "a\x7fb",
            "尾随点.",
            "CON",
            "con",
            "Nul",
            "COM1",
            "lpt9",
            "CON.backup",
        ],
    )
    def test_windows_unsafe_names_rejected(self, bad):
        """名称会拼进文件名，Windows 上保留字符 / 控制字符 / 尾随点 / 保留设备名
        会"校验通过但写盘失败"；项目须可跨平台迁移，所有平台统一拒绝。"""
        with pytest.raises(ValueError):
            validate_asset_name(bad)

    def test_non_string_reports_type_error(self):
        with pytest.raises(ValueError, match="必须是字符串"):
            validate_asset_name(None)
        with pytest.raises(ValueError, match="必须是字符串"):
            validate_asset_name(123)

    def test_reserved_device_names_not_overmatched(self):
        # 仅精确（首个点段）匹配保留设备名，CON1 / CONAN / COM10 这类合法名不误杀
        assert validate_asset_name("CON1") == "CON1"
        assert validate_asset_name("CONAN") == "CONAN"
        assert validate_asset_name("COM10") == "COM10"


@pytest.fixture
def pm(tmp_path):
    manager = ProjectManager(tmp_path / "projects")
    manager.create_project("demo")
    manager.create_project_metadata("demo", "Demo")
    return manager


class TestProjectManagerCreationEntryPoints:
    def test_add_character_rejects_slash(self, pm):
        with pytest.raises(ValueError):
            pm.add_character("demo", "李白/诗人", "desc")
        assert "李白/诗人" not in pm.load_project("demo")["characters"]

    def test_add_project_character_rejects_slash(self, pm):
        with pytest.raises(ValueError):
            pm.add_project_character("demo", "李白/诗人", "desc")

    def test_add_batch_rejects_slash(self, pm):
        with pytest.raises(ValueError):
            pm.add_scenes_batch("demo", {"庙/宇": {"description": "d"}})
        assert "庙/宇" not in pm.load_project("demo").get("scenes", {})

    def test_add_batch_rejects_normalized_collision(self, pm):
        """strip 后等价的两个 key 不允许静默覆盖，整批 fail-loud 不落盘（与 upsert_assets 一致）。"""
        with pytest.raises(ValueError, match="冲突"):
            pm.add_scenes_batch("demo", {"庙宇": {"description": "a"}, "  庙宇  ": {"description": "b"}})
        assert "庙宇" not in pm.load_project("demo").get("scenes", {})

    def test_upsert_assets_rejects_slash(self, pm):
        with pytest.raises(ValueError):
            pm.upsert_assets("demo", "props", {"玉/佩": {"description": "d"}})
        assert "玉/佩" not in pm.load_project("demo").get("props", {})

    def test_add_asset_strips_name(self, pm):
        assert pm.add_character("demo", "  李白  ", "desc") is True
        chars = pm.load_project("demo")["characters"]
        assert "李白" in chars
        assert "  李白  " not in chars

    def test_legal_names_still_work(self, pm):
        assert pm.add_character("demo", "李白", "desc") is True
        result = pm.upsert_assets("demo", "scenes", {"庙宇": {"description": "d"}})
        assert "庙宇" in result["added"]
        assert pm.add_props_batch("demo", {"玉佩": {"description": "d"}}) == 1
