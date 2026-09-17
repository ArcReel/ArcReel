"""内置目录扫描通过加载接口执行语法、槽位、变体族完整性与判重开启范围约束。"""

from lib.profile_manifest import VALID_CONTENT_MODES
from lib.project_manager import VALID_GENERATION_MODES, VALID_SOURCE_KINDS
from lib.prompt_builders_ad import AD_DURATION_TIERS
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
    # 判重只给存在纯文本回贴形态的模版开启，其余模版多处引用同一数据片段时不能被吞掉。
    assert {entry.id for entry in metadata if entry.idempotent} == {"storyboard/image", "storyboard/video"}
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


def test_builtin_applies_to_values_are_real_project_values():
    """设置页原样展示轴值，项目里不存在的取值会误导用户以为有这种模式。"""
    known = {
        "content_mode": set(VALID_CONTENT_MODES),
        "generation_mode": set(VALID_GENERATION_MODES),
        "source_kind": set(VALID_SOURCE_KINDS),
        "ad_duration_tier": {str(tier) for tier in AD_DURATION_TIERS},
    }
    for entry in PromptTemplates(BUILTIN_DIRECTORY).list_templates():
        for axis, values in entry.applies_to.items():
            if axis in known:
                assert set(values) <= known[axis], (entry.id, axis, values)
