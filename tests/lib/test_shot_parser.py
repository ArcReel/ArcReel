from lib.reference_video.shot_parser import (
    compute_duration_from_shots,
    parse_prompt,
    render_prompt_for_backend,
    resolve_references,
)
from lib.script_models import ReferenceResource, Shot


def test_parse_single_shot_no_header():
    shots, refs, override = parse_prompt("中景，主角走进房间。")
    assert len(shots) == 1
    assert shots[0].text == "中景，主角走进房间。"
    assert override is True  # 无 header → 单镜头，override 模式
    assert refs == []


def test_parse_multi_shot():
    text = "Shot 1 (3s): 中远景，主角推门进酒馆。\nShot 2 (5s): 近景，对面的张三抬眼。\n"
    shots, refs, override = parse_prompt(text)
    assert len(shots) == 2
    assert shots[0].duration == 3
    assert shots[0].text == "中远景，主角推门进酒馆。"
    assert shots[1].duration == 5
    assert shots[1].text == "近景，对面的张三抬眼。"
    assert override is False  # 有 header → 派生模式


def test_parse_three_shots_mixed_whitespace():
    text = """Shot 1 (2s):  开场
Shot 2 (4s):   中段
Shot 3 (3s): 收尾"""
    shots, _refs, _ = parse_prompt(text)
    durations = [s.duration for s in shots]
    assert durations == [2, 4, 3]


def test_parse_empty_returns_empty_text_as_single_shot():
    shots, refs, override = parse_prompt("")
    assert len(shots) == 1
    assert shots[0].text == ""
    assert override is True


def test_extract_mentions_ordered_unique():
    text = "Shot 1 (3s): @张三 看向 @酒馆\nShot 2 (5s): @张三 拔剑 @长剑"
    _shots, refs, _ = parse_prompt(text)
    assert refs == ["张三", "酒馆", "长剑"]


def test_extract_mentions_empty_prompt():
    _shots, refs, _ = parse_prompt("没有任何提及")
    assert refs == []


def test_render_prompt_replaces_mentions():
    text = "中景，@张三 走进 @酒馆 找 @长剑。"
    refs = [
        ReferenceResource(type="character", name="张三"),
        ReferenceResource(type="scene", name="酒馆"),
        ReferenceResource(type="prop", name="长剑"),
    ]
    rendered = render_prompt_for_backend(text, refs)
    assert rendered == "中景，[图1] 走进 [图2] 找 [图3]。"


def test_render_prompt_unknown_mention_kept():
    text = "@张三 和 @未知 对话"
    refs = [ReferenceResource(type="character", name="张三")]
    rendered = render_prompt_for_backend(text, refs)
    assert "[图1]" in rendered
    assert "@未知" in rendered  # 未注册保留


def test_render_prompt_multi_shot_text():
    text = "Shot 1 (3s): @张三 推门\nShot 2 (5s): @张三 坐下"
    refs = [ReferenceResource(type="character", name="张三")]
    rendered = render_prompt_for_backend(text, refs)
    assert rendered.count("[图1]") == 2
    assert "Shot 1 (3s):" in rendered  # header 保留


def test_compute_duration_sums_shots():
    shots = [Shot(duration=3, text="a"), Shot(duration=5, text="b"), Shot(duration=2, text="c")]
    assert compute_duration_from_shots(shots) == 10


def test_compute_duration_single_shot():
    assert compute_duration_from_shots([Shot(duration=7, text="x")]) == 7


def test_compute_duration_empty_list():
    assert compute_duration_from_shots([]) == 0


def _proj(characters=None, scenes=None, props=None):
    return {
        "characters": characters or {},
        "scenes": scenes or {},
        "props": props or {},
    }


def test_resolve_references_character():
    proj = _proj(characters={"张三": {}})
    refs, missing = resolve_references(["张三"], proj)
    assert len(refs) == 1
    assert refs[0].type == "character"
    assert refs[0].name == "张三"
    assert missing == []


def test_resolve_references_scene_and_prop():
    proj = _proj(scenes={"酒馆": {}}, props={"长剑": {}})
    refs, missing = resolve_references(["酒馆", "长剑"], proj)
    types = {r.name: r.type for r in refs}
    assert types == {"酒馆": "scene", "长剑": "prop"}
    assert missing == []


def test_resolve_references_missing_reports_name():
    refs, missing = resolve_references(["张三", "未知"], _proj(characters={"张三": {}}))
    assert len(refs) == 1
    assert missing == ["未知"]


def test_resolve_references_preserves_order():
    proj = _proj(characters={"B": {}}, scenes={"A": {}}, props={"C": {}})
    refs, _ = resolve_references(["A", "B", "C"], proj)
    assert [r.name for r in refs] == ["A", "B", "C"]


def test_resolve_references_empty_input():
    refs, missing = resolve_references([], _proj())
    assert refs == []
    assert missing == []


def test_parse_multi_shot_preserves_pre_header_text():
    text = (
        "开场说明：这段剧本的整体基调偏紧张。\n"
        "Shot 1 (3s): 中远景，主角推门进酒馆。\n"
        "Shot 2 (5s): 近景，对面的张三抬眼。\n"
    )
    shots, _refs, override = parse_prompt(text)
    assert len(shots) == 2
    assert override is False
    # Pre-header text 前置到首 shot
    assert "开场说明" in shots[0].text
    assert "中远景" in shots[0].text
    # 第二个 shot 不受影响
    assert shots[1].text == "近景，对面的张三抬眼。"


# ── mention 前缀边界（issue #346） ────────────────────────────────────────


def test_mention_ignores_email_like_prefix():
    """email 左侧是 \\w，不应被当成 mention。"""
    from lib.reference_video.shot_parser import _extract_mentions

    assert _extract_mentions("contact a@张三 for help") == []
    assert _extract_mentions("email: test@domain.com") == []
    assert _extract_mentions("alice@example.com 和 bob@foo.io") == []
    assert _extract_mentions("room9@张三") == []
    assert _extract_mentions("user123@李四") == []


def test_mention_accepts_chinese_prefix():
    """中文左侧字符（\\u4e00-\\u9fff）不是 \\w，合法 mention 用法。"""
    from lib.reference_video.shot_parser import _extract_mentions

    assert _extract_mentions("你好@张三") == ["张三"]
    assert _extract_mentions("（对面）@李四 抬眼") == ["李四"]


def test_mention_accepts_whitespace_and_line_start():
    """空白字符 / 行首 / 标点前缀都应识别。"""
    from lib.reference_video.shot_parser import _extract_mentions

    assert _extract_mentions("@张三") == ["张三"]
    assert _extract_mentions("之后 @张三 回头") == ["张三"]
    assert _extract_mentions("Shot 1 (3s):\n@张三 开门") == ["张三"]
    assert _extract_mentions("台词：@张三 起身") == ["张三"]


def test_mention_underscore_prefix_is_rejected():
    """underscore 属 \\w，`foo_@张三` 类打字错误不应触发 mention。"""
    from lib.reference_video.shot_parser import _extract_mentions

    assert _extract_mentions("prefix_@张三") == []
