import type { Character, CharacterDerivative, ProjectData } from "@/types";
import type { AssetKind } from "@/types/reference-video";

/**
 * Mention regex shared across frontend tokenizers. Mirrors backend
 * `lib/reference_video/text_parser.py` mention scanner — keep in sync.
 *
 * 前后端字面不同但语义等价：
 * - JS `\w` 永远是 ASCII-only，`(?<!\w)` 直接表达"左侧不是 ASCII 词字符"。
 * - Python `\w` 默认 Unicode-aware（中文属 `\w`），所以后端改用显式
 *   `[A-Za-z0-9_]` 字符类，避免误拒 `你好@张三` 这类中文前缀。
 *
 * CJK 字符（`\u4e00-\u9fff`）在两边都不在词字符集内，所以中文前缀合法。
 *
 * Supports legacy `@名称` plus wrapped `@[名称]` for asset names
 * containing punctuation, spaces, or parentheses.
 *
 * Curly-brace wrapping (`@{名称}`) is intentionally unsupported: the editor
 * only emits `@[名称]`, and braces are the speech syntax (see {@link splitSpeechLine}).
 */
export const MENTION_RE = /(?<!\w)@(?:\[([^\]\r\n]+)\]|([\w\u4e00-\u9fff]+))/g;

/**
 * BOM / ZWNBSP。镜像后端 `text_parser._BOM`：正文里它没有语义，却让按字节走的判定分叉
 * ——JS 的 `\s` 认它、Python 的 `str.strip()` 不认，带 BOM 的记号在前端认、在
 * 后端不认，说话人是否进参考图两侧结论相反。
 */
const BOM_RE = /\uFEFF/gu;

/**
 * 引用语法文本的入口归一：去掉全部 U+FEFF，并把编码形式收敛到 Unicode NFC。镜像后端
 * `lib/reference_video/text_parser.py::_normalize_source`——两条派生路径同口径。
 *
 * 两者同一性质：屏幕上看不见的字节差异，却让按字节走的判定分叉，故合并在一个入口处理。
 * BOM 不止出现在文档开头，粘贴拼接会把它带到任意行首，而分叉是按行发生的；NFC 则是资产名
 * 比对的坐标系（见 {@link normalizeAssetName}），正文以 NFD 书写、资产表以 NFC 登记时肉眼
 * 同字却判不相等。
 *
 * 归一落在解析入口而非提取结果上：BOM 落在名字内部（`@[名<U+FEFF>称]`）时，逐名补归一修不了——
 * 匹配已经按含 BOM 的字节做完了。
 */
function normalizeSource(text: string): string {
  return text.replace(BOM_RE, "").normalize("NFC");
}

/**
 * 从 mention 匹配取名字。归一在此完成而非交给调用方：高亮分词器（`tokenizePrompt`）要
 * 逐字拼回原文、不能归一源文本，只有名字这一路出得来规范形。
 */
export function mentionNameFromMatch(match: RegExpMatchArray): string {
  return normalizeSource(match[1] ?? match[2] ?? "");
}

/** 空台词（`{}` / `{   }`）不算发声记号——同后端：utterance 的 text 必须非空。 */
function hasSpokenText(text: string): boolean {
  return text.trim().length > 0;
}

/**
 * 引用名里分隔本体与衍生的字符。镜像后端 `lib/reference_catalog.py::DERIVATIVE_SEPARATOR`——
 * 它在资产名与衍生名里都非法，故 `本体名/衍生名` 的切分无歧义。
 */
const DERIVATIVE_SEPARATOR = "/";

/**
 * 把一个引用名拆成 `[本体名, 衍生名]`；不是衍生形态时衍生名为空串。镜像后端
 * `lib/reference_catalog.py::split_derivative_reference`。
 */
export function splitDerivativeReference(name: string): [string, string] {
  const at = name.indexOf(DERIVATIVE_SEPARATOR);
  return at < 0 ? [name, ""] : [name.slice(0, at), name.slice(at + 1)];
}

/**
 * 行内一段发声记号；`speaker` 为空串即画外音。`raw` 是它在原行里占据的整段原文。
 *
 * 说话人位写 `@[角色/衍生]` 时 `speaker` 仍是本体名、`derivative` 记下那个衍生名：声音属于
 * 身份，衍生共享本体的参考音频与音色（镜像后端 `text_parser.SpeechMark`）。
 */
