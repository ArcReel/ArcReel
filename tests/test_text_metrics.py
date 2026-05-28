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


class TestTextUtilsCountVsOffsetParity:
    """_text_utils.count_chars 与 find_char_offset 必须同口径(都跳所有空白字符)。
    若 count_chars 把行内空白也算入(早期 line.strip() 实现),en/vi 含空格场景下
    peek 输出的 split_target_chars 会超过 split.find_char_offset 能累到的 counted
    上限,导致 target_offset 跑末尾 + anchor 搜索窗口落空 + 切分错位。
    """

    @staticmethod
    def _load_text_utils():
        import importlib.util
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        module_path = repo_root / "agent_runtime_profile/.claude/skills/manage-project/scripts/_text_utils.py"
        spec = importlib.util.spec_from_file_location("_peek_text_utils_parity", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_en_with_inline_spaces_counts_only_nonwhitespace(self) -> None:
        tu = self._load_text_utils()
        # "hello world" 5+5=10 个非空白字符,行内空格不计入
        assert tu.count_chars("hello world") == 10

    def test_count_chars_equals_find_char_offset_counted_ceiling(self) -> None:
        # 对任意文本,find_char_offset(text, count_chars(text)+1) 必然返回末尾(超过 counted 上限)
        # 而 find_char_offset(text, count_chars(text)) 能命中最后一个非空白字符的位置
        tu = self._load_text_utils()
        for text in ["hello world foo bar baz", "今天天气真好", "  hello\n\n  world  ", "a b c\n\nd e f"]:
            n = tu.count_chars(text)
            if n == 0:
                continue
            # 上限内可达
            assert tu.find_char_offset(text, n) < len(text), f"target=count 应可达,text={text!r}"
            # 超出上限走兜底返回末尾
            assert tu.find_char_offset(text, n + 1) == len(text), f"target>count 兜底末尾,text={text!r}"

    def test_count_chars_drops_all_whitespace_kinds(self) -> None:
        tu = self._load_text_utils()
        # 全角空格 / 制表 / 换行 / 普通空格均不计入
        assert tu.count_chars("a　b\tc\nd e") == 5

    def test_zh_text_unaffected_by_change(self) -> None:
        # 中文几乎无内嵌空白,新旧口径同结果(回归保护)
        tu = self._load_text_utils()
        assert tu.count_chars("今天天气真好") == 6
        assert tu.count_chars("他说：「你好。」") == 8


class TestPeekSplitNfcSameCoordinateSystem:
    """peek 与 split 必须共用 NFC 坐标系:peek 把 NFD 源 normalize 到 NFC 后输出
    context/anchor,split 必须也对源文件 NFC normalize,否则 agent 从 peek 拿到的
    NFC anchor 在 NFD 文件中搜不到,vi macOS/外部导入越南语场景下切分失败。
    """

    @staticmethod
    def _read_script_text(rel: str) -> str:
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        return (repo_root / rel).read_text(encoding="utf-8")

    def test_split_episode_normalizes_to_nfc(self) -> None:
        # 钉契约:split_episode.py 源码中含 NFC normalize,确保与 peek 同坐标系
        src = self._read_script_text("agent_runtime_profile/.claude/skills/manage-project/scripts/split_episode.py")
        assert 'unicodedata.normalize("NFC"' in src or "unicodedata.normalize('NFC'" in src, (
            "split_episode.py 必须对源文件 NFC normalize 与 peek 同坐标系"
        )

    def test_nfc_anchor_found_in_nfd_source_via_normalize(self) -> None:
        # 模拟:NFD 源 → split NFC normalize 后,NFC anchor 能命中
        import unicodedata

        nfc_source = "Hôm nay trời đẹp quá. Chúng ta đi chơi nhé."
        nfd_source = unicodedata.normalize("NFD", nfc_source)
        assert nfc_source != nfd_source

        # peek 输出的 anchor 在 NFC 空间
        peek_anchor = "trời đẹp"
        assert peek_anchor in nfc_source

        # NFD 源直接查找 NFC anchor → miss(早期 bug)
        assert peek_anchor not in nfd_source

        # split NFC normalize 后查找 → hit(本 commit 修复)
        normalized = unicodedata.normalize("NFC", nfd_source)
        assert peek_anchor in normalized


class TestPeekBreakpointsLanguageAware:
    """peek 的 find_natural_breakpoints 调用须按 language 选标点集:zh 用 `。！？…`,
    en/vi 用 ASCII `. ! ? …`。早期版本未传 language → en/vi 文本断点为空,split
    工作流对英文/越南语源退化。
    """

    @staticmethod
    def _load_text_utils():
        import importlib.util
        from pathlib import Path

        repo_root = Path(__file__).resolve().parent.parent
        module_path = repo_root / "agent_runtime_profile/.claude/skills/manage-project/scripts/_text_utils.py"
        spec = importlib.util.spec_from_file_location("_peek_text_utils", module_path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_en_text_finds_ascii_sentence_endings(self) -> None:
        tu = self._load_text_utils()
        text = "Hello world. This is fine! Is that right?"
        bps = tu.find_natural_breakpoints(text, len(text) // 2, window=200, language="en")
        chars = {bp["char"] for bp in bps if bp["type"] == "sentence"}
        assert chars == {".", "!", "?"}

    def test_vi_text_finds_ascii_sentence_endings(self) -> None:
        tu = self._load_text_utils()
        text = "Hôm nay trời đẹp. Chúng ta đi chơi! Bạn có rảnh?"
        bps = tu.find_natural_breakpoints(text, len(text) // 2, window=200, language="vi")
        chars = {bp["char"] for bp in bps if bp["type"] == "sentence"}
        assert chars == {".", "!", "?"}

    def test_zh_default_keeps_cjk_endings(self) -> None:
        # 不传 language 时按 zh 路径,保持向后兼容
        tu = self._load_text_utils()
        text = "今天天气真好。我们一起去公园！你来吗？"
        bps = tu.find_natural_breakpoints(text, len(text) // 2, window=200)
        chars = {bp["char"] for bp in bps if bp["type"] == "sentence"}
        assert chars == {"。", "！", "？"}

    def test_en_does_not_find_cjk_endings(self) -> None:
        # 反向断言:en 路径不应误命中 zh 标点
        tu = self._load_text_utils()
        text = "Mixed text。Should ignore CJK punctuation here."
        bps = tu.find_natural_breakpoints(text, len(text) // 2, window=200, language="en")
        chars = {bp["char"] for bp in bps if bp["type"] == "sentence"}
        assert "。" not in chars
        assert "." in chars
