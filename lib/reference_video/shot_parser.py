"""参考视频 prompt 解析器：prompt ↔ Shot[]/references 双向转换。"""

from __future__ import annotations

import re
from collections.abc import Iterator
from typing import Any

from lib.asset_types import BUCKET_KEY
from lib.script_models import ReferenceResource, Shot

#: 镜头行 header：``镜头N：``（中英冒号均可）。时长已收编到 unit 级，header 不带秒数——
#: 旧格式 ``Shot N (Xs):`` / ``镜头N (Xs)：`` 不再解析，按普通描述行处理（不留容忍：
#: 存量落盘由加载时的一次性迁移改写，解析器只认新格式）。
#: 冒号后是裸捕获，首部空白由调用侧 ``lstrip()`` 去除：写成 ``\s*(.*)$`` 会让 ``\s`` 与
#: ``.`` 字符类重叠，对不可信输入（prompt 由用户输入）产生多项式级回溯。
_SHOT_HEADER_RE = re.compile(r"""^镜头\s*\d+\s*[:：](.*)$""")


#: BOM / ZWNBSP。前端按 JS 的 ``\s`` 判行首空白，U+FEFF 属之；Python 的 ``str.strip()``
#: 不认它（``"﻿".isspace()`` 为 False）。不归一会让带 BOM 的行在前端算规范台词行、
#: 在后端算描述行——说话人是否进参考图两侧结论相反，而 references 落盘取决于哪侧先跑。
#: BOM 在正文里没有语义，解析入口一次性去掉，两条派生路径回到同一口径。
_BOM = "﻿"


def _strip_bom(text: str) -> str:
    """去掉文本中全部 U+FEFF。

    不止文档开头：粘贴拼接会把 BOM 带到任意行首，而分叉是按行发生的。故归一落在三个
    行级原语（``_strip_shot_header`` / ``match_dialogue_line`` / ``match_voiceover_line``）
    上——它们各自与前端同名函数互为镜像，单独调用时也须同判；``parse_prompt`` 另做一次
    整体归一，让派生出的 shot 文本本身不带 BOM（它会进预览显示与后端渲染）。
    """
    return text.replace(_BOM, "") if _BOM in text else text


def _is_ascii_word_char(ch: str) -> bool:
    return ch == "_" or (ch.isascii() and ch.isalnum())


def _is_legacy_mention_char(ch: str) -> bool:
    return ch == "_" or (ch.isascii() and ch.isalnum()) or ("\u4e00" <= ch <= "\u9fff")


def _next_positions(text: str, targets: set[str]) -> list[int]:
    next_pos = [len(text)] * (len(text) + 1)
    for i in range(len(text) - 1, -1, -1):
        next_pos[i] = i if text[i] in targets else next_pos[i + 1]
    return next_pos


