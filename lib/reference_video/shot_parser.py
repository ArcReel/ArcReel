"""参考视频 prompt 解析器：prompt ↔ Shot[]/references 双向转换。"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterator
from typing import Any

from lib.asset_types import BUCKET_KEY, asset_name_comparison_key, normalize_asset_bucket
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


def _normalize_source(text: str) -> str:
    """书写层文本的入口归一：去掉全部 U+FEFF，并把编码形式收敛到 Unicode NFC。

    两者同一性质——屏幕上看不见的字节差异，却让按字节走的判定分叉，故合并在一个入口处理。
    BOM 不止出现在文档开头：粘贴拼接会把它带到任意行首，而分叉是按行发生的。NFC 则是
    资产名比对的坐标系（见 :func:`lib.asset_types.asset_name_comparison_key`）：说话人位与
    ``@[名称]`` 引用都要与资产表的 key 判等，正文以 NFD 落盘、资产表以 NFC 登记时两者
    肉眼同字却判不相等。

    归一落在四个行级原语（``strip_shot_header`` / ``match_dialogue_line`` /
    ``match_voiceover_line`` / ``leading_mention_before_colon``）与 ``find_malformed_mention``
    上——它们各自与前端同名函数互为镜像，单独调用时也须同判；``parse_prompt`` 另做一次整体
    归一，让派生出的 shot 文本本身已归一（它会进预览显示、后端渲染与落盘）。名字提取出口再
    经比对 helper 去除两端空白；因此说话人名与 mention 名一律已 strip + NFC，台词文本已是 NFC。
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


def strip_shot_header(line: str) -> str:
    """去掉行首的 ``镜头N：`` header，返回 header 之后的正文；无 header 时返回归一后的整行。

    两条分支都经 :func:`_normalize_source`：本函数是 ``_content_lines`` 逐行取正文的入口，
    无 header 分支若原样返回，描述行就绕过了归一，行内 ``@[名称]`` 与资产表的比对又回到
    两种坐标系。
    """
    normalized = _normalize_source(line)
    m = _SHOT_HEADER_RE.match(normalized.strip())
    return m.group(1).lstrip() if m else normalized


def match_dialogue_line(line: str) -> tuple[str, str] | None:
    """规范台词行 ``@[角色]：{台词}``（中英冒号均可）→ ``(speaker, text)``；不匹配返回 ``None``。

    整行仅此结构才算规范行：台词与描述混写在同一行时不匹配（由调用侧出 warning），
    杜绝「行内最近 mention 猜 speaker」式启发式——推断错误会把台词静默绑到错误角色的
    参考音频上。speaker 位复用 :func:`_iter_mentions`，与 mention 语法同一份真相。

    speaker 位全为空白（``@[ ]：{台词}``）不算规范行：``Utterance`` 要求 dialogue 带非空
    speaker，放行会让只读派生抛校验错；判为非规范后走既有「台词混写描述行」warning 路径。
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
    spoken = _unwrap_braces(rest[1:])
    speaker = asset_name_comparison_key(first[2])
    if spoken is None or not speaker:
        return None
    return speaker, spoken


def leading_mention_before_colon(line: str) -> str | None:
    """行首为 ``@[名称]：`` 形态时返回该名称，否则返回 ``None``（只看名称与冒号，不判花括号）。

    ``match_dialogue_line`` 是「整行合规才算台词」的严判，两者之差即「本想写台词但写坏了」：
    漏花括号、花括号不整体包裹、说话人位空白。机器产物校验据此把这类行判违约，而不是让它
    以画面描述的身份放行（说话人会被派生成参考图、台词则整句消失）。返回名称而不是布尔，
    是因为这一形态还要看名称是不是角色——场景 / 道具做小标题（``@[酒馆]：木门被风吹开``）
    是合法的画面描述写法，不能与漏花括号的台词混为一谈。
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


def match_voiceover_line(line: str) -> str | None:
    """裸 ``{台词}`` 行 = 画外音 → 台词正文；不匹配返回 ``None``。"""
    return _unwrap_braces(_normalize_source(line))


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
    text = _normalize_source(text)
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


def render_shots_text(shots: list[Any]) -> str:
    """``parse_prompt`` 的逆向：把 shots 还原为带 ``镜头N：`` header 的书写层正文。

    落盘的 step1 / 剧本只存切分后的 shots，而 step2 的 prompt 输入、保结构 diff 的比对项
    都是书写层正文；缺这个逆向，``assemble_shots_text`` 的裸拼接会丢掉 header，再解析回来
    整个 unit 塌成一个镜头。镜头正文可跨多行（台词行在描述行之下），故 header 只加在首行。
    """
    blocks: list[str] = []
    for index, shot in enumerate(shots, start=1):
        text = shot.get("text") if isinstance(shot, dict) else getattr(shot, "text", None)
        body = text if isinstance(text, str) else ""
        head, _, rest = body.partition("\n")
        blocks.append(f"镜头{index}：{head}" + (f"\n{rest}" if rest else ""))
    return "\n".join(blocks)


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
    for raw_line in text.splitlines():
        line = _normalize_source(raw_line)
        if match_dialogue_line(strip_shot_header(line)) is not None:
            continue
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
    """书写层正文 → ``(references, missing)`` 的唯一派生入口。

    references 是机械派生物，两条来源共用本函数：机器产物（拆分工具 / step2 合并，经
    ``lib.reference_video.draft_validation.validate_unit_text``）与人写产物（编辑器审阅回写，
    经 ``rederive_unit_references``）。两者的**严格度**按「产物来源是否有作者意图可保护」分流
    ——机器产物对 ``missing`` 与能力上限一律拒，人写产物只静默丢 ``missing``、不判上限——但
    **派生本身**必须同一套：分成两处各自 ``extract_mentions`` + ``resolve_references`` 时，任一
    侧的口径调整（如规范台词行不计入参考图）都会让同一份正文在编辑器与生成侧派生出不同的
    ``[图N]`` 编号。
    """
    return resolve_references(extract_mentions(text), project)