export interface SpeechMark {
  speaker: string;
  text: string;
  raw: string;
  derivative: string;
}

/**
 * mention 与 `{` 之间允许的行内空白。JS 的 `\s` 不含 U+001F 而 Python 的 `str.isspace()` 含，
 * 少这一个字符会让 `@[张三]<U+001F>{我来了}` 在后端绑说话人、在前端派生成参考图。
 */
// eslint-disable-next-line no-control-regex
const INLINE_SPACE_RE = /[\s\x1f]/u;

/** 说话人 mention 与 `{` 之间允许出现的分隔冒号（中英各一），只允许一个。 */
const SPEAKER_SEPARATORS = "：:";

/** `splitSpeechLine` 的一段：字符串是画面描述，对象是发声记号。 */
export type SpeechPart = string | SpeechMark;

export function isSpeechMark(part: SpeechPart): part is SpeechMark {
  return typeof part !== "string";
}

/**
 * 把一行拆成「画面描述片段」与「发声记号」的有序序列。镜像后端
 * `lib/reference_video/text_parser.py::split_speech_line`——两条派生路径同口径，
 * 改一侧必须同步改另一侧。
 *
 * 记号可出现在行内任意位置：`{台词}` 是画外音；紧接在 `@[角色]` 之后（中间允许空白或一个
 * 中英冒号）的 `{台词}` 是该角色说这句话。空台词、说话人位为空白、说话人位写坏
 * （`@[]{…}`、`@[张三]：：{…}`）三种一律不成记号，花括号留在描述片段里由调用侧出 warning。
 *
 * 与后端的一处刻意差异：本函数**不归一源文本**，只归一取出的说话人名。高亮分词器要逐字
 * 拼回原文覆盖在 textarea 上，归一会让 token 拼不回去；判定路径（`extractMentions` /
 * `toScriptLines`）的输入来自已归一的 `splitScriptLines`，故两侧结论仍一致。
 *
 * mention 与 `{` 之间的空白按 `INLINE_SPACE_RE` 判——JS 的 `\s` 比 Python 的 `str.isspace()`
 * 少一个 U+001F（更粗的 U+001C–U+001E / U+0085 由 `LINE_BREAK_RE` 当换行切走、根本不在行内，
 * U+FEFF 则被后端入口归一去掉、在此由 `\s` 命中），补进字符类后两侧空白集合逐字符相同。
 */
export function splitSpeechLine(line: string): SpeechPart[] {
  const mentions = [...line.matchAll(MENTION_RE)].map((m) => ({
    start: m.index ?? 0,
    end: (m.index ?? 0) + m[0].length,
    name: normalizeAssetName(mentionNameFromMatch(m)),
  }));
  const parts: SpeechPart[] = [];
  let cursor = 0;
  let scan = 0;
  for (;;) {
    const open = line.indexOf("{", scan);
    if (open < 0) break;
    const close = line.indexOf("}", open + 1);
    if (close < 0) break;
    const inner = line.slice(open + 1, close);
    if (inner.includes("{")) {
      // 嵌套 / 漏闭合：外层 `{` 不成记号，从内层重新扫描。
      scan = open + 1;
      continue;
    }
    if (!hasSpokenText(inner)) {
      scan = close + 1;
      continue;
    }

    let head = open;
    while (head > cursor && INLINE_SPACE_RE.test(line[head - 1])) head -= 1;
    let separatorColon = false;
    if (head > cursor && SPEAKER_SEPARATORS.includes(line[head - 1])) {
      head -= 1;
      separatorColon = true;
      while (head > cursor && INLINE_SPACE_RE.test(line[head - 1])) head -= 1;
    }

    let speaker = "";
    let derivative = "";
    let start = open;
    const mention = mentions.find((m) => m.end === head && m.start >= cursor);
    if (mention) {
      [speaker, derivative] = splitDerivativeReference(mention.name);
      if (!speaker) {
        scan = close + 1;
        continue;
      }
      start = mention.start;
    } else if (
      head > cursor &&
      (line[head - 1] === "]" || (separatorColon && SPEAKER_SEPARATORS.includes(line[head - 1])))
    ) {
      // 写坏的说话人位（`@[]{…}`、`@[张三]：：{…}`）：不静默降级成画外音。
      scan = close + 1;
      continue;
    }

    if (start > cursor) parts.push(line.slice(cursor, start));
    parts.push({ speaker, text: inner, raw: line.slice(start, close + 1), derivative });
    cursor = close + 1;
    scan = cursor;
  }
  if (cursor < line.length) parts.push(line.slice(cursor));
  return parts;
}

