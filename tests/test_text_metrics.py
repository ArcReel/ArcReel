"""lib.text_metrics.count_reading_units 的覆盖测试。"""

from __future__ import annotations

from lib.text_metrics import count_reading_units


class TestZh:
    def test_pure_narrative(self) -> None:
        # 13 个汉字
        assert count_reading_units("今天天气真好我们一起去公园", "zh") == 13

    def test_with_cjk_quotes_and_punct(self) -> None:
        # 「你好」+ 句号 + 「。」之外标点。「」, 。 都计入
        text = "他说：「你好。」"
        assert count_reading_units(text, "zh") == len(text)

    def test_mixed_with_ascii_digits(self) -> None:
        # 5 个汉字 + 「:」全角 1 + 半角字母数字均不计
        # 「他说：abc 123」中文 1+1+全角冒号(＀-￯) = 3 单位；ascii 字母数字不计
        assert count_reading_units("他说：abc 123", "zh") == 3

    def test_only_ascii_punct_no_chinese(self) -> None:
        # 纯 ascii 标点不被中文区段命中
        assert count_reading_units("hello, world!", "zh") == 0

    def test_empty(self) -> None:
        assert count_reading_units("", "zh") == 0

    def test_pure_whitespace(self) -> None:
        assert count_reading_units("   \n\t  ", "zh") == 0


class TestEn:
    def test_pure_english(self) -> None:
        assert count_reading_units("The quick brown fox jumps over the lazy dog", "en") == 9

    def test_with_digits(self) -> None:
        # word boundary 把 123 视为一个 word
        assert count_reading_units("call 911 now", "en") == 3

    def test_contractions(self) -> None:
        # \b\w+\b 把 don't 拆成 don + t,it's 拆成 it + s
        assert count_reading_units("don't worry, it's fine", "en") == 6

    def test_empty(self) -> None:
        assert count_reading_units("", "en") == 0


class TestVi:
    def test_typical_passage(self) -> None:
        # 复用 en 逻辑,unicode word-boundary 能识别带变音符号的越南语词
        text = "Hôm nay trời đẹp quá, chúng ta đi công viên nhé"
        # Hôm nay trời đẹp quá chúng ta đi công viên nhé = 11 词
        assert count_reading_units(text, "vi") == 11


class TestFallback:
    def test_none_language_falls_back_to_zh(self) -> None:
        assert count_reading_units("你好世界", None) == 4

    def test_empty_language_falls_back_to_zh(self) -> None:
        assert count_reading_units("你好世界", "") == 4

    def test_unknown_language_falls_back_to_zh(self) -> None:
        # ja / ko 等暂未支持,走 zh 路径不抛错
        assert count_reading_units("你好世界", "ja") == 4

    def test_case_insensitive(self) -> None:
        # 大小写不影响分支选择
        assert count_reading_units("hello world", "EN") == 2
        assert count_reading_units("hello world", "En") == 2
