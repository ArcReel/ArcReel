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

export type { MentionLookup } from "@/utils/reference-mentions";

export type Token =
  | { kind: "text"; text: string }
  | { kind: "mention"; text: string; name: string; assetKind: MentionKind }
  | { kind: "speech"; text: string; speaker: string; speakerKind: MentionKind };

export function tokenizePrompt(text: string, lookup: MentionLookup): Token[] {
  if (text.length === 0) return [];
  const tokens: Token[] = [];
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
    const resolved = Object.hasOwn(lookup, name) ? lookup[name] : undefined;
    out.push({
      kind: "mention",
      text: m[0],
      name,
      assetKind: resolved ?? "unknown",
    });
    lastIdx = idx + m[0].length;
  }
  if (lastIdx < text.length) {
    out.push({ kind: "text", text: text.slice(lastIdx) });
  }
}

export function useUnitPromptHighlight(text: string, lookup: MentionLookup): Token[] {
  return useMemo(() => tokenizePrompt(text, lookup), [text, lookup]);
}

export type ScriptLine =
  | { kind: "dialogue"; sourceLine: number; speaker: string; speakerKind: MentionKind; text: string }
  | { kind: "voiceover"; sourceLine: number; text: string }
  | { kind: "text"; sourceLine: number; tokens: Token[] };

export function toScriptLines(text: string, lookup: MentionLookup): ScriptLine[] {
  const lines: ScriptLine[] = [];
  for (const [sourceLine, raw] of splitScriptLines(text).entries()) {
    const parts = splitSpeechLine(raw.trim());
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
