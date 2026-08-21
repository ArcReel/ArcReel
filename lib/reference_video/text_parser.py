"""参考生视频正文解析器：引用语法记号（``@[名称]`` 引用、台词与画外音）的识别原语。

视频单元只持久化正文，参考图与发声归属都由本模块从正文读时派生（见 ADR 0064）：解析结果
不落盘，改正文即改一切派生物。"""

from __future__ import annotations

import unicodedata
from collections.abc import Collection, Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from lib.asset_types import BUCKET_KEY, asset_name_comparison_key, normalize_asset_bucket
from lib.script_models import ReferenceResource

#: BOM / ZWNBSP。前端按 JS 的 ``\s`` 判行首空白，U+FEFF 属之；Python 的 ``str.strip()``
#: 不认它（``"﻿".isspace()`` 为 False）。不归一会让带 BOM 的记号在前端认、
#: 在后端不认——同一份正文在编辑器与执行期派生出不同的参考图。
#: BOM 在正文里没有语义，解析入口一次性去掉，两条派生路径回到同一口径。
_BOM = "﻿"

#: 说话人 mention 与 ``{`` 之间允许出现的分隔冒号（中英各一）。只允许一个：写了第二个
#: 就说明这不是「``@[角色]：{台词}``」的形态，此时宁可不成记号，也不静默降级成画外音。
_SPEAKER_SEPARATORS = "：:"


def _normalize_source(text: str) -> str:
    """引用语法文本的入口归一：去掉全部 U+FEFF，并把编码形式收敛到 Unicode NFC。

    两者同一性质——屏幕上看不见的字节差异，却让按字节走的判定分叉，故合并在一个入口处理。
    BOM 不止出现在文档开头：粘贴拼接会把它带到任意行首，而分叉是按行发生的。NFC 则是
    资产名比对的坐标系（见 :func:`lib.asset_types.asset_name_comparison_key`）：说话人位与
    ``@[名称]`` 引用都要与资产表的 key 判等，正文以 NFD 落盘、资产表以 NFC 登记时两者
    肉眼同字却判不相等。

    归一落在三个行级原语（``split_speech_line`` / ``leading_mention_before_colon`` /
    ``find_malformed_mention``）上——它们各自与前端同名函数互为镜像，单独调用时也须同判。
    名字提取出口再经比对 helper 去除两端空白；因此说话人名与 mention 名一律已 strip + NFC，
    台词文本已是 NFC。
    """
    stripped = text.replace(_BOM, "") if _BOM in text else text
    return unicodedata.normalize("NFC", stripped)


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


@dataclass(frozen=True)
class SpeechMark:
    """行内一段发声记号的解析结果。

    ``speaker`` 为空串即画外音（``{台词}``），非空即该角色说这句话（``@[角色]{台词}``）；
    名称已归一到资产名比对坐标系。``text`` 是花括号内的逐字台词。

    ``raw`` 是该记号在归一后原行里占据的整段原文（含说话人 mention 与其后的空白 / 冒号）：
    「从不删字」的渲染路径（未登记说话人）据此原样回填，无须再回原行按偏移取。
    """

    speaker: str
    text: str
    raw: str


