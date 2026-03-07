"""
_text_utils.py - 分集切分共享工具函数

提供字数计数和字符偏移转换功能，供 peek_split_point.py 和 split_episode.py 共享。

计数规则：含标点，不含空行（纯空白行不计入字数）。
"""


def count_chars(text: str) -> int:
    """计算有效字数：所有非空行中的字符总数（含标点，不含空行）。"""
    total = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:  # 跳过空行
            total += len(stripped)
    return total


def find_char_offset(text: str, target_count: int) -> int:
    """将有效字数转换为原文字符偏移位置。

    遍历原文，跳过空行中的字符，当累计有效字数达到 target_count 时，
    返回对应的原文字符偏移（0-based）。

    如果 target_count 超过总有效字数，返回文本末尾偏移。
    """
    counted = 0
    i = 0
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


def find_natural_breakpoints(text: str, center_offset: int, window: int = 200) -> list[dict]:
    """在指定偏移附近查找自然断点（句号、段落边界等）。

    返回断点列表，每个断点包含：
    - offset: 原文字符偏移
    - char: 断点字符
    - type: 断点类型（sentence/paragraph）
    - distance: 距离 center_offset 的字符数
    """
    start = max(0, center_offset - window)
    end = min(len(text), center_offset + window)

    sentence_endings = {"。", "！", "？", "…", "\u201d"}  # 右双引号
    breakpoints = []

    for i in range(start, end):
        ch = text[i]
        if ch == "\n" and i + 1 < len(text) and text[i + 1] == "\n":
            breakpoints.append({
                "offset": i + 1,
                "char": "\\n\\n",
                "type": "paragraph",
                "distance": abs(i + 1 - center_offset),
            })
        elif ch in sentence_endings:
            breakpoints.append({
                "offset": i + 1,  # 在标点之后切分
                "char": ch,
                "type": "sentence",
                "distance": abs(i + 1 - center_offset),
            })

    # 按距离排序
    breakpoints.sort(key=lambda bp: bp["distance"])
    return breakpoints