/** 一行里按出现顺序排列的发声记号。 */
export function lineSpeechMarks(line: string): SpeechMark[] {
  return splitSpeechLine(line).filter(isSpeechMark);
}

/** 去掉全部发声记号后剩下的画面描述文本——参考图派生按此判定。 */
export function stripSpeechMarks(line: string): string {
  return splitSpeechLine(line)
    .filter((part): part is string => !isSpeechMark(part))
    .join("");
}

/**
 * Python `str.splitlines()` 的换行集合——后端 `text_parser` / `script_preview` 都用它切行。
 * 只按 `\n` 切会把 U+2028 之后的台词记号与上一行粘在一起（粘贴、agent 产出的文本里会出现），
 * 前端据此把说话人算进参考图、后端不算，两条派生路径当场分叉。
 * 带捕获组：`split` 时分隔符原样留在结果里，token 仍可拼回原文。
 *
 * The control characters in the class are deliberate: Python counts the file and
 * group separators as line breaks too, and dropping one reintroduces exactly the
 * front/back divergence this constant exists to remove.
 */
// eslint-disable-next-line no-control-regex
export const LINE_BREAK_RE = /(\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029])/;

/**
 * 按后端同一套换行边界切行（不保留分隔符），行内容已是规范形（见 `normalizeSource`）。
 *
 * 末尾换行不产生空行、空串切出空数组——都与 `splitlines()` 一致；行中间的空行照常保留。
 * 归一不增删换行，行数与下标（`toScriptLines` 的 `sourceLine`）与原文逐行对应。
 */
export function splitScriptLines(text: string): string[] {
  if (text.length === 0) return [];
  const lines = normalizeSource(text).split(LINE_BREAK_RE).filter((_, i) => i % 2 === 0);
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/**
 * Mention names in first-appearance order, deduplicated — the reference-image
 * derivation. Mirrors `text_parser.py:extract_mentions`, including its rule that
 * **the speaker slot of a speech mark is excluded**: attaching a reference image to
 * a speaker would coax the model into drawing a character who only speaks off-screen.
 * 记号之外的 `@[名称]` 照常进参考图，同一行里两者并存。
 *
 * 名字一律是规范形，去重也按规范形——调用方直接拿去与已归一的资产表 key 判等，不再补归一。
 */
export function extractMentions(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const raw of splitScriptLines(text)) {
    const line = stripSpeechMarks(raw);
    for (const m of line.matchAll(MENTION_RE)) {
      const name = normalizeAssetName(mentionNameFromMatch(m));
      if (!seen.has(name)) {
        seen.add(name);
        out.push(name);
      }
    }
  }
  return out;
}

/**
 * 台词记号的说话人，按出现顺序去重。镜像后端
 * `lib/reference_video/draft_validation.py::dialogue_speakers`——「谁在这个单元里发声」只由
 * 说话人位决定，与 `extractMentions`（画面参考图）互补：一个角色可以只发声不出镜，也可以
 * 只出镜不发声，音色相关的判定一律走本函数。
 *
 * 名字取自 `splitSpeechLine`，已归一到资产名比对坐标系，调用方直接与已归一的资产表 key 判等。
 */
export function dialogueSpeakers(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of splitScriptLines(text)) {
    for (const mark of lineSpeechMarks(line)) {
      if (!mark.speaker || seen.has(mark.speaker)) continue;
      seen.add(mark.speaker);
      out.push(mark.speaker);
    }
  }
  return out;
}

type ProjectBuckets = Pick<ProjectData, "characters" | "scenes" | "props" | "products">;
type ProjectAssetKind = AssetKind;
export type MentionLookup = Record<string, ProjectAssetKind>;

// Python str.strip() whitespace set. JavaScript trim() additionally removes U+FEFF,
// but backend asset-name comparison deliberately treats U+FEFF as a name character.
// eslint-disable-next-line no-control-regex
const PYTHON_STRIP_RE = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu;

