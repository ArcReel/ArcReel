"""Reference-video body parser: recognition primitives for ``@[名称]`` references, dialogue, and narration markers.

Units persist only bodies; this module derives images and speech ownership on read (ADR 0064). Parse results
are never persisted; editing the body changes every derivation.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Collection, Iterable, Iterator
from dataclasses import dataclass

from lib.project.asset_types import asset_name_comparison_key
from lib.references.reference_catalog import (
    ReferenceCatalog,
    build_reference_catalog,
    renamed_reference,
    split_derivative_reference,
)
from lib.script.script_models import ReferenceResource

#: BOM / ZWNBSP. Frontend JS ``\s`` recognizes U+FEFF as leading whitespace; Python ``str.strip()``
#: does not (``"﻿".isspace()`` is False). Without normalization, BOM markers parse differently in frontend/backend,
#: deriving different references for identical bodies.
#: BOMs have no body semantics; remove once at parser entry to align both paths.
_BOM = "﻿"

#: Allowed Chinese/English separator colons between speaker mentions and ``{``. Permit only one:
#: a second is not ``@[角色]：{台词}``; prefer nonrecognition over silently degrading to narration.
_SPEAKER_SEPARATORS = "：:"


def _normalize_source(text: str) -> str:
    """Normalize reference-syntax input: remove all U+FEFF and converge encoding to Unicode NFC.

    Both are invisible byte differences that diverge byte-level decisions, so handle them together. BOMs
    may appear at any pasted line beginning, not just document starts, and divergence is line-based. NFC
    is asset-name comparison space (:func:`lib.project.asset_types.asset_name_comparison_key`): speakers
    and ``@[名称]`` must equal registry keys; NFD bodies and NFC registry entries otherwise look identical
    but compare unequal.

    Normalize at three line primitives (``split_speech_line`` / ``leading_mention_before_colon`` /
    ``find_malformed_mention``), mirroring frontend functions and requiring identical decisions when used alone.
    Name extraction additionally trims through the comparison helper; speaker/mention names are strip + NFC,
    and dialogue text is NFC.
    """
    stripped = text.replace(_BOM, "") if _BOM in text else text
    return unicodedata.normalize("NFC", stripped)


def _is_ascii_word_char(ch: str) -> bool:
    return ch == "_" or (ch.isascii() and ch.isalnum())


def _is_legacy_mention_char(ch: str) -> bool:
    return ch == "_" or (ch.isascii() and ch.isalnum()) or ("\u4e00" <= ch <= "\u9fff")


def _next_positions(text: str, targets: set[str]) -> list[int]:
    next_pos = [len(text)] * (len(text) + 1)
    for i in range(len(text) - 1, -1, -1):
        next_pos[i] = i if text[i] in targets else next_pos[i + 1]
    return next_pos


def _iter_mentions(text: str) -> Iterator[tuple[int, int, str]]:
    """Yield (start, end, name) for @名称 / @[名称] mentions.

    The left side of `@` must not be an ASCII word character, otherwise the text
    is treated as an email/id fragment. Wrapped mentions may contain punctuation
    but cannot cross line breaks. Curly-brace wrapping is intentionally excluded
    because the editor only writes `@[名称]` and the runtime contract stays on a
    single wrapped form.
    """
    next_square = _next_positions(text, {"]"})
    next_line_break = _next_positions(text, {"\r", "\n"})
    i = 0
    while i < len(text):
        if text[i] != "@":
            i += 1
            continue

        if i > 0 and _is_ascii_word_char(text[i - 1]):
            i += 1
            continue

        if i + 1 >= len(text):
            i += 1
            continue

        opener = text[i + 1]
        if opener == "[":
            start = i + 2
            close = next_square[start]
            if start < close < next_line_break[start]:
                yield i, close + 1, text[start:close]
                i = close + 1
                continue
            i += 1
            continue

        j = i + 1
        while j < len(text) and _is_legacy_mention_char(text[j]):
            j += 1
        if j > i + 1:
            yield i, j, text[i + 1 : j]
            i = j
            continue
        i += 1


@dataclass(frozen=True)
class SpeechMark:
    """One parsed inline speech marker.

    Empty ``speaker`` means narration (``{台词}``); nonempty means character speech (``@[角色]{台词}``).
    Names use normalized asset comparison space; ``text`` is verbatim dialogue inside braces.

    ``raw`` is the complete original span in the normalized line (including speaker mention and following
    whitespace/colon), enabling lossless unregistered-speaker rendering without slicing original offsets again.

    For ``@[角色/衍生]``, ``speaker`` stays the base name and ``derivative`` records the derivative
    (``docs/adr/0072``). Voices belong to identities; derivatives share base audio/voice style.
    :attr:`speaker_reference` carries written appearance for registration checks and cascade renaming.
    """

    speaker: str
    text: str
    raw: str
    #: Written speaker derivative; empty for bare base names and narration.
    derivative: str = ""

    @property
    def speaker_reference(self) -> str:
        """Written speaker reference: base name or ``本体名/衍生名``; empty for narration."""
        return f"{self.speaker}/{self.derivative}" if self.derivative else self.speaker


def split_speech_line(line: str) -> list[str | SpeechMark]:
    """Split a line into ordered visual-description strings and speech markers, appearing anywhere inline.

    Two language-independent markers: ``{台词}`` is narration; ``{台词}`` immediately following ``@[角色]``
    with optional whitespace or one Chinese/English colon is character speech. Standalone
    ``@[角色]：{台词}`` is a valid special case.

    Recognize only the mention immediately before braces, never guess the nearest inline mention as
    speaker: mistakes silently bind dialogue to another character's reference audio. The following never
    form markers; retain braces in descriptions for caller-specific warnings or violations:

    - Empty dialogue (``{}`` / ``{   }``): ``Utterance`` requires nonempty text; acceptance would derive empty speech.
    - Blank speaker (``@[ ]{台词}``) or derivative-only (``@[/劲装]{台词}``): dialogue requires a nonempty
      speaker; without a base name, there is no identity to bind audio to.
    - Malformed speakers (``@[]{台词}``, ``@[李明{台词}``, ``@[张三]：：{台词}``): a preceding ``]``
      or extra colon without a usable mention never degrades to narration. The author intended a speaker;
      silent conversion to narration is harder to detect than an unrecognized marker.

    Descriptions plus each marker's ``raw`` concatenate verbatim to the normalized source line; ``strip_speech_marks``
    is the other half of lossless splitting and never swallows text.
    """
    text = _normalize_source(line)
    mentions = list(_iter_mentions(text))
    parts: list[str | SpeechMark] = []
    cursor = 0
    scan = 0
    while True:
        open_index = text.find("{", scan)
        if open_index < 0:
            break
        close_index = text.find("}", open_index + 1)
        if close_index < 0:
            break
        inner = text[open_index + 1 : close_index]
        if "{" in inner:
            # Nested/unclosed braces: outer ``{`` forms no marker; scan again from inside.
            scan = open_index + 1
            continue
        if not inner.strip():
            scan = close_index + 1
            continue

        head = open_index
        while head > cursor and text[head - 1].isspace():
            head -= 1
        separator_colon = False
        if head > cursor and text[head - 1] in _SPEAKER_SEPARATORS:
            head -= 1
            separator_colon = True
            while head > cursor and text[head - 1].isspace():
                head -= 1

        speaker = ""
        derivative = ""
        start = open_index
        mention = next((m for m in mentions if m[1] == head and m[0] >= cursor), None)
        if mention is not None:
            speaker, derivative = split_derivative_reference(asset_name_comparison_key(mention[2]))
            if not speaker:
                scan = close_index + 1
                continue
            start = mention[0]
        elif head > cursor and (text[head - 1] == "]" or (separator_colon and text[head - 1] in _SPEAKER_SEPARATORS)):
            scan = close_index + 1
            continue

        if start > cursor:
            parts.append(text[cursor:start])
        parts.append(SpeechMark(speaker=speaker, text=inner, raw=text[start : close_index + 1], derivative=derivative))
        cursor = close_index + 1
        scan = cursor

    if cursor < len(text):
        parts.append(text[cursor:])
    return parts


def line_speech_marks(line: str) -> list[SpeechMark]:
    """Speech markers on a line in occurrence order."""
    return [part for part in split_speech_line(line) if isinstance(part, SpeechMark)]


def speech_line_description(parts: Iterable[str | SpeechMark]) -> str:
    """Residual nonmarker text in ``split_speech_line``, representing that line's visuals.

    Centralize rather than join independently: previews, speech admission, and draft validation needing both
    markers and descriptions should split once and take each part, avoiding three divergent definitions
    of description as marker syntax evolves.
    """
    return "".join(part for part in parts if isinstance(part, str))


def strip_speech_marks(line: str) -> str:
    """Normalized visual text after removing all speech markers.

    Both reference derivation and artifact basis use this: characters appearing only before dialogue braces
    bind audio without images, while same-line ``@[名称]`` outside markers still derives references.
    """
    return speech_line_description(split_speech_line(line))


def leading_mention_before_colon(line: str) -> str | None:
    """Return the name for leading ``@[名称]：``, otherwise ``None``; inspect names/colons, not braces.

    Difference from strict speech-marker recognition identifies intended but malformed dialogue: missing/
    unmatched braces or blank speakers. Machine validation rejects those instead of treating them as visuals
    (which would derive speaker images while dropping dialogue). Return a name, not bool, because ownership
    matters: scene/prop headings (``@[酒馆]：木门被风吹开``) are valid visuals, not malformed dialogue.
    """
    stripped = _normalize_source(line).strip()
    if not stripped.startswith("@"):
        return None
    first = next(_iter_mentions(stripped), None)
    if first is None or first[0] != 0:
        return None
    rest = stripped[first[1] :].lstrip()
    if not rest or rest[0] not in "：:":
        return None
    return asset_name_comparison_key(first[2])


def find_malformed_mention(line: str) -> str | None:
    """Return the first malformed ``@[`` token (such as ``@[李明`` / ``@[]``), or ``None``.

    ``_iter_mentions`` silently ignores these, omitting references while ``render_mentions_as_subjects``
    retains unknown text in provider requests (it replaces recognized mentions, never deletes). Skip tokens
    preceded by ASCII word characters as email/ID fragments, matching ``_iter_mentions``.

    Fullwidth forms (``＠[李明]`` / ``@［李明］``) are malformed too: models often emit these with Chinese
    input methods, but syntax accepts halfwidth only; silent acceptance likewise loses reference images.
    """
    text = _normalize_source(line)
    for index, char in enumerate(text):
        if char == "＠" or (char == "@" and text[index + 1 : index + 2] == "［"):
            return text[index : index + 20]
    starts = {start for start, _end, _name in _iter_mentions(text)}
    for index in range(len(text) - 1):
        if text[index] != "@" or text[index + 1] != "[" or index in starts:
            continue
        if index > 0 and _is_ascii_word_char(text[index - 1]):
            continue
        return text[index : index + 20]
    return None


def extract_mentions(text: str) -> list[str]:
    """Extract deduplicated ``@`` names in first-mention order.

    Order determines execution reference-image numbers; bodies are authoritative, with no independent reference list to drift.

    **Exclude speaker positions in speech markers**: giving offscreen speakers images encourages rendering them
    on screen. ``@[角色]{台词}`` speakers drive only voice declarations/utterances, not images. Characters
    mentioned only there have no images but retain speech/voice declarations; same-line ``@[名称]``
    outside markers derives images normally.
    """
    seen: set[str] = set()
    result: list[str] = []
    for raw_line in text.splitlines():
        line = strip_speech_marks(raw_line)
        for _start, _end, raw_name in _iter_mentions(line):
            name = asset_name_comparison_key(raw_name)
            if name not in seen:
                seen.add(name)
                result.append(name)
    return result


def mention_names(text: str) -> list[str]:
    """All body ``@`` names, deduplicated in first-occurrence order, **including speech-marker speakers**.

    Unlike :func:`extract_mentions`, which asks which names need images and excludes speakers, this asks
    which reference names were written. Cascade renaming (:func:`rewrite_mentions` rewrites precisely this
    set) and asset/derivative usage detection depend on it.
    """
    seen: set[str] = set()
    result: list[str] = []
    for line in text.splitlines():
        for _start, _end, raw_name in _iter_mentions(_normalize_source(line)):
            name = asset_name_comparison_key(raw_name)
            if name and name not in seen:
                seen.add(name)
                result.append(name)
    return result


def speech_speaker_references(text: str) -> list[str]:
    """Written dialogue-speaker reference names in first-occurrence order, deduplicated; may be ``本体名/衍生名``.

    Complements ``dialogue_speakers`` base names: that asks whose voice binds; this asks which appearance
    the author wrote, determining whether derivatives are registered.
    """
    seen: set[str] = set()
    result: list[str] = []
    for line in text.splitlines():
        for mark in line_speech_marks(line):
            reference = mark.speaker_reference
            if reference and reference not in seen:
                seen.add(reference)
                result.append(reference)
    return result


def rewrite_mentions(text: str, old_name: str, new_name: str) -> tuple[str, int]:
    """Rewrite @ references to *old_name* as ``@[new_name]``, returning ``(新文本, 改写数)``.

    Body primitive for asset renaming; recognize mentions like ``_iter_mentions``, including legacy bare ``@名字``
    upgraded to wrapped form. :func:`lib.references.reference_catalog.renamed_reference` decides matches and
    replacements: character rename cascades ``@[旧角色/衍生]``; derivative rename (*old_name* ``角色/旧衍生``)
    touches only that appearance. NFC comparison also matches NFD-persisted names. Perform no other
    normalization: never strip BOMs or normalize the whole body to NFC. Unmatched characters stay verbatim;
    renaming changes names only, never body encoding. Already-target-form mentions do not count as rewrites.

    Rewrite speaker mentions too: ``@[旧角色/衍生]{台词}`` contains a reference to the renamed identity.
    """
    pieces: list[str] = []
    last = 0
    count = 0
    for start, end, name in _iter_mentions(text):
        renamed = renamed_reference(name, old_name, new_name)
        if renamed is None:
            continue
        replacement = f"@[{renamed}]"
        if text[start:end] == replacement:
            continue
        pieces.append(text[last:start])
        pieces.append(replacement)
        last = end
        count += 1
    if not count:
        return text, 0
    pieces.append(text[last:])
    return "".join(pieces), count


def remap_mentions(text: str, remap: Callable[[str, bool], str | None]) -> str:
    """Rewrite body ``@`` references in one pass through *remap*, returning the new body.

    *remap* receives normalized names and whether they occupy speech-speaker positions, returning complete
    replacements (such as ``@[新名]`` or a plain name), or ``None`` to preserve them. Rewrite multiple names
    together without cascading replacements. Rebuild only changed lines through parser normalization; preserve unchanged lines verbatim.
    """
    pieces: list[str] = []
    for raw_line in text.splitlines(keepends=True):
        content = raw_line.rstrip("\r\n")
        ending = raw_line[len(content) :]
        rebuilt = _remap_line(content, remap)
        pieces.append((content if rebuilt is None else rebuilt) + ending)
    return "".join(pieces)


def _remap_line(line: str, remap: Callable[[str, bool], str | None]) -> str | None:
    changed = False
    out: list[str] = []
    for part in split_speech_line(line):
        if isinstance(part, SpeechMark):
            raw = part.raw
            first = next(_iter_mentions(raw), None) if part.speaker else None
            replacement = remap(asset_name_comparison_key(first[2]), True) if first and first[0] == 0 else None
            if first is not None and replacement is not None:
                raw = replacement + raw[first[1] :]
                changed = True
            out.append(raw)
            continue
        last = 0
        for start, end, name in _iter_mentions(part):
            replacement = remap(asset_name_comparison_key(name), False)
            if replacement is None:
                continue
            out.append(part[last:start])
            out.append(replacement)
            last = end
            changed = True
        out.append(part[last:])
    return "".join(out) if changed else None


def derive_references_from_text(
    text: str, project: dict, *, catalog: ReferenceCatalog | None = None
) -> tuple[list[ReferenceResource], list[str]]:
    """Sole derivation from reference-syntax bodies to ``(references, missing)``.

    Images are pure nonpersistent derivations. Machine validation (``lib.script.reference_video.draft_validation
    .validate_unit_text``), editor previews, and execution projection share this function. **Strictness** depends
    on author intent: machine outputs reject ``missing`` and capability overflow; human-authored content
    only warns and still generates. **Derivation itself** must match: independent ``extract_mentions`` +
    ``resolve_references`` implementations would let changes such as excluding speech speakers produce different
    ``图N`` numbers in the editor and generation.

    ``catalog`` is already built by callers from the same ``project``; batch callers supply it to avoid rebuilding per unit.
    """
    return resolve_references(extract_mentions(text), project, catalog=catalog)


def render_mentions(text: str, render: Callable[[str], str]) -> str:
    """Replace each body ``@[X]`` / ``@X`` in place with ``render(X)``.

    Normalize names to comparison space before ``render``, so NFD ``@[名称]`` matches NFC registration.
    Never change any bytes outside mentions; return unmarked bodies and unparseable malformed markers
    unchanged. Storyboard/grid routes replace ``@[名称]`` with image numbers;
    :func:`render_mentions_as_subjects` produces reference-video subject markers.
    """
    if "@" not in text:
        return text
    parts: list[str] = []
    last = 0
    for start, end, name in _iter_mentions(text):
        parts.append(text[last:start])
        parts.append(render(asset_name_comparison_key(name)))
        last = end
    if last == 0:
        return text
    parts.append(text[last:])
    return "".join(parts)


def render_matched_mentions(text: str, render: Callable[[str], str | None]) -> str:
    """Like :func:`render_mentions`, but ``render`` returning ``None`` retains the mention, including ``@`` and brackets."""
    if "@" not in text:
        return text
    parts: list[str] = []
    last = 0
    for start, end, name in _iter_mentions(text):
        replacement = render(asset_name_comparison_key(name))
        if replacement is None:
            continue
        parts.append(text[last:start])
        parts.append(replacement)
        last = end
    if last == 0:
        return text
    parts.append(text[last:])
    return "".join(parts)


def render_mentions_as_subjects(text: str, names: Collection[str]) -> str:
    """Replace prompt ``@[X]`` / ``@X`` with three-part subject markers ``<X>``.

    ``names`` contains registered assets. ``<X>`` means visual subject, not image number, so capability
    clamping never narrows this set. Retain unknown mentions unchanged without deleting text; thus empty
    joined/trimmed input equals empty rendering, enabling lossless enqueue-side blank-prompt checks.

    Normalize both bodies and ``names`` before membership checks. Otherwise NFD ``@[名称]`` fails to
    match NFC registration, retaining the supposedly unregistered ``@[名称]`` marker directly in provider requests.
    """
    normalized_names = {asset_name_comparison_key(name) for name in names}
    text = _normalize_source(text)
    parts: list[str] = []
    last = 0
    for start, end, name in _iter_mentions(text):
        parts.append(text[last:start])
        canonical = asset_name_comparison_key(name)
        parts.append(f"<{canonical}>" if canonical in normalized_names else text[start:end])
        last = end

    parts.append(text[last:])
    return "".join(parts)


def resolve_references(
    names: list[str],
    project: dict,
    *,
    catalog: ReferenceCatalog | None = None,
) -> tuple[list[ReferenceResource], list[str]]:
    """Dispatch mention names into ReferenceResource through the reference catalog.

    :func:`lib.references.reference_catalog.build_reference_catalog` defines registration/ownership, including
    legacy duplicate names predating shared namespaces (product→character→scene→prop). Only order and deduplication live here.

    ``ReferenceResource.name`` and ``missing`` always use asset comparison space
    (:func:`lib.project.asset_types.asset_name_comparison_key`). Downstream asset lookups, speaker equality,
    and body ``<X>`` replacements must match this normalization or registered names become unfindable.
    Input ``names`` usually comes normalized from this parser; idempotent normalization additionally covers
    callers supplying external names directly.

    Returns:
        (refs, missing): refs preserve input order; missing contains names absent from the catalog.
    """
    if catalog is None:
        catalog = build_reference_catalog(project)
    refs: list[ReferenceResource] = []
    missing: list[str] = []
    seen: set[str] = set()
    for raw_name in names:
        name = asset_name_comparison_key(raw_name)
        if name in seen:
            continue
        seen.add(name)
        entry = catalog.resolve(name)
        if entry is not None:
            refs.append(ReferenceResource(type=entry.asset_type, name=name))  # type: ignore[arg-type]  # Catalog asset_type comes from ASSET_SPECS keys, always one of four asset types, but is carried as str
        else:
            missing.append(name)
    return refs, missing
