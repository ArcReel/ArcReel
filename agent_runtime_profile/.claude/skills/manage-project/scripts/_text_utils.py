"""
_text_utils.py - 分集切分共享工具函数

提供字数计数和字符偏移转换功能，供 peek_split_point.py 和 split_episode.py 共享。

计数规则：非空白 Unicode 字符总数（含标点，跳过所有空白:空格/制表/换行/全角空格）。
与 find_char_offset 的 counted 累积口径一致 —— 后者按非空白字符累计找 target 个,
前者数总数。两者口径一致后,peek 的 split_target_chars = count_chars(text[:offset])
可以直接喂给 split.find_char_offset 找到等价 offset。
"""


def count_chars(text: str) -> int:
    """非空白 Unicode 字符总数(含标点;空格/制表/换行/全角空格等均跳)。

    与 find_char_offset 的 counted 累积口径一致。早期实现按 line.strip() 长度求和,
    包含行内空白(单行 "hello world" 计 11),而 find_char_offset 的 counted 累计只
    认非空白字符(同样输入最多累 10)。zh 文本几乎无内嵌空白故长期未暴露,en/vi 路径
    打开后会让 split_target_chars 比 split 端能达到的 counted 上限大近千,导致
    target_offset 跑到末尾、anchor 搜索窗口落空。
    """
    return sum(1 for c in text if not c.isspace())


def find_char_offset(text: str, target_count: int) -> int:
    """将有效字数转换为原文字符偏移位置。

    遍历原文，跳过空行中的字符，当累计有效字数达到 target_count 时，
    返回对应的原文字符偏移（0-based）。

    如果 target_count 超过总有效字数，返回文本末尾偏移。
    """
    counted = 0
    lines = text.split("\n")
    pos = 0  # 原文中的字符位置

    for line_idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            # 空行：跳过整行（含换行符）
            pos += len(line)
            if line_idx < len(lines) - 1:
                pos += 1  # 换行符
            continue

        # 非空行：逐字符计数
        for char_idx, char in enumerate(line):
            if not char.strip():
                # 行首/行尾空白不计入有效字数，但推进偏移
                pos += 1
                continue
            counted += 1
            if counted >= target_count:
                return pos
            pos += 1

        if line_idx < len(lines) - 1:
            pos += 1  # 换行符

    return pos


_ZH_SENTENCE_ENDINGS = frozenset({"。", "！", "？", "…"})
_LATIN_SENTENCE_ENDINGS = frozenset({".", "!", "?", "…"})


def find_natural_breakpoints(
    text: str, center_offset: int, window: int = 200, language: str | None = None
) -> list[dict]:
    """在指定偏移附近查找自然断点（句号、段落边界等）。

    返回断点列表，每个断点包含：
    - offset: 原文字符偏移
    - char: 断点字符
    - type: 断点类型（sentence/paragraph）
    - distance: 距离 center_offset 的字符数

    language: zh (默认,与历史行为一致) 用 CJK 标点 `。！？…`;en/vi 用 ASCII `. ! ? …`。
    缺省/未知语言走 zh 路径(向后兼容老调用方,如未指定 language 的早期 peek)。
    """
    start = max(0, center_offset - window)
    end = min(len(text), center_offset + window)

    code = (language or "").strip().lower()
    sentence_endings = _LATIN_SENTENCE_ENDINGS if code in ("en", "vi") else _ZH_SENTENCE_ENDINGS
    breakpoints = []

    for i in range(start, end):
        ch = text[i]
        if ch == "\n" and i + 1 < len(text) and text[i + 1] == "\n":
            breakpoints.append(
                {
                    "offset": i + 1,
                    "char": "\\n\\n",
                    "type": "paragraph",
                    "distance": abs(i + 1 - center_offset),
                }
            )
        elif ch in sentence_endings:
            breakpoints.append(
                {
                    "offset": i + 1,  # 在标点之后切分
                    "char": ch,
                    "type": "sentence",
                    "distance": abs(i + 1 - center_offset),
                }
            )

    # 按距离排序
    breakpoints.sort(key=lambda bp: bp["distance"])
    return breakpoints
