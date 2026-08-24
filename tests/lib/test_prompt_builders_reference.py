"""reference_video prompt builder 单元测试。"""

import pytest

from lib.prompt_builders_reference import (
    build_reference_units_split_prompt,
    build_reference_video_prompt,
    render_reference_units_for_step2,
)
from lib.reference_video.writing_syntax import WRITING_SYNTAX_SPEC

pytestmark = pytest.mark.unit


def _step2_prompt(**overrides) -> str:
    kwargs = dict(
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        style="s",
        style_description="d",
        characters={"A": {"description": "d"}},
        scenes={},
        props={},
        step1_units=[],
        max_refs=9,
        episode=1,
    )
    kwargs.update(overrides)
    return build_reference_video_prompt(**kwargs)


def _split_prompt(**overrides) -> str:
    kwargs = dict(
        novel_text="text",
        project_overview={},
        characters={},
        scenes={},
        props={},
        supported_durations=[8],
        max_duration=8,
        max_reference_images=None,
        default_duration=None,
        episode=1,
    )
    kwargs.update(overrides)
    return build_reference_units_split_prompt(**kwargs)


def test_build_reference_video_prompt_contains_required_sections():
    step1_units = [
        {
            "unit_id": "E1U01",
            "text": "@[主角] 推门走进 @[酒馆]\n@[主角] 按住 @[长剑]",
            "duration_seconds": 8,
        }
    ]
    prompt = _step2_prompt(
        project_overview={"synopsis": "少年入江湖", "genre": "武侠", "theme": "成长", "world_setting": "北宋江湖"},
        style="国漫",
        style_description="水墨渲染风格",
        characters={"主角": {"description": "少年剑客"}, "张三": {"description": "酒客"}},
        scenes={"酒馆": {"description": "黑木桌椅的江湖酒馆"}},
        props={"长剑": {"description": "祖传青锋"}},
        step1_units=step1_units,
    )

    assert "北宋江湖" in prompt
    assert "水墨渲染风格" in prompt
    # 三类资产名称都必须出现（MentionPicker 候选源）
    assert "主角" in prompt and "张三" in prompt
    assert "酒馆" in prompt
    assert "长剑" in prompt
    # step1 正文逐字透传，不加任何分段前缀
    assert "@[主角] 推门走进 @[酒馆]\n@[主角] 按住 @[长剑]" in prompt
    assert "（时长 8s）" in prompt
    # 断言完整约束句：单看 "9" 会被默认 aspect_ratio "9:16" 满足，max_refs 未注入也能通过
    assert "普通 `@` 资产引用（去重）与关键分镜数量之和不超过 9（模型上限）" in prompt


def test_build_reference_video_prompt_emphasizes_no_appearance_description():
    assert "外貌" in _step2_prompt()


def test_build_reference_video_prompt_structures_shot_text_by_four_elements():
    """镜头描述指导按景别 / 构图 / 运镜 / 画面内容四要素组织（对抗生成过短的镜头描述）。"""
    prompt = _step2_prompt()
    for element in ("景别", "构图", "运镜", "画面内容"):
        assert element in prompt


def test_reference_prompts_anchor_keyframes_to_action_start_not_outcome():
    """step1/step2 都必须把关键帧锁在 00:00 起始状态，不能把整段动作结果当首帧。"""
    for prompt in (_split_prompt(), _step2_prompt()):
        assert "00:00.000" in prompt
        assert "摔进桂花堆" in prompt
        assert "不能画弟弟摔倒" in prompt or "错误首帧" in prompt


def test_build_reference_video_prompt_states_structure_preserving_contract():
    """step2 的职责是视觉展开：unit 数与台词两项保结构要求必须写进 prompt。"""
    prompt = _step2_prompt()
    assert "等长、同序" in prompt
    assert "逐字保留" in prompt


def test_build_reference_video_prompt_omits_duration_from_output_contract():
    """时长是 step1 定稿、机械沿用的字段，step2 不写——prompt 不得要求模型产出它。"""
    prompt = _step2_prompt(step1_units=[{"unit_id": "E1U01", "text": "x", "duration_seconds": 8}])
    assert "duration_seconds" not in prompt


