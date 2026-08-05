import type { ProjectData } from "@/types";
import type { AssetKind, ReferenceResource } from "@/types/reference-video";

/**
 * Mention regex shared across frontend tokenizers. Mirrors backend
 * `lib/reference_video/shot_parser.py` mention scanner — keep in sync.
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
 * only emits `@[名称]`, and narrowing the parser avoids carrying an unused
 * alternate syntax through highlight / merge / backend replacement paths.
 */
export const MENTION_RE = /(?<!\w)@(?:\[([^\]\r\n]+)\]|([\w\u4e00-\u9fff]+))/g;

/**
 * BOM / ZWNBSP。镜像后端 `shot_parser._BOM`：正文里它没有语义，却让按字节走的判定分叉
 * ——JS 的 `\s` 认它、Python 的 `str.strip()` 不认，带 BOM 的行在前端算规范台词行、在
 * 后端算描述行，说话人是否进参考图两侧结论相反。
 */
const BOM_RE = /\uFEFF/gu;

/**
 * 书写层文本的入口归一：去掉全部 U+FEFF，并把编码形式收敛到 Unicode NFC。镜像后端
 * `lib/reference_video/shot_parser.py::_normalize_source`——两条派生路径同口径。
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

/**
 * Normative dialogue line — `@[角色]：{台词}` alone on a line (either colon).
 * Mirrors `lib/reference_video/shot_parser.py:match_dialogue_line`; keep in sync.
 */
const DIALOGUE_LINE_RE = /^\s*@(?:\[([^\]\r\n]+)\]|([\w一-鿿]+))\s*[:：]\s*\{([^{}]*)\}\s*$/;

/** 空台词（`{}` / `{   }`）不算规范行——同后端：utterance 的 text 必须非空。 */
function hasSpokenText(text: string): boolean {
  return text.trim().length > 0;
}

/**
 * Python `str.splitlines()` 的换行集合——后端 `shot_parser` / `script_preview` 都用它切行。
 * 只按 `\n` 切会把 U+2028 之后的规范台词行与上一行粘在一起（粘贴、agent 产出的文本里会出现），
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
 * Leading `镜头N：` header. Stripped before the normative-line test so a dialogue
 * written on the header line is judged the way the backend judges it — `parse_prompt`
 * drops the header when it splits shots, so such a line is a normative line in the
 * shot text and must not leave a reference-image entry behind.
 * Mirrors `shot_parser.py:_strip_shot_header`（其 `line.strip()` 对应此处的前导 `\s*`）。
 *
 * 序号用 `\p{Nd}` 而非 `\d`：Python 的 `\d` 是 Unicode-aware，`镜头１：`（全角数字）后端
 * 照样剥 header 并按台词派生；JS 的 `\d` 只认 ASCII，用它会让这行在前端留下说话人的参考图。
 */
export const SHOT_HEADER_PREFIX_RE = /^\s*镜头\s*\p{Nd}+\s*[:：]\s*/u;

/** 同上，但不吃行首空白——高亮分词按原样切 token，缩进须留在 text token 里才能拼回原文。 */
export const SHOT_HEADER_RE = /^镜头\s*\p{Nd}+\s*[:：]\s*/u;

/**
 * 结构化 `shots[]` 拼回可显示脚本时的 header 写法（`shotIndex` 1-based）。与上面两个 RE
 * 同处，免得 header 语法在识别侧与生成侧各写一份而漂移。这是文稿语法而非界面文案，不 i18n。
 */
export function formatShotHeader(shotIndex: number): string {
  return `镜头${shotIndex}：`;
}

/** Bare `{台词}` line = voiceover. Mirrors `shot_parser.py:match_voiceover_line`. */
const VOICEOVER_LINE_RE = /^\s*\{([^{}]*)\}\s*$/;

export function matchDialogueLine(line: string): { speaker: string; text: string } | null {
  const m = DIALOGUE_LINE_RE.exec(normalizeSource(line));
  if (!m) return null;
  const speaker = m[1] ?? m[2] ?? "";
  // speaker 位全为空白不算规范行（同 shot_parser.py：dialogue utterance 必须带非空 speaker）。
  if (!speaker.trim() || !hasSpokenText(m[3])) return null;
  return { speaker, text: m[3] };
}

