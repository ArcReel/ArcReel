"""reference_video prompt builder 单元测试。

Spec §7.3、§4.2/4.3。
"""

from lib.prompt_builders_reference import build_reference_video_prompt


def test_build_reference_video_prompt_contains_required_sections():
    project_overview = {
        "synopsis": "少年入江湖",
        "genre": "武侠",
        "theme": "成长",
        "world_setting": "北宋江湖",
    }
    characters = {"主角": {"description": "少年剑客"}, "张三": {"description": "酒客"}}
    scenes = {"酒馆": {"description": "黑木桌椅的江湖酒馆"}}
    props = {"长剑": {"description": "祖传青锋"}}
    step1_units = [
        {
            "unit_id": "E1U1",
            "shots": [
                {"duration": 5, "text": "@[主角] 推门走进 @[酒馆]"},
                {"duration": 5, "text": "@[主角] 按住 @[长剑]"},
            ],
            "references": [
                {"type": "character", "name": "主角"},
                {"type": "scene", "name": "酒馆"},
                {"type": "prop", "name": "长剑"},
            ],
        }
    ]

    prompt = build_reference_video_prompt(
        project_overview=project_overview,
        style="国漫",
        style_description="水墨渲染风格",
        characters=characters,
        scenes=scenes,
        props=props,
        step1_units=step1_units,
        supported_durations=[13, 17, 19],
        max_refs=37,
        aspect_ratio="9:16",
        episode=1,
    )

    # 必备上下文
    assert "北宋江湖" in prompt
    assert "水墨渲染风格" in prompt
    # 三类资产名称都必须出现（MentionPicker 候选源）
    assert "主角" in prompt and "张三" in prompt
    assert "酒馆" in prompt
    assert "长剑" in prompt
    # 结构化 step1 须经机械渲染透传（unit_id / shot 文本 / references）
    assert "E1U1" in prompt
    assert "@[主角] 推门走进 @[酒馆]" in prompt
    assert "character:主角" in prompt
    assert "13" in prompt
    assert "17" in prompt
    assert "19" in prompt
    assert "37" in prompt


def test_build_reference_video_prompt_max_duration_changes_output():
    kwargs = dict(
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        style="s",
        style_description="d",
        characters={},
        scenes={},
        props={},
        step1_units=[],
        supported_durations=[4, 8],
        max_refs=7,
        episode=1,
    )
    without_max = build_reference_video_prompt(**kwargs)
    with_max = build_reference_video_prompt(**kwargs, max_duration=17)
    assert with_max != without_max
    assert "17" in with_max
    assert "17" not in without_max


def test_build_reference_video_prompt_constrains_unit_total_to_supported():
    """unit 总时长（各 shot 之和）∈ supported 的硬约束由动态 schema 枚举承担；

    prompt 只保留编排策略：给出支持集合，引导各 shot 时长相加正好落在集合内。
    """
    prompt = build_reference_video_prompt(
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        style="s",
        style_description="d",
        characters={"甲": {"description": "d"}},
        scenes={},
        props={},
        step1_units=[],
        supported_durations=[4, 8, 12],
        max_refs=9,
        max_duration=12,
        episode=1,
    )
    for duration in (4, 8, 12):
        assert str(duration) in prompt


def test_build_reference_video_prompt_injects_episode_constraints():
    """reference_video prompt 必须告知 LLM 当前 episode，避免 unit_id 跨集污染。"""
    prompt = build_reference_video_prompt(
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        style="s",
        style_description="d",
        characters={},
        scenes={},
        props={},
        step1_units=[],
        supported_durations=[8],
        max_refs=9,
        episode=37,
    )
    assert "E37U" in prompt


def test_build_reference_units_split_prompt_contains_constraints_and_candidates():
    from lib.prompt_builders_reference import build_reference_units_split_prompt

    prompt = build_reference_units_split_prompt(
        novel_text="李明推门走进酒馆",
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        characters={"李明": {"description": "少年"}},
        scenes={"酒馆": {"description": "江湖酒馆"}},
        props={},
        supported_durations=[4, 6, 8],
        max_duration=13,
        max_reference_images=7,
        default_duration=4,
        episode=2,
        target_language="中文",
    )
    assert "李明推门走进酒馆" in prompt
    assert "李明" in prompt and "酒馆" in prompt
    # episode 注入 unit_id 前缀
    assert "E2U" in prompt
    assert "E1U" not in prompt
    # 能力参数透传
    assert "4, 6, 8" in prompt
    assert "13" in prompt
    assert "7" in prompt


def test_build_reference_units_split_prompt_max_refs_changes_output():
    from lib.prompt_builders_reference import build_reference_units_split_prompt

    kwargs = dict(
        novel_text="text",
        project_overview={},
        characters={},
        scenes={},
        props={},
        supported_durations=[8],
        max_duration=8,
        default_duration=None,
        episode=1,
    )
    assert build_reference_units_split_prompt(
        **kwargs, max_reference_images=None
    ) != build_reference_units_split_prompt(**kwargs, max_reference_images=2)


def test_build_reference_units_split_prompt_rejects_bad_inputs():
    import pytest as _pytest

    from lib.prompt_builders_reference import build_reference_units_split_prompt

    common = dict(
        novel_text="text",
        project_overview={},
        characters={},
        scenes={},
        props={},
        max_duration=8,
        max_reference_images=None,
        episode=1,
    )
    with _pytest.raises(ValueError, match="supported_durations"):
        build_reference_units_split_prompt(supported_durations=[], default_duration=None, **common)
    with _pytest.raises(ValueError, match="default_duration"):
        build_reference_units_split_prompt(supported_durations=[4, 8], default_duration=5, **common)


def test_render_reference_units_for_step2_mechanical():
    """渲染是机械变换：unit_id / 总时长 / references / 各 shot 时长与文本逐项出现；畸形项跳过。"""
    from lib.prompt_builders_reference import render_reference_units_for_step2

    text = render_reference_units_for_step2(
        [
            {
                "unit_id": "E1U01",
                "shots": [{"duration": 4, "text": "@[甲] 起身"}, {"duration": 6, "text": "@[甲] 出门"}],
                "references": [{"type": "character", "name": "甲"}],
            },
            {"unit_id": "E1U02", "shots": [{"duration": 8, "text": "@[甲] 回头"}], "references": []},
        ]
    )
    assert "#### E1U01（预估总时长 10s）" in text
    assert "references: character:甲" in text
    assert "Shot 1 (4s): @[甲] 起身" in text
    assert "Shot 2 (6s): @[甲] 出门" in text
    assert "#### E1U02（预估总时长 8s）" in text
    assert "references: （无）" in text
