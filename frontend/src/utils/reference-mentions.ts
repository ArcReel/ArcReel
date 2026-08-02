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

export function mentionNameFromMatch(match: RegExpMatchArray): string {
  return match[1] ?? match[2] ?? "";
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
 * 按后端同一套换行边界切行（不保留分隔符）。
 *
 * 末尾换行不产生空行、空串切出空数组——都与 `splitlines()` 一致；行中间的空行照常保留。
 */
export function splitScriptLines(text: string): string[] {
  if (text.length === 0) return [];
  const lines = text.split(LINE_BREAK_RE).filter((_, i) => i % 2 === 0);
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

/** Bare `{台词}` line = voiceover. Mirrors `shot_parser.py:match_voiceover_line`. */
const VOICEOVER_LINE_RE = /^\s*\{([^{}]*)\}\s*$/;

export function matchDialogueLine(line: string): { speaker: string; text: string } | null {
  const m = DIALOGUE_LINE_RE.exec(line);
  if (!m) return null;
  const speaker = m[1] ?? m[2] ?? "";
  // speaker 位全为空白不算规范行（同 shot_parser.py：dialogue utterance 必须带非空 speaker）。
  if (!speaker.trim() || !hasSpokenText(m[3])) return null;
  return { speaker, text: m[3] };
}

export function matchVoiceoverLine(line: string): string | null {
  const m = VOICEOVER_LINE_RE.exec(line);
  if (!m || !hasSpokenText(m[1])) return null;
  return m[1];
}

/**
 * Mention names in first-appearance order, deduplicated — the reference-image
 * derivation. Mirrors `shot_parser.py:extract_mentions`, including its rule that
 * **normative dialogue lines are skipped entirely**: attaching a reference image to
 * a speaker would coax the model into drawing a character who only speaks off-screen.
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

export function resolveMentionType(
  project: ProjectBuckets | null | undefined,
  name: string,
): AssetKind | undefined {
  if (!project) return undefined;
  // 用 hasOwn 而非 `in`：`toString` / `constructor` / `__proto__` 都是合法资产名
  // （`validate_asset_name` 只挡路径分隔符与 Windows 保留字符），`in` 会命中原型链上的
  // 同名属性，把未登记的名字判成已登记。
  if (project.characters && Object.hasOwn(project.characters, name)) return "character";
  if (project.scenes && Object.hasOwn(project.scenes, name)) return "scene";
  if (project.props && Object.hasOwn(project.props, name)) return "prop";
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
  const mentioned = new Set(extractMentions(prompt));
  const kept: ReferenceResource[] = [];
  const keptNames = new Set<string>();
  for (const ref of existing) {
    if (mentioned.has(ref.name) && !keptNames.has(ref.name)) {
      kept.push(ref);
      keptNames.add(ref.name);
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
