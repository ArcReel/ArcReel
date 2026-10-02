"""成片字幕的换行规则：中文按「可用宽度 ÷ 字号」显式换行，英文与越南语交给 libass。"""

from __future__ import annotations

from lib.final_cut.render_plan import output_profile_for_aspect_ratio
from lib.final_cut.subtitles import BurnedSubtitle, ass_document, subtitle_layout, subtitle_lines, wrap_chinese


def test_chinese_lines_hold_usable_width_divided_by_the_font_size() -> None:
    portrait = subtitle_layout(output_profile_for_aspect_ratio("9:16"))
    landscape = subtitle_layout(output_profile_for_aspect_ratio("16:9"))
    text = "天" * 30

    # 竖屏行宽 1080 × 82%、字身 60 像素，一行 14 字；横屏行宽 1920 × 60%、字身 40 像素，一行 28 字。
    assert [len(line) for line in subtitle_lines(text, portrait)] == [14, 14, 2]
    assert [len(line) for line in subtitle_lines(text, landscape)] == [28, 2]
    assert subtitle_lines("短句。", portrait) == ("短句。",)


def test_text_without_chinese_is_left_to_libass() -> None:
    layout = subtitle_layout(output_profile_for_aspect_ratio("9:16"))
    sentence = "This line is far longer than fourteen characters and wraps at spaces in libass."

    assert subtitle_lines(sentence, layout) == (sentence,)


def test_closing_punctuation_hangs_on_the_previous_line() -> None:
    assert wrap_chinese("一二三四五，六七", 5) == ("一二三四五，", "六七")
    assert wrap_chinese("一二三四五……六", 5) == ("一二三四五……", "六")


def test_opening_brackets_move_to_the_next_line_with_what_they_open() -> None:
    assert wrap_chinese("一二三四「五六」", 5) == ("一二三四", "「五六」")
    assert wrap_chinese("一二三四（ English）", 5) == ("一二三四", "（English）")


def test_latin_words_and_numbers_stay_whole_unless_wider_than_a_line() -> None:
    # 半角字符按半个字身计：「ArcReel」占 3.5 个字宽，整词换到下一行；换行处的空格丢弃。
    assert wrap_chinese("我们用了ArcReel做视频", 6) == ("我们用了", "ArcReel做视", "频")
    assert wrap_chinese("一二 2026 年", 3) == ("一二", "2026", "年")
    assert wrap_chinese("abcdefghijklmnop中", 4) == ("abcdefgh", "ijklmnop", "中")


def test_line_breaks_inside_a_subtitle_stay_within_one_ass_event() -> None:
    document = ass_document(
        [
            BurnedSubtitle(start_us=0, end_us=1_000_000, text="风起了\n门开了"),
            BurnedSubtitle(start_us=1_000_000, end_us=2_000_000, text="The wind rose\r\nthe door opened"),
        ],
        output_profile_for_aspect_ratio("9:16"),
    )
    events = [line for line in document.splitlines() if line.startswith("Dialogue:")]

    assert len(events) == 2
    assert events[0].endswith("风起了 门开了")
    assert events[1].endswith("The wind rose the door opened")
