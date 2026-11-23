"""lib.infra.text_metrics 的覆盖测试。"""

from __future__ import annotations

from lib.infra.text_metrics import count_reading_units, find_reading_unit_offset, reading_unit_noun


class TestZh:
    def test_pure_narrative(self) -> None:
        assert count_reading_units("今天天气真好我们一起去公园", "zh") == 13

    def test_with_cjk_quotes_and_punct(self) -> None:
        text = "他说：「你好。」"
        assert count_reading_units(text, "zh") == len(text)

    def test_mixed_with_ascii_digits(self) -> None:
        assert count_reading_units("他说：abc 123", "zh") == 3

    def test_only_ascii_punct_no_chinese(self) -> None:
        assert count_reading_units("hello, world!", "zh") == 0

    def test_empty(self) -> None:
        assert count_reading_units("", "zh") == 0

    def test_pure_whitespace(self) -> None:
        assert count_reading_units("   \n\t  ", "zh") == 0

    def test_sip_plane_cjk_ext_b_to_h_counted(self) -> None:
        assert count_reading_units(chr(0x20000), "zh") == 1
        assert count_reading_units(chr(0x2A6DF), "zh") == 1
        assert count_reading_units(chr(0x30000), "zh") == 1
        assert count_reading_units(chr(0x323AF), "zh") == 1
        text = f"今天{chr(0x20000)}天气{chr(0x30000)}好"
        assert count_reading_units(text, "zh") == 7


class TestEn:
    def test_pure_english(self) -> None:
        assert count_reading_units("The quick brown fox jumps over the lazy dog", "en") == 9

    def test_with_digits(self) -> None:
        assert count_reading_units("call 911 now", "en") == 3

    def test_contractions(self) -> None:
        assert count_reading_units("don't worry, it's fine", "en") == 6

    def test_empty(self) -> None:
        assert count_reading_units("", "en") == 0


class TestVi:
    def test_typical_passage(self) -> None:
        text = "Hôm nay trời đẹp quá, chúng ta đi công viên nhé"
        assert count_reading_units(text, "vi") == 11


class TestRu:
    def test_cyrillic_words(self) -> None:
        text = "Привет, мир! Это тест."
        assert count_reading_units(text, "ru") == 4

    def test_with_digits(self) -> None:
        assert count_reading_units("3 кота и 2 собаки", "ru") == 5

    def test_case_insensitive(self) -> None:
        assert count_reading_units("Привет мир", "RU") == 2


class TestFallback:
    def test_none_language_falls_back_to_zh(self) -> None:
        assert count_reading_units("你好世界", None) == 4

    def test_empty_language_falls_back_to_zh(self) -> None:
        assert count_reading_units("你好世界", "") == 4

    def test_unknown_language_falls_back_to_zh(self) -> None:
        assert count_reading_units("你好世界", "ja") == 4

    def test_case_insensitive(self) -> None:
        assert count_reading_units("hello world", "EN") == 2
        assert count_reading_units("hello world", "En") == 2


class TestFindReadingUnitOffset:
    def test_zh_returns_end_of_nth_char(self) -> None:
        assert find_reading_unit_offset("今天天气真好", 3, "zh") == 3

    def test_zh_with_mixed_ascii(self) -> None:
        assert find_reading_unit_offset("他说：abc 123，好", 3, "zh") == 3

    def test_en_returns_end_of_nth_word(self) -> None:
        assert find_reading_unit_offset("hello world foo", 2, "en") == 11

    def test_en_uneven_word_lengths_no_global_ratio_drift(self) -> None:
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
        assert find_reading_unit_offset("Hôm nay trời", 2, "vi") == 7

    def test_vi_nfd_documents_caller_normalize_contract(self) -> None:
        import unicodedata

        nfc = "Hôm nay trời"
        nfd = unicodedata.normalize("NFD", nfc)
        assert nfc != nfd, "前置:NFC 与 NFD 字面应不同(否则 case 无效)"
        assert count_reading_units(nfc, "vi") == 3
        assert count_reading_units(nfd, "vi") > 3
        assert count_reading_units(unicodedata.normalize("NFC", nfd), "vi") == 3

    def test_fallback_to_zh_for_unknown_language(self) -> None:
        assert find_reading_unit_offset("hello world", 1, "ja") == 11


class TestReadingUnitNoun:
    """量词名与 count_reading_units 的语言分类同源：按词计 → 词，按字计 → 字。"""

    def test_word_based_languages_use_ci(self) -> None:
        assert reading_unit_noun("en") == "词"
        assert reading_unit_noun("vi") == "词"
        assert reading_unit_noun("ru") == "词"

    def test_char_based_language_uses_zi(self) -> None:
        assert reading_unit_noun("zh") == "字"

    def test_case_insensitive(self) -> None:
        assert reading_unit_noun("EN") == "词"
        assert reading_unit_noun(" Vi ") == "词"
        assert reading_unit_noun(" RU ") == "词"

    def test_fallback_to_zi_for_unknown_or_missing(self) -> None:
        assert reading_unit_noun(None) == "字"
        assert reading_unit_noun("") == "字"
        assert reading_unit_noun("ja") == "字"
