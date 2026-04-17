from lib.project_manager import effective_mode


def test_effective_mode_defaults_to_storyboard():
    assert effective_mode(project={}, episode={}) == "storyboard"


def test_effective_mode_reads_project_level():
    assert effective_mode(project={"generation_mode": "grid"}, episode={}) == "grid"


def test_effective_mode_episode_overrides_project():
    assert (
        effective_mode(
            project={"generation_mode": "grid"},
            episode={"generation_mode": "reference_video"},
        )
        == "reference_video"
    )


def test_effective_mode_episode_none_falls_back():
    assert (
        effective_mode(
            project={"generation_mode": "grid"},
            episode={"generation_mode": None},
        )
        == "grid"
    )


def test_effective_mode_empty_episode_string_falls_back():
    assert (
        effective_mode(
            project={"generation_mode": "grid"},
            episode={"generation_mode": ""},
        )
        == "grid"
    )


def test_effective_mode_rejects_unknown_value_fallback():
    # 未知值应回退到 storyboard，不抛异常（兼容旧项目的脏数据）
    assert effective_mode(project={"generation_mode": "invalid"}, episode={}) == "storyboard"
