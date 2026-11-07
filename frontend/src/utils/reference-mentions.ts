import type { Character, CharacterDerivative, ProjectData } from "@/types";
import type { AssetKind } from "@/types/reference-video";

/**
 * Mention regex shared across frontend tokenizers. Mirrors backend
 * `lib/script/reference_video/text_parser.py` mention scanner; keep in sync.
 *
 * Different frontend/backend literals have equivalent semantics:
 * - JS `\w` is always ASCII-only; `(?<!\w)` directly means no ASCII word character on the left.
 * - Python `\w` defaults to Unicode-aware (Chinese belongs to `\w`), so backends explicitly use
 *   `[A-Za-z0-9_]` to avoid rejecting Chinese prefixes like `你好@张三`.
 *
 * CJK (`\u4e00-\u9fff`) is outside word-character sets on both sides, permitting Chinese prefixes.
 *
 * Supports legacy `@名称` plus wrapped `@[名称]` for asset names
 * containing punctuation, spaces, or parentheses.
 *
 * Curly-brace wrapping (`@{名称}`) is intentionally unsupported: the editor
 * only emits `@[名称]`, and braces are the speech syntax (see {@link splitSpeechLine}).
 */
export const MENTION_RE = /(?<!\w)@(?:\[([^\]\r\n]+)\]|([\w\u4e00-\u9fff]+))/g;

/**
 * BOM/ZWNBSP mirrors backend `text_parser._BOM`: meaningless in bodies, yet splits byte-based decisions;
 * JS `\s` recognizes it while Python `str.strip()` does not, making frontend tokens fail backend
 * recognition and reversing whether speakers become reference images.
 */
const BOM_RE = /\uFEFF/gu;

/**
 * Normalize reference-syntax inputs: remove every U+FEFF and normalize encoding to Unicode NFC, matching backend
 * `lib/script/reference_video/text_parser.py::_normalize_source` so both derivation paths agree.
 *
 * Both are invisible byte differences splitting byte-based decisions, therefore handled at one entry point.
 * BOM occurs beyond document starts: pasted concatenations put it at arbitrary line starts, with line-local divergence. NFC defines asset-name
 * comparison coordinates (see {@link normalizeAssetName}); NFD bodies against NFC registries can look identical
 * while comparing unequal.
 *
 * Normalize parser inputs, never just extracted results: BOM inside names (`@[名<U+FEFF>称]`) cannot be fixed per-name afterward,
 * since matching already used BOM-containing bytes.
 */
function normalizeSource(text: string): string {
  return text.replace(BOM_RE, "").normalize("NFC");
}

/**
 * Extract matched mention names, normalizing here rather than in callers: highlighting tokenizers (`tokenizePrompt`) must
 * reconstruct source character by character without source normalization, so only name extraction can return canonical forms.
 */
export function mentionNameFromMatch(match: RegExpMatchArray): string {
  return normalizeSource(match[1] ?? match[2] ?? "");
}

/** Empty dialogue (`{}`/`{   }`) forms no speech tokens, matching backend nonempty utterance text. */
function hasSpokenText(text: string): boolean {
  return text.trim().length > 0;
}

/**
 * Character separating bases/derivatives in reference names, mirroring backend `lib/references/reference_catalog.py::DERIVATIVE_SEPARATOR`.
 * Invalid within both asset/derivative names, making `本体名/衍生名` splitting unambiguous.
 */
const DERIVATIVE_SEPARATOR = "/";

/**
 * Split references into `[本体名, 衍生名]`; nonderivative names return empty derivative names. Mirrors backend
 * `lib/references/reference_catalog.py::split_derivative_reference`.
 */
export function splitDerivativeReference(name: string): [string, string] {
  const at = name.indexOf(DERIVATIVE_SEPARATOR);
  return at < 0 ? [name, ""] : [name.slice(0, at), name.slice(at + 1)];
}

/**
 * Inline speech tokens; empty `speaker` means voiceover. `raw` preserves entire original spans.
 *
 * `@[角色/衍生]` speakers retain base names in `speaker` and derivative names in `derivative`: voices belong to
 * identities, with derivatives sharing base reference audio/timbre (backend `text_parser.SpeechMark`).
 */