def split_speech_line(line: str) -> list[str | SpeechMark]:
    """把一行拆成「画面描述片段」与「发声记号」的有序序列（记号可出现在行内任意位置）。

    发声记号有两种，语言无关：``{台词}`` 是画外音；紧接在 ``@[角色]`` 之后（中间允许空白
    或一个中英冒号）的 ``{台词}`` 是该角色说这句话。``@[角色]：{台词}`` 独占一行是后者的
    一个特例，同样合法。

    说话人只认「紧贴花括号之前的那个 mention」，不做「行内最近 mention 猜 speaker」式
    启发式——推断错误会把台词静默绑到错误角色的参考音频上。以下三种一律不成记号，花括号
    留在描述片段里，由调用侧按各自严格度出 warning 或判违约：

    - 空台词（``{}`` / ``{   }``）：``Utterance`` 要求 text 非空，放行会派生出没有内容的发声。
    - 说话人位为空白（``@[ ]{台词}``）：dialogue 要求非空 speaker。
    - 说话人位写坏（``@[]{台词}``、``@[李明{台词}``、``@[张三]：：{台词}``）：花括号前是 ``]``
      或又一个分隔冒号、却没有可用 mention 时不降级成画外音——作者写的是「某人说」，静默改成
      画外音比不识别更难发现。

    拼接结果（描述片段 + 各记号的 ``raw``）逐字等于归一后的原行，故 ``strip_speech_marks``
    是无损切分的另一半，不会吞字。
    """
    text = _normalize_source(line)
    mentions = list(_iter_mentions(text))
    parts: list[str | SpeechMark] = []
    cursor = 0
    scan = 0
    while True:
        open_index = text.find("{", scan)
        if open_index < 0:
            break
        close_index = text.find("}", open_index + 1)
        if close_index < 0:
            break
        inner = text[open_index + 1 : close_index]
        if "{" in inner:
            # 嵌套 / 漏闭合：外层 ``{`` 不成记号，从内层重新扫描。
            scan = open_index + 1
            continue
        if not inner.strip():
            scan = close_index + 1
            continue

        head = open_index
        while head > cursor and text[head - 1].isspace():
            head -= 1
        separator_colon = False
        if head > cursor and text[head - 1] in _SPEAKER_SEPARATORS:
            head -= 1
            separator_colon = True
            while head > cursor and text[head - 1].isspace():
                head -= 1

        speaker = ""
        start = open_index
        mention = next((m for m in mentions if m[1] == head and m[0] >= cursor), None)
        if mention is not None:
            speaker = asset_name_comparison_key(mention[2])
            if not speaker:
                scan = close_index + 1
                continue
            start = mention[0]
        elif head > cursor and (text[head - 1] == "]" or (separator_colon and text[head - 1] in _SPEAKER_SEPARATORS)):
            scan = close_index + 1
            continue

        if start > cursor:
            parts.append(text[cursor:start])
        parts.append(SpeechMark(speaker=speaker, text=inner, raw=text[start : close_index + 1]))
        cursor = close_index + 1
        scan = cursor

    if cursor < len(text):
        parts.append(text[cursor:])
    return parts


def line_speech_marks(line: str) -> list[SpeechMark]:
    """一行里按出现顺序排列的发声记号。"""
    return [part for part in split_speech_line(line) if isinstance(part, SpeechMark)]


def speech_line_description(parts: Iterable[str | SpeechMark]) -> str:
    """``split_speech_line`` 结果里记号之外的残余文本，即这一行的画面描述。

    收在此处而不是各调用侧就地 join：同时要记号与残余的调用方（预览派生、发声准入、草稿
    校验）只能切一次再各取一半，否则「什么算描述」会在三处各写一遍、日后随记号语法一起漂移。
    """
    return "".join(part for part in parts if isinstance(part, str))


def strip_speech_marks(line: str) -> str:
    """去掉全部发声记号后剩下的画面描述文本（归一形）。

    参考图派生与产物依据都按此文本判定：只在花括号前出现的角色只绑声音、不进画面参考，
    而同一行里写在记号之外的 ``@[名称]`` 照常进参考图。
    """
    return speech_line_description(split_speech_line(line))