def rederive_unit_references(units: list[Any], project: dict) -> None:
    """就地按各 unit 的 shot 文本 ``@[名称]`` 引用机械重派生 references（并集、首现顺序，
    顺序即参考图编号）。

    web 审阅编辑 shot 文本后回写时调用：references 是从正文机械派生的字段（拆分工具产出时即如此），
    若不随编辑重派生，正文改了引用而 references 停留旧值，step2 会以陈旧的参考图映射生成——正是
    结构化 step1 要从工程上消除的不一致类。只做机械派生，不校验能力上限 / 引用完整性（未登记的
    名称静默落入 missing、不进 references，正文 @mention 渲染时原样保留）——与 web 审阅对 drama /
    narration 只做结构校验、把越限留待 step2 读回 / 供应商侧同口径。
    """
    for unit in units:
        if not isinstance(unit, dict):
            continue
        shots = unit.get("shots") or []
        text = "\n".join(str(s.get("text") or "") for s in shots if isinstance(s, dict))
        refs, _missing = derive_references_from_text(text, project)
        unit["references"] = [r.model_dump() for r in refs]


def render_mentions_as_subjects(text: str, names: Collection[str]) -> str:
    """把 prompt 中的 ``@[X]`` / ``@X`` 替换为三段论的主体记号 ``<X>``。

    ``names`` 是资产表里已登记的名字——``<X>`` 是画面主体记号、不指向参考图编号，故不随
    能力上限裁剪收窄。不在其中的 mention 原样保留、从不删字（据此「拼接文本去空白后为空」
    等价于「渲染后为空」，空提示词校验可在入队侧无损完成）。

    正文与 ``names`` 都归一到比对坐标系后再判成员：不归一时，NFD 落盘的 ``@[名称]`` 与 NFC
    登记的同一个名字判不相等，该 mention 会被当成未登记而原样保留，``@[名称]`` 这个书写层
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


def assemble_shots_text(shots: list[Any]) -> str:
    """把 unit.shots[*].text 拼接为单一原始 prompt（三段论渲染之前的书写层文本）。

    供入队守卫点对参考生视频做空提示词结构校验：三段论渲染恒产出非空的机器段（第一/三段），
    渲染后已无从判别书写层是否为空，故空检查落在本函数的拼接结果上——`@mention` 替换从不
    删字，「拼接文本去空白后为空」等价于「书写层为空」。

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


def assemble_shots_text_for_render(shots: list[Any]) -> str:
    """把 ``unit.shots[*].text`` 拼接为供 :func:`render_unit_prompt` 二次解析的书写层文本。

    与 :func:`assemble_shots_text` 的区别：本函数按数组位置重新注入规范 ``镜头N：`` header
    （先剥掉每个 shot 首行可能残留的旧 header，避免重复）。``parse_prompt`` 只认文本里的
    header 切分镜头，而 ``shots[*].text`` 本身在多条路径下并不带 header——``parse_prompt``
    构造 ``Shot`` 时就会把 header 剥掉，经解析预览面板编辑回写的 unit 因此普遍不带 header；
    不重新注入的话，两个以上镜头拼接后会因为找不到任何 header 被重新解析成一个镜头，
    第二段的分镜结构丢失。空提示词的结构校验用 :func:`assemble_shots_text`（不注入 header，
    保留「空 shots 拼接结果为空」的判定），本函数不作此用途。
    """
    parts: list[str] = []
    for i, s in enumerate(shots, start=1):
        if not isinstance(s, dict):
            continue
        text = s.get("text")
        text = text if isinstance(text, str) else ""
        lines = text.split("\n") if text else [""]
        lines[0] = strip_shot_header(lines[0])
        parts.append(f"镜头{i}：" + "\n".join(lines))
    return "\n".join(parts)


def resolve_references(
    names: list[str],
    project: dict,
) -> tuple[list[ReferenceResource], list[str]]:
    """按 project.json 三 bucket 把 mention 名字分派成 ReferenceResource。

    schema v6 起项目资产共用名称空间，一个名字最多命中一个 bucket。

    名字与三张资产表都先归一到比对坐标系（:func:`lib.asset_types.asset_name_comparison_key`），
    产出的 ``ReferenceResource.name`` 与 ``missing`` 因此一律是归一形式：下游拿它回查资产表、
    与说话人判等、在正文里替换成主体记号 ``<X>``，三处都要与这里的判定同形，否则「这里判已
    登记、下游查不到」。入参 ``names`` 通常已出自本模块的解析器（已归一），归一是幂等的补齐，
    覆盖直接传外部名字的调用方。

    Returns:
        (refs, missing): refs 保持入参顺序；missing 是没在任何 bucket 找到的名字
    """
    buckets: dict[str, dict[str, Any]] = {
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
        matches = [rtype for rtype, bucket in buckets.items() if name in bucket]
        if len(matches) == 1:
            refs.append(ReferenceResource(type=matches[0], name=name))  # type: ignore[arg-type]
        elif not matches:
            missing.append(name)
        else:
            raise ValueError(f"项目资产名称空间损坏：{name!r} 同时存在于 {', '.join(matches)}")
    return refs, missing