export interface SpeechMark {
  speaker: string;
  text: string;
  raw: string;
  derivative: string;
}

/**
 * Inline whitespace allowed between mentions/`{`. JS `\s` excludes U+001F while Python `str.isspace()` includes it;
 * omitting it makes `@[张三]<U+001F>{我来了}` bind speakers on backends but derive reference images on frontends.
 */
// eslint-disable-next-line no-control-regex -- Explicit U+001F matching aligns Python str.isspace()
const INLINE_SPACE_RE = /[\s\x1f]/u;

/** Single separator colons, Chinese or English, permitted between speaker mentions/`{`. */
const SPEAKER_SEPARATORS = "：:";

/** `splitSpeechLine` parts: strings describe visuals, objects represent speech tokens. */
export type SpeechPart = string | SpeechMark;

export function isSpeechMark(part: SpeechPart): part is SpeechMark {
  return typeof part !== "string";
}

/**
 * Split lines into ordered visual-description fragments/speech tokens, mirroring backend
 * `lib/script/reference_video/text_parser.py::split_speech_line` across derivation paths;
 * changing either requires updating the other.
 *
 * Tokens appear anywhere inline: `{台词}` is voiceover; following `@[角色]` (with whitespace or one
 * Chinese/English colon), `{台词}` is that character speaking. Empty dialogue, whitespace-only speakers and malformed speakers
 * (`@[]{…}`, `@[张三]：：{…}`) never form tokens; braces remain descriptions for caller warnings.
 *
 * Deliberate backend difference: **never normalize source text**, only extracted speakers. Highlight tokenizers must reconstruct sources
 * character by character over textareas; normalization would prevent reconstruction. Decision paths (`extractMentions`/
 * `toScriptLines`) receive already normalized `splitScriptLines` inputs, so both sides still agree.
 *
 * Mention/`{` whitespace uses `INLINE_SPACE_RE`: JS `\s` lacks Python `str.isspace()`
 * U+001F (coarser U+001C–U+001E/U+0085 become line breaks through `LINE_BREAK_RE`, never inline;
 * backends remove U+FEFF at normalization, while `\s` matches it here). Added classes make both sets character-identical.
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
      // Nested/unclosed braces: outer `{` forms no token; resume scanning inside.
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
      // Malformed speakers (`@[]{…}`,`@[张三]：：{…}`) never silently degrade to voiceover.
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

/** Speech tokens in first-appearance order within lines. */
export function lineSpeechMarks(line: string): SpeechMark[] {
  return splitSpeechLine(line).filter(isSpeechMark);
}

/** Visual descriptions after removing all speech tokens; reference-image derivation uses these. */
export function stripSpeechMarks(line: string): string {
  return splitSpeechLine(line)
    .filter((part): part is string => !isSpeechMark(part))
    .join("");
}

/**
 * Python `str.splitlines()` line-break sets, used by backend `text_parser`/`script_preview`.
 * Splitting only on `\n` joins dialogue after U+2028 to previous lines (pasted/agent-generated text can contain this),
 * making frontends include speakers as image references while backends exclude them, diverging derivation immediately.
 * Capturing groups retain delimiters unchanged in `split` results, preserving source reconstruction from tokens.
 *
 * The control characters in the class are deliberate: Python counts the file and
 * group separators as line breaks too, and dropping one reintroduces exactly the
 * front/back divergence this constant exists to remove.
 */
// eslint-disable-next-line no-control-regex -- Explicit U+001C–U+001E matching aligns Python str.splitlines()
export const LINE_BREAK_RE = /(\r\n|[\n\r\v\f\x1c\x1d\x1e\x85\u2028\u2029])/;

/**
 * Split on backend-identical boundaries without retaining delimiters; line contents are canonical (`normalizeSource`).
 *
 * Final newlines add no empty lines; empty strings yield empty arrays, matching `splitlines()`; interior empty lines remain.
 * Normalization never adds/removes breaks, so counts/indexes (`toScriptLines` `sourceLine`) correspond linewise with originals.
 */
