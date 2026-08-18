import { useMemo } from "react";
import type { MentionKind } from "@/components/canvas/reference/asset-colors";
import {
  LINE_BREAK_RE,
  MENTION_RE,
  SHOT_HEADER_RE,
  isSpeechMark,
  mentionNameFromMatch,
  normalizeAssetName,
  splitScriptLines,
  splitSpeechLine,
  type MentionLookup,
} from "@/utils/reference-mentions";

/**
 * Shot/@mention tokenizer for the reference-video prompt editor.
 *
 * Regex mirrors lib/reference_video/shot_parser.py:
 * - _SHOT_HEADER_RE: `^镜头\s*\d+\s*[:：]` (per-line; duration lives on the unit, not the header)
 * - _MENTION_RE:     shared via reference-mentions.MENTION_RE
 *
 * Output tokens are non-overlapping and concatenate back to the original text.
 *
 * 匹配跑在原始文本上——token 要逐字拼回原文覆盖在 textarea 上，源文本不能归一——只有
 * `mentionNameFromMatch` 取出的 `name` 是规范形。据此留一处残留：BOM 落在裸提及内部
 * （`@张<U+FEFF>三`）时 `MENTION_RE` 的裸名字符类不含 U+FEFF，高亮只认到 BOM 之前那截、
 * 判它未登记。参考图派生走 `extractMentions`（行已归一）不受影响，两者只在编辑器着色上
 * 不一致；包裹形 `@[名<U+FEFF>称]` 无此残留——BOM 在方括号内，名字整取后再归一。
 */

/**
 * key 一律是归一后的资产名（callers 构建时须先 `normalizeAssetName`）。查询侧不再补归一：
 * mention 名与说话人都出自 `reference-mentions` 的解析原语，已承诺是规范形——两侧不同源，
 * 同一坐标系才能稳定命中。
 */
export type { MentionLookup } from "@/utils/reference-mentions";

/**
 * `speech` 是解析器认出的发声记号（`@[角色]{台词}` / `{台词}`）。`text` 恒为它在原文里
 * 占据的整段原文（含 `@[名称]` 与花括号），token 序列因此仍能逐字拼回原文；`speaker`
 * 为空串即画外音。台词正文不单列一个字段——渲染层要的是原文整段，拆出来无人消费。
 */
export type Token =
  | { kind: "text"; text: string }
  | { kind: "shot_header"; text: string }
  | { kind: "mention"; text: string; name: string; assetKind: MentionKind }
  | { kind: "speech"; text: string; speaker: string; speakerKind: MentionKind };

export function tokenizePrompt(text: string, lookup: MentionLookup): Token[] {
  if (text.length === 0) return [];
  const tokens: Token[] = [];
  // 分隔符随捕获组留在结果里，token 仍可拼回原文；换行集合与后端 splitlines 一致。
  // 捕获组分割的结果是「正文, 分隔符, 正文, …」，奇数位恒为分隔符，按下标判定即可。
  const lines = text.split(LINE_BREAK_RE);

  for (const [i, piece] of lines.entries()) {
    if (i % 2 === 1) {
      tokens.push({ kind: "text", text: piece });
      continue;
    }

    // 后端 `_strip_shot_header` 先 strip 再匹配，缩进的 `  镜头1：` 同样算 header；
    // 缩进单独留成 text token，header 才能既被认出、又拼得回原文。
    const indent = /^\s*/u.exec(piece)?.[0] ?? "";
    const line = piece.slice(indent.length);
    const shotMatch = line.match(SHOT_HEADER_RE);
    if (shotMatch) {
      if (indent.length > 0) {
        tokens.push({ kind: "text", text: indent });
      }
      const header = shotMatch[0];
      tokens.push({ kind: "shot_header", text: header });
      const rest = line.slice(header.length);
      if (rest.length > 0) {
        pushLineTokens(tokens, rest, lookup);
      }
    } else {
      pushLineTokens(tokens, piece, lookup);
    }
  }

  return tokens;
}

/**
 * 一行（或行内一段）的分词：先按发声记号切开，记号成一个 token，其余部分再按 mention 切。
 * 记号整体着色而不再逐个 mention 上色——说话人位不进参考图，与描述里的引用是两回事。
 */
function pushLineTokens(out: Token[], text: string, lookup: MentionLookup): void {
  for (const part of splitSpeechLine(text)) {
    if (!isSpeechMark(part)) {
      pushMentionTokens(out, part, lookup);
      continue;
    }
    out.push({
      kind: "speech",
      text: part.raw,
      speaker: part.speaker,
      speakerKind: speakerKindOf(part.speaker, lookup),
    });
  }
}

/** 说话人只有登记角色才算解析成功——场景 / 道具名占说话人位在后端同样出 warning。 */
function speakerKindOf(speaker: string, lookup: MentionLookup): MentionKind {
  return Object.hasOwn(lookup, speaker) && lookup[speaker] === "character" ? "character" : "unknown";
}

