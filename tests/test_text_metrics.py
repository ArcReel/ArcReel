"""lib.text_metrics 的覆盖测试。"""

from __future__ import annotations

from lib.text_metrics import count_reading_units, find_reading_unit_offset, reading_unit_noun


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

    def test_sip_plane_cjk_ext_b_to_h_counted(self) -> None:
        # 罕用字 / 古籍 / 人名地名: SIP 平面 U+20000-U+323AF (CJK Ext B-H)
        # 早期实现仅覆盖 BMP,SIP 字符走 zh 路径会被 \w 不识别 → count 偏少
        assert count_reading_units(chr(0x20000), "zh") == 1  # CJK Ext B 起
        assert count_reading_units(chr(0x2A6DF), "zh") == 1  # CJK Ext B 末
        assert count_reading_units(chr(0x30000), "zh") == 1  # CJK Ext G 起
        assert count_reading_units(chr(0x323AF), "zh") == 1  # CJK Ext H 覆盖范围内
        # 混排 BMP + SIP
        text = f"今天{chr(0x20000)}天气{chr(0x30000)}好"
        assert count_reading_units(text, "zh") == 7


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

    def test_vi_nfd_documents_caller_normalize_contract(self) -> None:
        # 越南语 NFD/组合重音形式: H + o + ̂ + m → \w word boundary 把组合标记拆出
        # 导致 "Hôm" 被计为 2 token (H + om)。lib 不主动 normalize (保持纯字符串),
        # 调用方应在文件读入边界 NFC normalize。这里把契约钉为测试。
        import unicodedata

        nfc = "Hôm nay trời"
        nfd = unicodedata.normalize("NFD", nfc)
        assert nfc != nfd, "前置:NFC 与 NFD 字面应不同(否则 case 无效)"
        # NFC 输入:3 词
        assert count_reading_units(nfc, "vi") == 3
        # NFD 输入:词数偏多(具体值视组合标记数,但必然 > 3),证明 lib 不会 silent 兜底
        assert count_reading_units(nfd, "vi") > 3
        # 调用方 NFC normalize 后,lib 即可正确计数
        assert count_reading_units(unicodedata.normalize("NFC", nfd), "vi") == 3

    def test_fallback_to_zh_for_unknown_language(self) -> None:
        # ja / None / "" 走 zh 路径,英文字符不计入 → 应返回 0(没有阅读单位)
        # 但因为没找到第 N 个单位,会走到末尾分支
        assert find_reading_unit_offset("hello world", 1, "ja") == 11


class TestReadingUnitNoun:
    """量词名与 count_reading_units 的语言分类同源：按词计 → 词，按字计 → 字。"""

    def test_word_based_languages_use_ci(self) -> None:
        assert reading_unit_noun("en") == "词"
        assert reading_unit_noun("vi") == "词"

    def test_char_based_language_uses_zi(self) -> None:
        assert reading_unit_noun("zh") == "字"

    def test_case_insensitive(self) -> None:
        assert reading_unit_noun("EN") == "词"
        assert reading_unit_noun(" Vi ") == "词"

    def test_fallback_to_zi_for_unknown_or_missing(self) -> None:
        # 未知 / None / 空 language 与 count_reading_units 同走 zh 路径 → 字
        assert reading_unit_noun(None) == "字"
        assert reading_unit_noun("") == "字"
        assert reading_unit_noun("ja") == "字"