export function splitScriptLines(text: string): string[] {
  if (text.length === 0) return [];
  const lines = normalizeSource(text).split(LINE_BREAK_RE).filter((_, i) => i % 2 === 0);
  if (lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/**
 * Mention names in first-appearance order, deduplicated: reference-image
 * derivation. Mirrors `text_parser.py:extract_mentions`, including its rule that
 * **the speaker slot of a speech mark is excluded**: attaching a reference image to
 * a speaker would coax the model into drawing a character who only speaks off-screen.
 * Non-token `@[名称]` still enters image references, coexisting on the same lines.
 *
 * Names/deduplication are canonical; callers compare directly against normalized asset-table keys without further normalization.
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
 * Dialogue-token speakers deduplicated by appearance, mirroring backend
 * `lib/script/reference_video/draft_validation.py::dialogue_speakers`. Speaker slots alone determine
 * who speaks in units, complementing `extractMentions` visual references: characters may speak without appearing, or appear
 * without speaking; timbre-related decisions always use this function.
 *
 * Names from `splitSpeechLine` already use asset comparison coordinates, compared directly against normalized asset keys.
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
// eslint-disable-next-line no-control-regex -- Explicitly match control characters in Python str.strip() whitespace sets
const PYTHON_STRIP_RE = /^[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+|[\u0009-\u000d\u001c-\u0020\u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+$/gu;

/**
 * Normalize asset names into project-namespace comparison coordinates (strip+Unicode NFC), mirroring backend
 * `lib.project.asset_types.asset_name_comparison_key`. Both sides require identical coordinates, otherwise backend Registered/frontend
 * Unregistered (or vice versa) diverge. Combined-character names (e.g. Vietnamese) particularly easily acquire
 * differing encodings through respective input methods/sources.
 */
export function normalizeAssetName(name: string): string {
  return name.replace(PYTHON_STRIP_RE, "").normalize("NFC");
}

/**
 * Unique project-reference-name/type maps for editor highlights, mirroring backend
 * `lib/references/reference_catalog.py::build_reference_catalog` names: asset names plus character
 * `本体名/衍生名` (derivatives share base types; `docs/adr/0072`).
 *
 * Prototype-free dictionaries allow legal names such as `__proto__` as ordinary keys. Corrupt projects with duplicate names
 * resolve by stable product→character→scene→prop precedence.
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

/** Character forms: bases or owned derivatives; `asset` is each form own entry, supplying its images/descriptions. */
export interface CharacterForm {
  /** Reference names: base names or `本体名/衍生名`, written into `@[…]`/`characters_in_*`. */
  name: string;
  /** Owning character entry names; differ from `name` for derivatives, equal for bases. */
  ownerName: string;
  /** Derivative names; empty for bases. */
  derivativeName: string;
  /** Registered form entries: character entries for bases, `derivatives` entries for derivatives. */
  asset: Character | CharacterDerivative;
}

/**
 * All referenceable project character forms, bases followed immediately by their derivatives. Mirrors backend
 * `lib/references/reference_catalog.py::ReferenceCatalog` character expansion (`docs/adr/0072`):
 * mention candidates/avatar-image stacks/editor highlights source names here, never traversing `derivatives` independently.
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
 * Resolve character forms by reference names; undefined for unregistered names, including existing bases with missing derivatives.
 * Equality uses asset-name comparison coordinates, matching {@link buildMentionLookup}.
 */
export function resolveCharacterForm(
  characters: Record<string, Character> | null | undefined,
  name: string,
): CharacterForm | undefined {
  const key = normalizeAssetName(name);
  return characterReferenceForms(characters).find((form) => normalizeAssetName(form.name) === key);
}

/** Display reference names: derivatives as `本体 / 衍生`, bases unchanged. */
export function formatReferenceName(name: string): string {
  const [owner, derivative] = splitDerivativeReference(name);
  return derivative ? `${owner} / ${derivative}` : name;
}

/**
 * Initials for imageless placeholder squares use derivative names; otherwise multiple same-character forms would share
 * letters/backgrounds, indistinguishable in stacks.
 */
export function referenceInitial(name: string): string {
  const [owner, derivative] = splitDerivativeReference(name);
  return (derivative || owner).charAt(0);
}
