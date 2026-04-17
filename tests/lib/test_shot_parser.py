from lib.reference_video.shot_parser import parse_prompt


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
