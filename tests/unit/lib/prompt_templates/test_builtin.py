"""内置目录扫描通过加载接口执行语法、槽位与变体族完整性约束。"""

from lib.prompt_templates import PromptTemplates
from lib.prompt_templates.builtin import BUILTIN_DIRECTORY


def test_builtin_directory_has_valid_slots_variants_and_template_syntax():
    templates = PromptTemplates(BUILTIN_DIRECTORY)
    metadata = templates.list_templates()
    assert metadata
    for entry in metadata:
        body, partials = templates.read_source(entry.id)
        assert body.strip()
        # 行距写在引用处，片段只写措辞本身。
        assert not [name for name, source in partials.items() if source.startswith("\n")]
    asset = next(entry for entry in metadata if entry.id == "asset/sheet")
    assert set(asset.applies_to["asset_type"]) == {"character", "character_derivative", "scene", "prop", "product"}
    for asset_type in asset.applies_to["asset_type"]:
        rendered = templates.render(
            asset.id,
            asset_type=asset_type,
            name="测试资产",
            description="外观描述",
            style="水彩",
            style_description="柔和笔触",
        )
        assert "外观描述" in rendered
        assert "Avoid:" in rendered
        if asset_type in {"character_derivative", "product"}:
            assert "Style:" not in rendered
            assert "Visual style:" not in rendered
        else:
            assert rendered.count("Style: 水彩") == 1
            assert rendered.count("Visual style: 柔和笔触") == 1