def test_build_reference_video_prompt_max_refs_none_skips_rule():
    assert "模型上限" not in _step2_prompt(max_refs=None)


def test_build_reference_units_split_prompt_contains_constraints_and_candidates():
    prompt = _split_prompt(
        novel_text="李明推门走进酒馆",
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        characters={"李明": {"description": "少年"}},
        scenes={"酒馆": {"description": "江湖酒馆"}},
        supported_durations=[4, 6, 8],
        max_duration=12,
        max_reference_images=3,
        default_duration=4,
        episode=2,
        target_language="中文",
    )
    assert "李明推门走进酒馆" in prompt
    assert "李明" in prompt and "酒馆" in prompt
    assert "第 2 集" in prompt
    # 能力约束：档位集合、总时长上限、references 上限、默认偏好
    assert "4, 6, 8" in prompt
    assert "12 秒" in prompt
    assert "`@` 资产引用（去重）与 `keyframe_plan` 数量之和不超过 3" in prompt
    assert "默认取 4 秒" in prompt
    # 关键写作纪律
    assert "@[名称]" in prompt
    assert "外貌" in prompt
    # step1 的内容契约三件：原文锚、台词落位、语速下界
    assert "source_text" in prompt
    assert "不参与逐字机械校验" in prompt
    assert "source_text` 须逐字复制" not in prompt
    assert "口播语速约" in prompt
    assert "keyframe_plan（不可为空）" in prompt
    assert "每个 unit 至少有一个场景" in prompt
    assert "不是传统 Storyboard 图片" in prompt
    assert "不是等到视频生成时再截取的首帧" in prompt


def test_build_reference_units_split_prompt_speech_rate_override():
    """项目级语速覆盖生效时注入覆盖值而非语言默认；量词仍随 source_language。"""
    assert "口播语速约 7.5 字/秒" in _split_prompt(source_language="zh", speech_rate_override=7.5)
    assert "口播语速约 7.5 词/秒" in _split_prompt(source_language="en", speech_rate_override=7.5)


def test_build_reference_units_split_prompt_injects_episode_outline():
    """分集大纲注入（借 drama step1）：给出本集内容边界与下集接续点。"""
    prompt = _split_prompt(
        episode_outline={"title": "初入江湖", "story_beats": ["少年离家", "酒馆遇袭"], "hook": "剑断人亡"},
        next_episode_outline={"story_beats": ["追查线索"]},
    )
    assert "<episode_outline>" in prompt
    assert "少年离家" in prompt
    assert "<next_episode_outline>" in prompt
    assert "追查线索" in prompt


def test_build_reference_units_split_prompt_without_outline_leaves_no_empty_block():
    prompt = _split_prompt()
    assert "<episode_outline>" not in prompt
    assert "<next_episode_outline>" not in prompt


def test_both_prompt_levels_share_one_syntax_template():
    """语法规范唯一真相源：两级 prompt 注入同一份常量，仓库里没有第二份语法全文。"""
    split = _split_prompt()
    step2 = _step2_prompt()
    assert WRITING_SYNTAX_SPEC in split
    assert WRITING_SYNTAX_SPEC in step2


def test_build_reference_units_split_prompt_max_refs_none_skips_rule():
    assert "references 上限" not in _split_prompt(max_reference_images=None)


def test_build_reference_units_split_prompt_rejects_bad_inputs():
    with pytest.raises(ValueError, match="supported_durations"):
        _split_prompt(supported_durations=[])
    with pytest.raises(ValueError, match="default_duration"):
        _split_prompt(supported_durations=[4, 8], default_duration=5)
    with pytest.raises(ValueError, match="reference_supported_durations"):
        _split_prompt(supported_durations=[4, 8], reference_supported_durations=[6])
    with pytest.raises(ValueError, match="text_supported_durations"):
        _split_prompt(supported_durations=[4, 8], text_supported_durations=[6])