export function matchVoiceoverLine(line: string): string | null {
  const m = VOICEOVER_LINE_RE.exec(normalizeSource(line));
  if (!m || !hasSpokenText(m[1])) return null;
  return m[1];
}

/**
 * Mention names in first-appearance order, deduplicated — the reference-image
 * derivation. Mirrors `shot_parser.py:extract_mentions`, including its rule that
 * **normative dialogue lines are skipped entirely**: attaching a reference image to
 * a speaker would coax the model into drawing a character who only speaks off-screen.
 *
 * 名字一律是规范形，去重也按规范形——调用方直接拿去与已归一的资产表 key 判等，不再补归一。
 */
export function extractMentions(text: string): string[] {
  const seen = new Set<string>();
  const out: string[] = [];
  for (const line of splitScriptLines(text)) {
    if (matchDialogueLine(line.replace(SHOT_HEADER_PREFIX_RE, ""))) continue;
    for (const m of line.matchAll(MENTION_RE)) {
      const name = mentionNameFromMatch(m);
      if (!seen.has(name)) {
        seen.add(name);
        out.push(name);
      }
    }
  }
  return out;
}

type ProjectBuckets = Pick<ProjectData, "characters" | "scenes" | "props">;

/**
 * 把资产名归一到比对坐标系（Unicode NFC）。镜像后端
 * `lib.asset_types.normalize_asset_name`——两侧必须同一坐标系，否则「后端判已登记、
 * 前端判未登记」（反之亦然），组合字符名（如越南语）在这两侧各自输入法/来源下
 * 尤其容易产出不同编码形式。
 */
export function normalizeAssetName(name: string): string {
  return name.normalize("NFC");
}

function bucketHasName(bucket: Record<string, unknown> | undefined, target: string): boolean {
  if (!bucket) return false;
  // Object.keys 而非 `in`：`toString` / `constructor` / `__proto__` 都是合法资产名
  // （`validate_asset_name` 只挡路径分隔符与 Windows 保留字符），`in` 会命中原型链上的
  // 同名属性，把未登记的名字判成已登记；Object.keys 只返回自有可枚举属性，同样安全。
  return Object.keys(bucket).some((key) => normalizeAssetName(key) === target);
}

export function resolveMentionType(
  project: ProjectBuckets | null | undefined,
  name: string,
): AssetKind | undefined {
  if (!project) return undefined;
  const target = normalizeAssetName(name);
  if (bucketHasName(project.characters, target)) return "character";
  if (bucketHasName(project.scenes, target)) return "scene";
  if (bucketHasName(project.props, target)) return "prop";
  return undefined;
}

/**
 * Re-derive the references list for a unit given new prompt text.
 *
 * Rules:
 *  1. Preserve the order of `existing` entries whose names still appear in prompt.
 *  2. Drop entries whose names no longer appear.
 *  3. Append new mentions (in first-appearance order) that resolve to a known bucket.
 *  4. Skip unknown mentions (they become UI warning chips, not references).
 *  5. Deduplicate by name.
 */
export function mergeReferences(
  prompt: string,
  existing: ReferenceResource[],
  project: ProjectBuckets | null | undefined,
): ReferenceResource[] {
  // mention 名出自解析器、已是规范形；既有 references 出自后端落盘值，来源不同故仍需归一后
  // 再判等/去重。输出的 name 一律是规范形，与后端 `resolve_references` 的产出口径一致。
  const mentioned = new Set(extractMentions(prompt));
  const kept: ReferenceResource[] = [];
  const keptNames = new Set<string>();
  for (const ref of existing) {
    const name = normalizeAssetName(ref.name);
    if (mentioned.has(name) && !keptNames.has(name)) {
      kept.push({ ...ref, name });
      keptNames.add(name);
    }
  }
  for (const name of mentioned) {
    if (keptNames.has(name)) continue;
    const type = resolveMentionType(project, name);
    if (!type) continue;
    kept.push({ type, name });
    keptNames.add(name);
  }
  return kept;
}