def leading_mention_before_colon(line: str) -> str | None:
    """行首为 ``@[名称]：`` 形态时返回该名称，否则返回 ``None``（只看名称与冒号，不判花括号）。

    与「这行首是不是一个发声记号」的严判之差即「本想写台词但写坏了」：漏花括号、花括号不
    成对、说话人位空白。机器产物校验据此把这类行判违约，而不是让它以画面描述的身份放行
    （说话人会被派生成参考图、台词则整句消失）。返回名称而不是布尔，是因为这一形态还要看
    名称是不是角色——场景 / 道具做小标题（``@[酒馆]：木门被风吹开``）是合法的画面描述写法，
    不能与漏花括号的台词混为一谈。
    """
    stripped = _normalize_source(line).strip()
    if not stripped.startswith("@"):
        return None
    first = next(_iter_mentions(stripped), None)
    if first is None or first[0] != 0:
        return None
    rest = stripped[first[1] :].lstrip()
    if not rest or rest[0] not in "：:":
        return None
    return asset_name_comparison_key(first[2])


def find_malformed_mention(line: str) -> str | None:
    """返回行内首个写坏的 ``@[`` 引用片段（如 ``@[李明`` / ``@[]``）；没有则返回 ``None``。

    ``_iter_mentions`` 对这类 token 静默不产出 mention，正文里的坏 token 因此既不进
    references，又会被 ``render_mentions_as_subjects`` 原样带进供应商请求（它只替换认得的
    mention、从不删字）。左侧是 ASCII 词字符时按邮箱 / id 片段跳过，与 ``_iter_mentions`` 同口径。

    全角形（``＠[李明]`` / ``@［李明］``）一并算坏 token：中文输入法下模型很容易写出，而语法只认
    半角，静默放行的后果同样是那张参考图从视频请求里消失。
    """
    text = _normalize_source(line)
    for index, char in enumerate(text):
        if char == "＠" or (char == "@" and text[index + 1 : index + 2] == "［"):
            return text[index : index + 20]
    starts = {start for start, _end, _name in _iter_mentions(text)}
    for index in range(len(text) - 1):
        if text[index] != "@" or text[index + 1] != "[" or index in starts:
            continue
        if index > 0 and _is_ascii_word_char(text[index - 1]):
            continue
        return text[index : index + 20]
    return None


def extract_mentions(text: str) -> list[str]:
    """提取正文中的 ``@`` 引用名（保持首次出现顺序、去重）。

    顺序即执行期参考图的编号顺序：正文是唯一真相，没有另一份可以与它分叉的引用列表。

    **发声记号内的说话人位不计入**：给画外说话的角色附参考图会诱导模型把他画进画面，故
    ``@[角色]{台词}`` 的 speaker 位只驱动音色声明与 utterance 派生，不进参考图。只在记号前
    出现过的角色因此没有参考图，但台词与音色声明照常；同一行写在记号之外的 ``@[名称]``
    照常进参考图。
    """
    seen: set[str] = set()
    result: list[str] = []
    for raw_line in text.splitlines():
        line = strip_speech_marks(raw_line)
        for _start, _end, raw_name in _iter_mentions(line):
            name = asset_name_comparison_key(raw_name)
            if name not in seen:
                seen.add(name)
                result.append(name)
    return result


def rewrite_mentions(text: str, old_name: str, new_name: str) -> tuple[str, int]:
    """把文本中指向 *old_name* 的 @ 引用改写为 ``@[new_name]``，返回 ``(新文本, 改写数)``。

    资产重命名的正文改写原语：与 ``_iter_mentions`` 同口径识别 mention（含旧式裸 ``@名字``，
    改写时一并升格为包裹形式），名字判等走比对坐标系（NFC）——正文以 NFD 落盘的同名 mention
    也会被命中改写。不做其他归一（不去 BOM、不整体 NFC），未命中的字符原样保留：重命名只
    该改名字本身，不该顺带改写正文的编码形式。已经是目标形式的 mention 不计入改写数。
    """
    target = asset_name_comparison_key(old_name)
    replacement = f"@[{new_name}]"
    pieces: list[str] = []
    last = 0
    count = 0
    for start, end, name in _iter_mentions(text):
        if asset_name_comparison_key(name) != target or text[start:end] == replacement:
            continue
        pieces.append(text[last:start])
        pieces.append(replacement)
        last = end
        count += 1
    if not count:
        return text, 0
    pieces.append(text[last:])
    return "".join(pieces), count


