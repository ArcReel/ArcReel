import { useMemo } from "react";
import type { MentionKind } from "@/components/canvas/reference/asset-colors";
import {
  LINE_BREAK_RE,
  MENTION_RE,
  isSpeechMark,
  mentionNameFromMatch,
  normalizeAssetName,
  splitScriptLines,
  splitSpeechLine,
  type MentionLookup,
} from "@/utils/reference-mentions";

/**
 * @mention / speech tokenizer for the reference-video unit body editor.
 *
 * Regex mirrors lib/script/reference_video/text_parser.py via reference-mentions.MENTION_RE.
 *
 * Output tokens are non-overlapping and concatenate back to the original text.
 *
 * Match raw text: tokens must reconstruct exact source over textarea overlays, so never normalize source. Only
 * `name` from `mentionNameFromMatch` is canonical. One residual case remains: BOM inside bare mentions
 * (`@张<U+FEFF>三`) is excluded by `MENTION_RE` bare-name character classes, so highlighting stops before BOM,
 * treating names as unregistered. Reference derivation uses `extractMentions` on normalized lines and is unaffected; only editor coloring
 * differs. Wrapped `@[名<U+FEFF>称]` avoids this: BOM stays inside brackets, whole names normalize after extraction.
 */

/**
 * Keys are always normalized asset names (callers must first `normalizeAssetName`). Queries never normalize again:
 * mention names and speakers originate from `reference-mentions` primitives, already guaranteed canonical. Different sources
 * require identical coordinates for stable matching.
 */
export type { MentionLookup } from "@/utils/reference-mentions";

/**
 * `speech` represents parser-recognized utterance markers (`@[角色]{台词}` / `{台词}`). `text` always contains their entire
 * raw source (including `@[名称]` and braces), so token sequences still reconstruct source exactly; empty `speaker`
 * means voiceover. No separate dialogue-body field: rendering needs entire raw spans, with no consumer for extracted bodies.
 */
export type Token =
  | { kind: "text"; text: string }
  | { kind: "mention"; text: string; name: string; assetKind: MentionKind }
  | { kind: "speech"; text: string; speaker: string; speakerKind: MentionKind };

export function tokenizePrompt(text: string, lookup: MentionLookup): Token[] {
  if (text.length === 0) return [];
  const tokens: Token[] = [];
  // Capture delimiters in split results so tokens still reconstruct sources; newline sets match backend splitlines.
  // Capturing-group splits yield body, delimiter, body, …; odd positions are delimiters, identified by index.
  const lines = text.split(LINE_BREAK_RE);

  for (const [i, piece] of lines.entries()) {
    if (i % 2 === 1) {
      tokens.push({ kind: "text", text: piece });
      continue;
    }
    pushLineTokens(tokens, piece, lookup);
  }

  return tokens;
}

/**
 * Tokenize lines (or line fragments): split speech markers first, each as one token, then tokenize mentions in remaining parts.
 * Color entire markers rather than individual mentions: speaker positions never enter reference images, distinct from descriptive references.
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

/** Only registered characters resolve as speakers; scene/prop speaker names also produce backend warnings. */
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
    // Use hasOwn rather than direct indexing: prototype properties such as `toString` are valid asset names; unregistered direct indexing retrieves
    // Object.prototype functions and misclassifies them as resolved types.
    const resolved = Object.hasOwn(lookup, name) ? lookup[name] : undefined;
    out.push({
      kind: "mention",
      text: m[0],
      name,
      // All four asset types derive references identically (ADR 0064); products use their own palettes like characters/scenes/props.
      // Only missing names receive unknown styles.
      assetKind: resolved ?? "unknown",
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
export function useUnitPromptHighlight(text: string, lookup: MentionLookup): Token[] {
  return useMemo(() => tokenizePrompt(text, lookup), [text, lookup]);
}

/**
 * Line-level view of the same unit body, for the read-only parse preview.
 *
 * `tokenizePrompt` stays character-exact because the editor overlays it on a
 * textarea; this one groups by line so the preview can set apart the lines the
 * parser actually recognized as utterances.
 *
 * Only whole lines containing one speech marker plus whitespace become standalone `dialogue` / `voiceover`; mixed markers/descriptions
 * become `text`, with `speech` tokens colored inline. Indentation/trailing whitespace never change this classification:
 * backend line parsing also strips first, so indented dialogue remains dialogue on both sides.
 *
 * `sourceLine` is the 0-based raw line index (`splitScriptLines` order — one entry per
 * physical line), the same coordinate system as the backend's `DraftViolation.line`
 * (`lib/script/reference_video/draft_validation.py`, `text.splitlines()` coordinates).
 */
export type ScriptLine =
  | { kind: "dialogue"; sourceLine: number; speaker: string; speakerKind: MentionKind; text: string }
  | { kind: "voiceover"; sourceLine: number; text: string }
  | { kind: "text"; sourceLine: number; tokens: Token[] };

export function toScriptLines(text: string, lookup: MentionLookup): ScriptLine[] {
  const lines: ScriptLine[] = [];
  for (const [sourceLine, raw] of splitScriptLines(text).entries()) {
    const parts = splitSpeechLine(raw.trim());
    // Single-marker whole lines stand alone, with speakers/dialogue in separate columns. Mixed markers/descriptions render as ordinary
    // description lines with inline coloring, preserving author-written sequence.
    // Whitespace around markers is not description: `  @[张三]：{我来了}  ` is identical dialogue to unindented forms.
    const marks = parts.filter(isSpeechMark);
    const only =
      marks.length === 1 && parts.every((part) => isSpeechMark(part) || part.trim() === "") ? marks[0] : null;

    if (only) {
      if (only.speaker) {
        lines.push({
          kind: "dialogue",
          sourceLine,
          speaker: only.speaker,
          speakerKind: speakerKindOf(only.speaker, lookup),
          text: only.text,
        });
      } else {
        lines.push({ kind: "voiceover", sourceLine, text: only.text });
      }
      continue;
    }
    const tokens: Token[] = [];
    pushLineTokens(tokens, raw, lookup);
    lines.push({ kind: "text", sourceLine, tokens });
  }
  return lines;
}
