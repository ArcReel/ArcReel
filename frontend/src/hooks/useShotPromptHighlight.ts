import { useMemo } from "react";
import type { MentionKind } from "@/components/canvas/reference/asset-colors";
import {
  MENTION_RE,
  matchDialogueLine,
  matchVoiceoverLine,
  mentionNameFromMatch,
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

export type MentionLookup = Record<string, "character" | "scene" | "prop">;

export type Token =
  | { kind: "text"; text: string }
  | { kind: "shot_header"; text: string }
  | { kind: "mention"; text: string; name: string; assetKind: MentionKind };

const SHOT_HEADER_RE = /^镜头\s*\d+\s*[:：]\s*/;

export function tokenizePrompt(text: string, lookup: MentionLookup): Token[] {
  if (text.length === 0) return [];
  const tokens: Token[] = [];
  const lines = text.split(/(\n)/); // keep newlines as separate entries

  for (const piece of lines) {
    if (piece === "\n") {
      tokens.push({ kind: "text", text: "\n" });
      continue;
    }

    const shotMatch = piece.match(SHOT_HEADER_RE);
    if (shotMatch) {
      const header = shotMatch[0];
      tokens.push({ kind: "shot_header", text: header });
      const rest = piece.slice(header.length);
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
    const resolved = lookup[name];
    out.push({
      kind: "mention",
      text: m[0],
      name,
      assetKind: (resolved ?? "unknown") as MentionKind,
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
 * `shotIndex` is 1-based and 0 for anything before the first `镜头N：` header —
 * matching the backend, which folds that lead-in into the first shot's text.
 */
export type ScriptLine =
  | { kind: "shot_header"; shotIndex: number; header: string; tokens: Token[] }
  | { kind: "dialogue"; shotIndex: number; speaker: string; speakerKind: MentionKind; text: string }
  | { kind: "voiceover"; shotIndex: number; text: string }
  | { kind: "text"; shotIndex: number; tokens: Token[] };

export function toScriptLines(text: string, lookup: MentionLookup): ScriptLine[] {
  const lines: ScriptLine[] = [];
  let shotIndex = 0;
  for (const raw of text.split("\n")) {
    const trimmed = raw.trim();
    const headerMatch = trimmed.match(SHOT_HEADER_RE);
    if (headerMatch) shotIndex += 1;
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
      lines.push({ kind: "shot_header", shotIndex, header: headerMatch[0].trim(), tokens });
      if (!isUtterance) continue;
    }

    if (dialogue) {
      lines.push({
        kind: "dialogue",
        shotIndex,
        speaker: dialogue.speaker,
        // Only a registered character can be a speaker — a scene or prop name in the
        // speaker slot reads as unresolved here, matching the backend's warning.
        speakerKind: lookup[dialogue.speaker] === "character" ? "character" : "unknown",
        text: dialogue.text,
      });
      continue;
    }
    if (voiceover !== null) {
      lines.push({ kind: "voiceover", shotIndex, text: voiceover });
      continue;
    }
    const tokens: Token[] = [];
    pushMentionTokens(tokens, raw, lookup);
    lines.push({ kind: "text", shotIndex, tokens });
  }
  return lines;
}
