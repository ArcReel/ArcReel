"""lib.text_metrics 的覆盖测试。"""

from __future__ import annotations

from lib.text_metrics import count_reading_units, find_reading_unit_offset


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


class TestFindReadingUnitOffset:
    def test_zh_returns_end_of_nth_char(self) -> None:
        # "今天天气真好" 第 3 个汉字"天"末尾 → offset 3(0-based exclusive)
        assert find_reading_unit_offset("今天天气真好", 3, "zh") == 3

    def test_zh_with_mixed_ascii(self) -> None:
        # "他说：abc 123，好" 阅读单位:他 说 ：（全角）, ，（全角）, 好
        # 第 3 个单位"：" 末尾 = 索引 3 (0-based 'a' 前)
        assert find_reading_unit_offset("他说：abc 123，好", 3, "zh") == 3

    def test_en_returns_end_of_nth_word(self) -> None:
        # "hello world foo" 第 2 个 word "world" 末尾 = 索引 11
        assert find_reading_unit_offset("hello world foo", 2, "en") == 11

    def test_en_uneven_word_lengths_no_global_ratio_drift(self) -> None:
        # 全局比例换算的关键失败场景:前半部分长词、后半部分短词
        # "longwordone longwordtwo a b c d e" 7 个 word,字符总长度不均(共 33 字符)
        # 全局比例:第 4 个 word target → int(4*33/7)=18,落到 "longwordtwo" 中间
        # 累计扫描:第 4 个 word 是 "b" 末尾 = 27
        text = "longwordone longwordtwo a b c d e"
        assert find_reading_unit_offset(text, 2, "en") == 23
        assert find_reading_unit_offset(text, 4, "en") == 27

    def test_target_exceeds_total_returns_text_length(self) -> None:
        assert find_reading_unit_offset("hello", 99, "en") == 5
        assert find_reading_unit_offset("你好", 99, "zh") == 2

    def test_target_zero_or_negative_returns_zero(self) -> None:
        assert find_reading_unit_offset("hello", 0, "en") == 0
        assert find_reading_unit_offset("hello", -1, "en") == 0

    def test_empty_text_returns_zero(self) -> None:
        assert find_reading_unit_offset("", 5, "zh") == 0

    def test_vi_uses_word_pattern(self) -> None:
        # Hôm nay trời 第 2 词 "nay" 末尾 = 7
        assert find_reading_unit_offset("Hôm nay trời", 2, "vi") == 7

    def test_fallback_to_zh_for_unknown_language(self) -> None:
        # ja / None / "" 走 zh 路径,英文字符不计入 → 应返回 0(没有阅读单位)
        # 但因为没找到第 N 个单位,会走到末尾分支
        assert find_reading_unit_offset("hello world", 1, "ja") == 11


class TestPeekVendorSync:
    """peek_split_point.py 内联了 lib.text_metrics 的纯字符串逻辑(see vendor 注释)。
    本类锁两份在 pattern 与行为上一致,防止 copy-paste 时字符录入错(如 U+8C48 vs
    U+F900 这种视觉相同 codepoint 不同的字符)漂移到生产路径。
    """

    @staticmethod
    def _load_peek():
        import importlib.util
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        module_path = repo_root / "agent_runtime_profile/.claude/skills/manage-project/scripts/peek_split_point.py"
        spec = importlib.util.spec_from_file_location("_peek_split_point", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_zh_pattern_codepoints_match(self) -> None:
        import lib.text_metrics as lib_tm

        peek = self._load_peek()
        assert lib_tm._ZH_UNIT_PATTERN.pattern == peek._ZH_UNIT_PATTERN.pattern, (
            f"vendor drift: lib={lib_tm._ZH_UNIT_PATTERN.pattern!r} vs peek={peek._ZH_UNIT_PATTERN.pattern!r}"
        )

    def test_latin_pattern_codepoints_match(self) -> None:
        import lib.text_metrics as lib_tm

        peek = self._load_peek()
        assert lib_tm._LATIN_WORD_PATTERN.pattern == peek._LATIN_WORD_PATTERN.pattern

    def test_count_agrees_on_mixed_inputs(self) -> None:
        import lib.text_metrics as lib_tm

        peek = self._load_peek()
        # 覆盖 zh / en / vi 以及 fallback 路径,断言两实现行为一致
        cases = [
            ("今天天气真好", "zh"),
            ("他说：「你好。」abc 123", "zh"),
            ("The quick brown fox jumps", "en"),
            ("don't worry, it's fine", "en"),
            ("Hôm nay trời đẹp quá", "vi"),
            ("hello world", None),
            ("", "zh"),
            # Hangul / Yi 等非 CJK 字符: zh 度量应该 == 0
            # (vendor 早期把 U+F900 写成 U+8C48 时,这里会误把 Hangul 计入)
            ("안녕하세요", "zh"),
            ("ꀀꀁꀂ", "zh"),
        ]
        for text, lang in cases:
            assert peek.count_reading_units(text, lang) == lib_tm.count_reading_units(text, lang), (
                f"drift on text={text!r} lang={lang!r}"
            )
            assert peek.find_reading_unit_offset(text, 2, lang) == lib_tm.find_reading_unit_offset(text, 2, lang), (
                f"offset drift on text={text!r} lang={lang!r}"
            )
