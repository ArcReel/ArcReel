import type { Character, CharacterDerivative, ProjectData } from "@/types";
import type { AssetKind } from "@/types/reference-video";

export const MENTION_RE = /(?<!\w)@(?:\[([^\]\r\n]+)\]|([\w\u4e00-\u9fff]+))/g;

const BOM_RE = /\uFEFF/gu;

function normalizeSource(text: string): string {
  return text.replace(BOM_RE, "").normalize("NFC");
}

export function mentionNameFromMatch(match: RegExpMatchArray): string {
  return normalizeSource(match[1] ?? match[2] ?? "");
}

function hasSpokenText(text: string): boolean {
  return text.trim().length > 0;
}

const DERIVATIVE_SEPARATOR = "/";

export function splitDerivativeReference(name: string): [string, string] {
  const at = name.indexOf(DERIVATIVE_SEPARATOR);
  return at < 0 ? [name, ""] : [name.slice(0, at), name.slice(at + 1)];
}

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
// eslint-disable-next-line no-control-regex -- 须显式匹配 U+001F，对齐 Python str.isspace()
const INLINE_SPACE_RE = /[\s\x1f]/u;

const SPEAKER_SEPARATORS = "：:";

export type SpeechPart = string | SpeechMark;

export function isSpeechMark(part: SpeechPart): part is SpeechMark {
  return typeof part !== "string";
}

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

export function lineSpeechMarks(line: string): SpeechMark[] {
  return splitSpeechLine(line).filter(isSpeechMark);
}

export function stripSpeechMarks(line: string): string {
  return splitSpeechLine(line)
    .filter((part): part is string => !isSpeechMark(part))
    .join("");
}

// eslint-disable-next-line no-control-regex -- 须显式匹配 U+001C–U+001E，对齐 Python str.splitlines()
export const LINE_BREAK_RE = /(\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029])/;

export function splitScriptLines(text: string): string[] {
  if (text.length === 0) return [];
  const lines = normalizeSource(text).split(LINE_BREAK_RE).filter((_, i) => i % 2 === 0);
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

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

// eslint-disable-next-line no-control-regex -- 须显式匹配 Python str.strip() 空白集中的控制字符
const PYTHON_STRIP_RE = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu;

export function normalizeAssetName(name: string): string {
  return name.replace(PYTHON_STRIP_RE, "").normalize("NFC");
}

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

export interface CharacterForm {
  name: string;
  ownerName: string;
  derivativeName: string;
  asset: Character | CharacterDerivative;
}

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

export function resolveCharacterForm(
  characters: Record<string, Character> | null | undefined,
  name: string,
): CharacterForm | undefined {
  const key = normalizeAssetName(name);
  return characterReferenceForms(characters).find((form) => normalizeAssetName(form.name) === key);
}

export function formatReferenceName(name: string): string {
  const [owner, derivative] = splitDerivativeReference(name);
  return derivative ? `${owner} / ${derivative}` : name;
}

export function referenceInitial(name: string): string {
  const [owner, derivative] = splitDerivativeReference(name);
  return (derivative || owner).charAt(0);
}