def _iter_mentions(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield (start, end, name) for @名称 / @[名称] mentions.

    The left side of `@` must not be an ASCII word character, otherwise the text
    is treated as an email/id fragment. Wrapped mentions may contain punctuation
    but cannot cross line breaks. Curly-brace wrapping is intentionally excluded
    because the editor only writes `@[名称]` and the runtime contract stays on a
    single wrapped form.
    """
    next_square = _next_positions(text, {"]"})
    next_line_break = _next_positions(text, {"\r", "\n"})
    i = 0
    while i < len(text):
        if text[i] != "@":
            i += 1
            continue

        if i > 0 and _is_ascii_word_char(text[i - 1]):
            i += 1
            continue

        if i + 1 >= len(text):
            i += 1
            continue

        opener = text[i + 1]
        if opener == "[":
            start = i + 2
            close = next_square[start]
            if start < close < next_line_break[start]:
                yield i, close + 1, text[start:close]
                i = close + 1
                continue
            i += 1
            continue

        j = i + 1
        while j < len(text) and _is_legacy_mention_char(text[j]):
            j += 1
        if j > i + 1:
            yield i, j, text[i + 1 : j]
            i = j
            continue
        i += 1


def _strip_shot_header(line: str) -> str:
    """去掉行首的 ``镜头N：`` header，返回 header 之后的正文；无 header 时原样返回。"""
    m = _SHOT_HEADER_RE.match(_strip_bom(line).strip())
    return m.group(1).lstrip() if m else line


def match_dialogue_line(line: str) -> tuple[str, str] | None:
    """规范台词行 ``@[角色]：{台词}``（中英冒号均可）→ ``(speaker, text)``；不匹配返回 ``None``。

    整行仅此结构才算规范行：台词与描述混写在同一行时不匹配（由调用侧出 warning），
    杜绝「行内最近 mention 猜 speaker」式启发式——推断错误会把台词静默绑到错误角色的
    参考音频上。speaker 位复用 :func:`_iter_mentions`，与 mention 语法同一份真相。

    speaker 位全为空白（``@[ ]：{台词}``）不算规范行：``Utterance`` 要求 dialogue 带非空
    speaker，放行会让只读派生抛校验错；判为非规范后走既有「台词混写描述行」warning 路径。
    """
    stripped = _strip_bom(line).strip()
    if not stripped.startswith("@"):
        return None
    first = next(_iter_mentions(stripped), None)
    if first is None or first[0] != 0:
        return None
    rest = stripped[first[1] :].lstrip()
    if not rest or rest[0] not in "：:":
        return None
    spoken = _unwrap_braces(rest[1:])
    speaker = first[2]
    if spoken is None or not speaker.strip():
        return None
    return speaker, spoken


def match_voiceover_line(line: str) -> str | None:
    """裸 ``{台词}`` 行 = 画外音 → 台词正文；不匹配返回 ``None``。"""
    return _unwrap_braces(_strip_bom(line))


def _unwrap_braces(text: str) -> str | None:
    """``{…}`` 整体包裹判定：去空白后须以 ``{`` 开头、``}`` 结尾且内部无花括号。

    空台词（``{}`` / ``{   }``）不算：``Utterance`` 与 ``DataValidator._validate_utterances``
    都要求 text 非空，派生出空台词会既进不了校验、又在预览里凭空多出一条没有内容的发声。
    判为非规范后走既有 warning 路径，作者能看见这行没被认成台词。
    """
    body = text.strip()
    if len(body) < 2 or body[0] != "{" or body[-1] != "}":
        return None
    inner = body[1:-1]
    if "{" in inner or "}" in inner or not inner.strip():
        return None
    return inner


def parse_prompt(text: str) -> tuple[list[Shot], list[str]]:
    """把用户书写的 prompt 文本拆为 (shots, mention_names)。

    返回的第二项是 prompt 中出现的名字列表（保持首次出现的顺序、去重），
    由 caller 结合 project.json 分派成 ReferenceResource（本函数不区分 type）。

    - 有 `镜头N：` header → 按 header 切分
    - 无 header → 整段视为单镜头

    时长不从文本解析：它是 unit 级字段，由 caller 从请求 / 剧本读取（见
    ``ReferenceVideoUnit.duration_seconds``）。
    """
    text = _strip_bom(text)
    lines = text.splitlines()
    segments: list[str] = []
    started = False
    current_buf: list[str] = []

    for line in lines:
        m = _SHOT_HEADER_RE.match(line.strip())
        if m:
            header_rest = m.group(1).lstrip()
            if started:
                segments.append("\n".join(current_buf).strip())
                current_buf = [header_rest]
            else:
                # 首个 header 之前的非空文本保留，前置到首镜头 text
                pre_header = "\n".join(current_buf).strip()
                current_buf = [pre_header, header_rest] if pre_header else [header_rest]
                started = True
        else:
            current_buf.append(line)

    if started:
        segments.append("\n".join(current_buf).strip())

    if not segments:
        # 无 header → 单镜头
        return [Shot(text=text.strip())], extract_mentions(text)

    return [Shot(text=t) for t in segments], extract_mentions(text)


def extract_mentions(text: str) -> list[str]:
    """提取文本中的 @ 引用名（保持首次出现顺序、去重）。

    与 ``parse_prompt`` 的 mention 口径同源；参考生视频 step1 拆分工具据此从
    shot 文本机械派生 unit 的 references 列表（顺序即参考图编号）。

    **规范台词行整行不计入**：给画外说话的角色附参考图会诱导模型把他画进画面，故
    ``@[角色]：{台词}`` 行的 speaker 位只驱动音色声明与 utterance 派生，不进参考图。
    纯画外角色因此没有参考图条目，但台词与音色声明照常。

    规范行判定在剥掉 ``镜头N：`` header 之后进行：``parse_prompt`` 切分镜头时会把 header
    去掉，写在 header 同一行的台词在 shot 文本里就是规范行、照常派生 utterance——此处若按
    原始行判定，同一行会既派生 utterance 又留下参考图，两处口径分叉。
    """
    seen: set[str] = set()
    result: list[str] = []
    for line in text.splitlines():
        if match_dialogue_line(_strip_shot_header(line)) is not None:
            continue
        for _start, _end, name in _iter_mentions(line):
            if name not in seen:
                seen.add(name)
                result.append(name)
    return result


def rederive_unit_references(units: list[Any], project: dict) -> None:
    """就地按各 unit 的 shot 文本 ``@[名称]`` 引用机械重派生 references（并集、首现顺序，
    顺序即 [图N] 编号）。

    web 审阅编辑 shot 文本后回写时调用：references 是从正文机械派生的字段（拆分工具产出时即如此），
    若不随编辑重派生，正文改了引用而 references 停留旧值，step2 会以陈旧 [图N] 映射生成——正是
    结构化 step1 要从工程上消除的不一致类。只做机械派生，不校验能力上限 / 引用完整性（未登记的
    名称静默落入 missing、不进 references，正文 @mention 渲染时原样保留）——与 web 审阅对 drama /
    narration 只做结构校验、把越限留待 step2 读回 / 供应商侧同口径。
    """
    for unit in units:
        if not isinstance(unit, dict):
            continue
        shots = unit.get("shots") or []
        text = "\n".join(str(s.get("text") or "") for s in shots if isinstance(s, dict))
        refs, _missing = resolve_references(extract_mentions(text), project)
        unit["references"] = [r.model_dump() for r in refs]


def render_prompt_for_backend(text: str, references: list[ReferenceResource]) -> str:
    """把 prompt 中的 @mention 替换为 [图N]，其中 N 是 references 列表中 1-based 序号。"""
    index_by_name: dict[str, int] = {}
    for i, ref in enumerate(references, start=1):
        index_by_name[ref.name] = i

    parts: list[str] = []
    last = 0
    for start, end, name in _iter_mentions(text):
        idx = index_by_name.get(name)
        parts.append(text[last:start])
        parts.append(f"[图{idx}]" if idx else text[start:end])  # 未注册 → 保留原样
        last = end

    parts.append(text[last:])
    return "".join(parts)


def assemble_shots_text(shots: list[Any]) -> str:
    """把 unit.shots[*].text 拼接为单一原始 prompt（渲染、@→[图N] 替换之前）。

    供入队守卫点对参考生视频做空提示词结构校验：``render_prompt_for_backend`` 对未注册
    的 @mention 保留原文、从不删字，故「拼接文本去空白后为空」等价于「渲染后为空」，
    空检查可无损地在入队侧完成。

    对畸形数据做防御性归一化（Agent 可裸写 script JSON，绕过 ProjectManager 校验）：
    非 dict 的 shot 元素跳过；``text`` 缺失或非字符串（含显式 ``null``）按空串处理——
    否则 ``str(None)`` 会得到 truthy 的 "None" 既绕过空校验又把字面量注入 backend。
    """
    parts: list[str] = []
    for s in shots:
        if not isinstance(s, dict):
            continue
        text = s.get("text")
        parts.append(text if isinstance(text, str) else "")
    return "\n".join(parts)


def resolve_references(
    names: list[str],
    project: dict,
) -> tuple[list[ReferenceResource], list[str]]:
    """按 project.json 三 bucket 把 mention 名字分派成 ReferenceResource。

    当同一名称同时存在于多个 bucket 时，优先级为 character → scene → prop。

    Returns:
        (refs, missing): refs 保持入参顺序；missing 是没在任何 bucket 找到的名字
    """
    buckets: dict[str, dict] = {
        "character": project.get(BUCKET_KEY["character"]) or {},
        "scene": project.get(BUCKET_KEY["scene"]) or {},
        "prop": project.get(BUCKET_KEY["prop"]) or {},
    }
    refs: list[ReferenceResource] = []
    missing: list[str] = []
    for name in names:
        resolved = False
        for rtype, bucket in buckets.items():
            if name in bucket:
                refs.append(ReferenceResource(type=rtype, name=name))  # type: ignore[arg-type]
                resolved = True
                break
        if not resolved:
            missing.append(name)
    return refs, missing