def derive_references_from_text(text: str, project: dict) -> tuple[list[ReferenceResource], list[str]]:
    """引用语法正文 → ``(references, missing)`` 的唯一派生入口。

    参考图是纯派生物、不落盘：机器产物校验（``lib.reference_video.draft_validation
    .validate_unit_text``）、编辑器预览与执行期请求投影都经本函数从同一份正文派生。三者的
    **严格度**按「产物来源是否有作者意图可保护」分流——机器产物对 ``missing`` 与能力上限一律拒，
    人写产物只警告、照常生成——但**派生本身**必须同一套：分成多处各自 ``extract_mentions`` +
    ``resolve_references`` 时，任一侧的口径调整（如台词记号的说话人位不计入参考图）都会让同一份
    正文在编辑器与生成侧派生出不同的 ``图N`` 编号。
    """
    return resolve_references(extract_mentions(text), project)


def render_mentions_as_subjects(text: str, names: Collection[str]) -> str:
    """把 prompt 中的 ``@[X]`` / ``@X`` 替换为三段论的主体记号 ``<X>``。

    ``names`` 是资产表里已登记的名字——``<X>`` 是画面主体记号、不指向参考图编号，故不随
    能力上限裁剪收窄。不在其中的 mention 原样保留、从不删字（据此「拼接文本去空白后为空」
    等价于「渲染后为空」，空提示词校验可在入队侧无损完成）。

    正文与 ``names`` 都归一到比对坐标系后再判成员：不归一时，NFD 落盘的 ``@[名称]`` 与 NFC
    登记的同一个名字判不相等，该 mention 会被当成未登记而原样保留，``@[名称]`` 这个引用语法
    记号就直接漏进了供应商请求。
    """
    normalized_names = {asset_name_comparison_key(name) for name in names}
    text = _normalize_source(text)
    parts: list[str] = []
    last = 0
    for start, end, name in _iter_mentions(text):
        parts.append(text[last:start])
        canonical = asset_name_comparison_key(name)
        parts.append(f"<{canonical}>" if canonical in normalized_names else text[start:end])
        last = end

    parts.append(text[last:])
    return "".join(parts)


def resolve_references(
    names: list[str],
    project: dict,
) -> tuple[list[ReferenceResource], list[str]]:
    """按 project.json 四类资产把 mention 名字分派成 ReferenceResource。

    新项目资产共用名称空间；对历史重复名仍按商品→角色→场景→道具稳定决议。

    名字与三张资产表都先归一到比对坐标系（:func:`lib.asset_types.asset_name_comparison_key`），
    产出的 ``ReferenceResource.name`` 与 ``missing`` 因此一律是归一形式：下游拿它回查资产表、
    与说话人判等、在正文里替换成主体记号 ``<X>``，三处都要与这里的判定同形，否则「这里判已
    登记、下游查不到」。入参 ``names`` 通常已出自本模块的解析器（已归一），归一是幂等的补齐，
    覆盖直接传外部名字的调用方。

    Returns:
        (refs, missing): refs 保持入参顺序；missing 是没在任何 bucket 找到的名字
    """
    buckets: dict[str, dict[str, Any]] = {
        "product": normalize_asset_bucket(project.get(BUCKET_KEY["product"])),
        "character": normalize_asset_bucket(project.get(BUCKET_KEY["character"])),
        "scene": normalize_asset_bucket(project.get(BUCKET_KEY["scene"])),
        "prop": normalize_asset_bucket(project.get(BUCKET_KEY["prop"])),
    }
    refs: list[ReferenceResource] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        name = asset_name_comparison_key(raw_name)
        if name in seen:
            continue
        seen.add(name)
        match = next((rtype for rtype, bucket in buckets.items() if name in bucket), None)
        if match is not None:
            refs.append(ReferenceResource(type=match, name=name))  # type: ignore[arg-type]
        else:
            missing.append(name)
    return refs, missing