function pushMentionTokens(out: Token[], text: string, lookup: MentionLookup): void {
  let lastIdx = 0;
  for (const m of text.matchAll(MENTION_RE)) {
    const idx = m.index ?? 0;
    if (idx > lastIdx) {
      out.push({ kind: "text", text: text.slice(lastIdx, idx) });
    }
    const name = normalizeAssetName(mentionNameFromMatch(m));
    // hasOwn 而非直接下标：`toString` 等原型链属性是合法资产名，未登记时下标会取到
    // Object.prototype 上的函数并被当成已解析的类型。
    const resolved = Object.hasOwn(lookup, name) ? lookup[name] : undefined;
    out.push({
      kind: "mention",
      text: m[0],
      name,
      // product 参与四 bucket 名称空间归属，但不是 generic reference-video
      // ReferenceResource；继续以 unknown 样式提示它不会进入参考图列表。
      assetKind: resolved === "product" ? "unknown" : (resolved ?? "unknown"),
    });
    lastIdx = idx + m[0].length;
  }
  if (lastIdx < text.length) {
    out.push({ kind: "text", text: text.slice(lastIdx) });
  }
}

/**
 * React hook wrapper around tokenizePrompt. Memoizes by (text, lookup identity).
 * Callers should `useMemo` the lookup object to keep the reference stable.
 */
export function useShotPromptHighlight(text: string, lookup: MentionLookup): Token[] {
  return useMemo(() => tokenizePrompt(text, lookup), [text, lookup]);
}

/**
 * Line-level view of the same script, for the read-only parse preview.
 *
 * `tokenizePrompt` stays character-exact because the editor overlays it on a
 * textarea; this one groups by line so the preview can indent dialogue under its
 * shot and tint the lines the parser actually recognized as utterances.
 *
 * `shotIndex` is 1-based throughout: `parse_prompt` folds any lead-in written before
 * the first `镜头N：` header into shot 1 rather than opening a shot of its own, so
 * those lines carry index 1 here too and the first header does not advance past it.
 *
 * 整行除空白外只有一个发声记号时才独占一条 `dialogue` / `voiceover`；记号与描述混写的行
 * 归 `text`，记号作为 `speech` token 在行内就地着色。缩进与行尾空白不影响这一判定——
 * 后端 `_content_lines` 同样先 strip 再判，缩进写的台词两侧都是台词。
 *
 * `sourceLine` is the 0-based raw line index (`splitScriptLines` order — one entry per
 * physical line), the same coordinate system as the backend's `DraftViolation.line`
 * (`lib/reference_video/draft_validation.py::_content_lines`). A header line written with
 * a lone dialogue on the same physical line yields two `ScriptLine`s sharing one `sourceLine` —
 * callers anchoring a violation to a line should match on `sourceLine`, not array index.
 */
export type ScriptLine =
  | { kind: "shot_header"; shotIndex: number; sourceLine: number; header: string; tokens: Token[] }
  | { kind: "dialogue"; shotIndex: number; sourceLine: number; speaker: string; speakerKind: MentionKind; text: string }
  | { kind: "voiceover"; shotIndex: number; sourceLine: number; text: string }
  | { kind: "text"; shotIndex: number; sourceLine: number; tokens: Token[] };

export function toScriptLines(text: string, lookup: MentionLookup): ScriptLine[] {
  const lines: ScriptLine[] = [];
  let shotIndex = 0;
  let firstHeaderSeen = false;
  for (const [sourceLine, raw] of splitScriptLines(text).entries()) {
    const trimmed = raw.trim();
    const headerMatch = trimmed.match(SHOT_HEADER_RE);
    if (headerMatch) {
      // 首个 header 与它之前的引子同属 shot 1（后端把引子折进第一个镜头），
      // 之后每个 header 各开一镜。
      shotIndex = firstHeaderSeen ? shotIndex + 1 : Math.max(shotIndex, 1);
      firstHeaderSeen = true;
    } else if (shotIndex === 0) {
      shotIndex = 1;
    }
    // 先剥 header 再切记号：`parse_prompt` 切分镜头时也丢掉 header，故
    // `镜头1：@[张三]{我来了}` 在后端是一条台词。不剥就会把它渲染成描述行，
    // 与同屏的服务端派生台词列表自相矛盾。
    const afterHeader = headerMatch ? trimmed.slice(headerMatch[0].length) : null;
    const body = afterHeader ?? trimmed;
    const parts = splitSpeechLine(body);
    // 整行只有一个记号时单独成行，说话人与台词分栏显示；记号与描述混写的行按普通
    // 描述行渲染，记号在行内就地着色，行文顺序因此与作者所写一致。
    // 记号两侧的空白不算「描述」：`  @[张三]：{我来了}  ` 与顶格写的是同一条台词。
    const marks = parts.filter(isSpeechMark);
    const only =
      marks.length === 1 && parts.every((part) => isSpeechMark(part) || part.trim() === "") ? marks[0] : null;

    if (headerMatch) {
      // 整行台词写在 header 行时，header 单独占一行（台词归入下面的 utterance 行），
      // 镜头结构在预览里仍然顶格可见。
      const tokens: Token[] = [];
      if (!only && afterHeader && afterHeader.length > 0) {
        pushLineTokens(tokens, afterHeader, lookup);
      }
      lines.push({ kind: "shot_header", shotIndex, sourceLine, header: headerMatch[0].trim(), tokens });
      if (!only) continue;
    }

    if (only) {
      if (only.speaker) {
        lines.push({
          kind: "dialogue",
          shotIndex,
          sourceLine,
          speaker: only.speaker,
          speakerKind: speakerKindOf(only.speaker, lookup),
          text: only.text,
        });
      } else {
        lines.push({ kind: "voiceover", shotIndex, sourceLine, text: only.text });
      }
      continue;
    }
    const tokens: Token[] = [];
    pushLineTokens(tokens, raw, lookup);
    lines.push({ kind: "text", shotIndex, sourceLine, tokens });
  }
  return lines;
}
