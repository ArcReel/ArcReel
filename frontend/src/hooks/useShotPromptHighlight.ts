import { useMemo } from "react";
import type { MentionKind } from "@/components/canvas/reference/asset-colors";
import { MENTION_RE } from "@/utils/reference-mentions";

/**
 * Shot/@mention tokenizer for the reference-video prompt editor.
 *
 * Regex mirrors lib/reference_video/shot_parser.py:
 * - _SHOT_HEADER_RE: `^Shot\s+\d+\s*\(\s*(\d+)\s*s\s*\)\s*:` (per-line, case-insensitive)
 * - _MENTION_RE:     shared via reference-mentions.MENTION_RE
 *
 * Output tokens are non-overlapping and concatenate back to the original text.
 */

export type MentionLookup = Record<string, "character" | "scene" | "prop">;

export type Token =
  | { kind: "text"; text: string }
  | { kind: "shot_header"; text: string }
  | { kind: "mention"; text: string; name: string; assetKind: MentionKind };

const SHOT_HEADER_RE = /^Shot\s+\d+\s*\(\s*\d+\s*s\s*\)\s*:\s*/i;

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
    const name = m[1];
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
