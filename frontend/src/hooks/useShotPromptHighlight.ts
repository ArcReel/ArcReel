import { useMemo } from "react";
import type { MentionKind } from "@/components/canvas/reference/asset-colors";
import {
  LINE_BREAK_RE,
  MENTION_RE,
  SHOT_HEADER_RE,
  matchDialogueLine,
  matchVoiceoverLine,
  mentionNameFromMatch,
  splitScriptLines,
} from "@/utils/reference-mentions";

/**
 * Shot/@mention tokenizer for the reference-video prompt editor.
 *
 * Regex mirrors lib/reference_video/shot_parser.py:
 * - _SHOT_HEADER_RE: `^镜头\s*\d+\s*[:：]` (per-line; duration lives on the unit, not the header)
 * - _MENTION_RE:     shared via reference-mentions.MENTION_RE
 *
 * Output tokens are non-overlapping and concatenate back to the original text.
 */

/**
 * key 一律是归一后的资产名（callers 构建时须先 `normalizeAssetName`）。查询侧不再补归一：
 * mention 名与说话人都出自 `reference-mentions` 的解析原语，已承诺是规范形——两侧不同源，
 * 同一坐标系才能稳定命中。
 */
export type MentionLookup = Record<string, "character" | "scene" | "prop">;

export type Token =
  | { kind: "text"; text: string }
  | { kind: "shot_header"; text: string }
  | { kind: "mention"; text: string; name: string; assetKind: MentionKind };

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
        pushMentionTokens(tokens, rest, lookup);
      }
    } else {
      pushMentionTokens(tokens, piece, lookup);
    }
  }

  return tokens;
}

function pushMentionTokens(out: Token[], text: string, lookup: MentionLookup): void {
  let lastIdx = 0;
  for (const m of text.matchAll(MENTION_RE)) {
    const idx = m.index ?? 0;
    if (idx > lastIdx) {
      out.push({ kind: "text", text: text.slice(lastIdx, idx) });
    }
    const name = mentionNameFromMatch(m);
    // hasOwn 而非直接下标：`toString` 等原型链属性是合法资产名，未登记时下标会取到
    // Object.prototype 上的函数并被当成已解析的类型。
    const resolved = Object.hasOwn(lookup, name) ? lookup[name] : undefined;
    out.push({
      kind: "mention",
      text: m[0],
      name,
      assetKind: (resolved ?? "unknown"),
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
 * `sourceLine` is the 0-based raw line index (`splitScriptLines` order — one entry per
 * physical line), the same coordinate system as the backend's `DraftViolation.line`
 * (`lib/reference_video/draft_validation.py::_content_lines`). A header line written with
 * dialogue on the same physical line yields two `ScriptLine`s sharing one `sourceLine` —
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
    // 先剥 header 再判规范行：`parse_prompt` 切分镜头时也丢掉 header，故
    // `镜头1：@[张三]：{我来了}` 在后端是台词行。不剥就会把它渲染成描述行，
    // 与同屏的服务端派生台词列表自相矛盾。
    const afterHeader = headerMatch ? trimmed.slice(headerMatch[0].length) : null;
    const body = afterHeader ?? raw;
    const dialogue = matchDialogueLine(body);
    const voiceover = dialogue ? null : matchVoiceoverLine(body);
    const isUtterance = dialogue !== null || voiceover !== null;

    if (headerMatch) {
      // 台词写在 header 行时，header 单独占一行（正文归入下面的 utterance 行），
      // 镜头结构在预览里仍然顶格可见。
      const tokens: Token[] = [];
      if (!isUtterance && afterHeader && afterHeader.length > 0) {
        pushMentionTokens(tokens, afterHeader, lookup);
      }
      lines.push({ kind: "shot_header", shotIndex, sourceLine, header: headerMatch[0].trim(), tokens });
      if (!isUtterance) continue;
    }

    if (dialogue) {
      lines.push({
        kind: "dialogue",
        shotIndex,
        sourceLine,
        speaker: dialogue.speaker,
        // Only a registered character can be a speaker — a scene or prop name in the
        // speaker slot reads as unresolved here, matching the backend's warning.
        speakerKind: lookup[dialogue.speaker] === "character" ? "character" : "unknown",
        text: dialogue.text,
      });
      continue;
    }
    if (voiceover !== null) {
      lines.push({ kind: "voiceover", shotIndex, sourceLine, text: voiceover });
      continue;
    }
    const tokens: Token[] = [];
    pushMentionTokens(tokens, raw, lookup);
    lines.push({ kind: "text", shotIndex, sourceLine, tokens });
  }
  return lines;
}