/**
 * 把资产名归一到项目名称空间的比对坐标系（strip + Unicode NFC）。镜像后端
 * `lib.asset_types.asset_name_comparison_key`——两侧必须同一坐标系，否则「后端判已登记、
 * 前端判未登记」（反之亦然），组合字符名（如越南语）在这两侧各自输入法/来源下
 * 尤其容易产出不同编码形式。
 */
export function normalizeAssetName(name: string): string {
  return name.replace(PYTHON_STRIP_RE, "").normalize("NFC");
}

/**
 * 为编辑器高亮构造项目引用名到类型的唯一映射。镜像后端
 * `lib/reference_catalog.py::build_reference_catalog` 的名字集合：资产名，加上角色的
 * `本体名/衍生名`（衍生共享本体的类型，见 `docs/adr/0072`）。
 *
 * 无原型字典保证 `__proto__` 等合法资产名可作为普通 key。损坏项目若有同名资产，
 * 按 product → character → scene → prop 的稳定优先级解析。
 */
export function buildMentionLookup(project: ProjectBuckets | null | undefined): MentionLookup {
  const lookup: MentionLookup = Object.create(null) as MentionLookup;
  const claim = (name: string, kind: ProjectAssetKind) => {
    const key = normalizeAssetName(name);
    if (!Object.hasOwn(lookup, key)) lookup[key] = kind;
  };
  for (const name of Object.keys(project?.products ?? {})) claim(name, "product");
  for (const form of characterReferenceForms(project?.characters)) claim(form.name, "character");
  for (const name of Object.keys(project?.scenes ?? {})) claim(name, "scene");
  for (const name of Object.keys(project?.props ?? {})) claim(name, "prop");
  return lookup;
}

/** 一个角色形态：本体，或它名下的一个衍生。`asset` 是该形态自己的条目（资产图与描述都取自它）。 */
export interface CharacterForm {
  /** 引用名：本体名，或 `本体名/衍生名`。写进 `@[…]` 与 `characters_in_*` 的就是它。 */
  name: string;
  /** 承载该形态的角色条目名。衍生下与 `name` 分叉，本体下相等。 */
  ownerName: string;
  /** 衍生名；本体形态为空串。 */
  derivativeName: string;
  /** 该形态自己的登记条目：本体是角色条目，衍生是 `derivatives` 表里的那条。 */
  asset: Character | CharacterDerivative;
}

/**
 * 项目里全部可被引用的角色形态，本体在前、其衍生紧随其后。镜像后端
 * `lib/reference_catalog.py::ReferenceCatalog` 对角色类的展开（见 `docs/adr/0072`）：
 * 引用弹窗的候选、头像堆的取图与编辑器高亮都从这一处取名字，不各自遍历 `derivatives`。
 */
export function characterReferenceForms(
  characters: Record<string, Character> | null | undefined,
): CharacterForm[] {
  const forms: CharacterForm[] = [];
  for (const [name, character] of Object.entries(characters ?? {})) {
    forms.push({ name, ownerName: name, derivativeName: "", asset: character });
    for (const [derivativeName, derivative] of Object.entries(character?.derivatives ?? {})) {
      forms.push({
        name: `${name}${DERIVATIVE_SEPARATOR}${derivativeName}`,
        ownerName: name,
        derivativeName,
        asset: derivative,
      });
    }
  }
  return forms;
}

/**
 * 按引用名取角色形态；名字未登记（含「本体在、这个衍生不在」）时返回 undefined。
 * 判等落在资产名比对坐标系上，与 {@link buildMentionLookup} 同口径。
 */
export function resolveCharacterForm(
  characters: Record<string, Character> | null | undefined,
  name: string,
): CharacterForm | undefined {
  const key = normalizeAssetName(name);
  return characterReferenceForms(characters).find((form) => normalizeAssetName(form.name) === key);
}

/** 引用名的展示形态：衍生显示为 `本体 / 衍生`，本体原样。 */
export function formatReferenceName(name: string): string {
  const [owner, derivative] = splitDerivativeReference(name);
  return derivative ? `${owner} / ${derivative}` : name;
}

/**
 * 无图时占位方块里的首字母：衍生取衍生名。同一角色的多个形态否则是同一个字、同一种底色，
 * 堆在一起分不开。
 */
export function referenceInitial(name: string): string {
  const [owner, derivative] = splitDerivativeReference(name);
  return (derivative || owner).charAt(0);
}