def test_build_reference_units_split_prompt_writes_reference_duration_linkage():
    """两套档位不同时，prompt 写明各自的档位与两条出路（换档位 / 去引用）。"""
    prompt = _split_prompt(
        supported_durations=[4, 6, 8],
        reference_supported_durations=[8],
        text_supported_durations=[4, 6, 8],
        default_duration=None,
    )
    assert "带 `@` 引用取（8）" in prompt
    assert "不带取（4, 6, 8）" in prompt
    assert "不用 `@` 引用" in prompt


def test_build_reference_units_split_prompt_states_both_tiers_without_containment():
    """带图档位反而更宽时，被收窄的是无引用 unit——prompt 必须照样写全，不能只讲带图那套。

    `constrain_durations` 在交集为空时回退到未收窄候选，故两套档位之间不假定包含关系
    （与 `_context.reference_unit_duration_tiers` 同一判据）。只讲带图会让无引用 unit
    照并集取到自己申请不到的档位。
    """
    prompt = _split_prompt(
        supported_durations=[4, 6, 8],
        reference_supported_durations=[4, 6, 8],
        text_supported_durations=[6],
        default_duration=None,
    )
    assert "带 `@` 引用取（4, 6, 8）" in prompt
    assert "不带取（6）" in prompt


def test_build_reference_units_split_prompt_excludes_dialogue_speaker_from_reference_rule():
    """联动约束按画面描述判定，台词记号 `@[角色]{台词}` 的说话人不计入。

    ``extract_mentions`` 派生 references 时剔除发声记号里的说话人（画外说话不生成参考图，
    见 shot_parser 同函数 docstring）；prompt 若只说「正文里有没有 `@`」，模型会把只在台词
    记号里出现说话人的 unit 误判为「带引用」、选进更窄的档位——落盘派生时 references 却是空，
    与模型的选择依据不一致。
    """
    prompt = _split_prompt(
        supported_durations=[4, 6, 8],
        reference_supported_durations=[8],
        text_supported_durations=[4, 6, 8],
        default_duration=None,
    )
    assert "台词记号 `@[角色]{台词}` 的说话人不计入" in prompt


def test_build_reference_units_split_prompt_scopes_default_to_its_tier():
    """默认值只对一种引用状态合法时点明适用范围，免得模型把它套到另一种状态的 unit 上。"""
    prompt = _split_prompt(
        supported_durations=[4, 6, 8],
        reference_supported_durations=[8],
        text_supported_durations=[4, 6, 8],
        default_duration=4,
    )
    assert "unit 默认取 4 秒（该默认值只落在不带 `@` 引用的 unit 的档位内" in prompt
    # 两套档位都含该默认值时不加这段限定，避免无效措辞。
    plain = _split_prompt(supported_durations=[4, 6, 8], default_duration=4)
    assert "该默认值只落在" not in plain


def test_build_reference_units_split_prompt_omits_linkage_when_tiers_equal():
    """多数型号未声明「参考图↔时长」约束：两套档位相同时不写这条，避免无效约束占注意力。"""
    for reference_durations, text_durations in (
        ([4, 6, 8], [4, 6, 8]),
        (None, None),
        ([4, 6, 8], None),
        (None, [4, 6, 8]),
    ):
        prompt = _split_prompt(
            supported_durations=[4, 6, 8],
            reference_supported_durations=reference_durations,
            text_supported_durations=text_durations,
        )
        assert "按该 unit **镜头描述行里有没有 `@` 资产引用**取用" not in prompt


def test_render_reference_units_for_step2_mechanical():
    """渲染是机械变换：序号 + unit 时长 + 正文逐字出现；unit_id 不进渲染。"""
    text = render_reference_units_for_step2(
        [
            {
                "unit_id": "E1U01",
                "text": "@[甲] 起身\n@[甲]：{走了。}\n@[甲] 出门",
                "duration_seconds": 10,
            },
            {"unit_id": "E1U02", "text": "@[甲] 回头", "duration_seconds": 8},
        ]
    )
    assert "#### unit 1（时长 10s）" in text
    assert "@[甲] 起身\n@[甲]：{走了。}\n@[甲] 出门" in text
    assert "#### unit 2（时长 8s）" in text
    # unit_id 由序号机械派生，不下发给 step2
    assert "E1U01" not in text
