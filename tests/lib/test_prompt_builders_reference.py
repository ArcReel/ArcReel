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
    step1_md = "| unit | 时长 | shots | references |\n| E1U1 | 8s | 2 | 主角,酒馆 |"

    prompt = build_reference_video_prompt(
        project_overview=project_overview,
        style="国漫",
        style_description="水墨渲染风格",
        characters=characters,
        scenes=scenes,
        props=props,
        units_md=step1_md,
        supported_durations=[5, 8, 10],
        max_refs=9,
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
    # step1 内容必须透传
    assert "E1U1" in prompt
    # 关键 prompt 指令
    assert "@[名称]" in prompt
    assert "Shot" in prompt
    # schema 上下文
    assert "ReferenceVideoScript" in prompt
    assert "references" in prompt
    # 时长约束
    assert "5" in prompt or "8" in prompt
    assert "9" in prompt  # max_refs


def test_build_reference_video_prompt_emphasizes_no_appearance_description():
    """spec §7.3 规则 3：描述里用包裹 mention，不描述外貌。"""
    prompt = build_reference_video_prompt(
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        style="style",
        style_description="desc",
        characters={"A": {"description": "d"}},
        scenes={},
        props={},
        units_md="stub",
        supported_durations=[8],
        max_refs=9,
        episode=1,
    )
    assert "外貌" in prompt  # 有反向说明


def test_build_reference_video_prompt_lists_shot_max_count():
    """spec §4.2：每 unit 1-4 shot。"""
    prompt = build_reference_video_prompt(
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        style="s",
        style_description="d",
        characters={},
        scenes={},
        props={},
        units_md="stub",
        supported_durations=[8],
        max_refs=9,
        episode=1,
    )
    assert "4" in prompt  # shot 数量上限


def test_build_reference_video_prompt_injects_max_duration():
    """传入 max_duration=15 时，prompt 含"贴近 15 秒"指示（对抗 8s 锚点污染）。"""
    prompt = build_reference_video_prompt(
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        style="s",
        style_description="d",
        characters={},
        scenes={},
        props={},
        units_md="stub",
        supported_durations=list(range(1, 16)),
        max_refs=7,
        max_duration=15,
        episode=1,
    )
    assert "15 秒" in prompt
    assert "当前模型上限" in prompt


def test_build_reference_video_prompt_max_duration_none_skips_segment():
    """未传 max_duration（None）时，prompt 不插入模型上限段（向后兼容）。"""
    prompt = build_reference_video_prompt(
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        style="s",
        style_description="d",
        characters={},
        scenes={},
        props={},
        units_md="stub",
        supported_durations=[4, 8],
        max_refs=9,
        episode=1,
    )
    assert "当前模型上限" not in prompt


def test_build_reference_video_prompt_constrains_unit_total_to_supported():
    """约束的是 unit 总时长（各 shot 之和）∈ supported，而非单个 shot 成员。

    参考视频发给 API 的是 unit.duration_seconds（各 shot 之和），故 prompt 必须把"必须取支持值"
    挂到总和上；per-shot duration 只保留 1-15 合理性，不再要求每个 shot 都是 supported 成员。
    """
    prompt = build_reference_video_prompt(
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        style="s",
        style_description="d",
        characters={"甲": {"description": "d"}},
        scenes={},
        props={},
        units_md="stub",
        supported_durations=[4, 8, 12],
        max_refs=9,
        max_duration=12,
        episode=1,
    )
    # 支持集合出现，且与"之和 / 必须"绑定（约束挂在总和上）
    assert "4/8/12s" in prompt
    assert "之和" in prompt and "必须" in prompt


def test_build_reference_video_prompt_injects_episode_constraints():
    """reference_video prompt 必须告知 LLM 当前 episode，避免 unit_id 跨集污染（#574）。"""
    prompt = build_reference_video_prompt(
        project_overview={"synopsis": "s", "genre": "g", "theme": "t", "world_setting": "w"},
        style="s",
        style_description="d",
        characters={},
        scenes={},
        props={},
        units_md="stub",
        supported_durations=[8],
        max_refs=9,
        episode=3,
    )
    assert "第 3 集" in prompt
    assert "E3U" in prompt
    assert "<episode_constraints>" in prompt
