"""
_text_utils.py - Shared utilities for episode splitting

Provides character counting and character offset conversion functions,
shared by peek_split_point.py and split_episode.py.

Counting rules: includes punctuation, excludes empty lines (pure whitespace lines are not counted).
"""


def count_chars(text: str) -> int:
    """Count effective characters: total characters in all non-empty lines (including punctuation, excluding empty lines)."""
    total = 0
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:  # skip empty lines
            total += len(stripped)
    return total


def find_char_offset(text: str, target_count: int) -> int:
    """Convert an effective character count to an original text character offset position.

    Traverse the original text, skipping characters in empty lines; when the accumulated
    effective character count reaches target_count, return the corresponding original text
    character offset (0-based).

    If target_count exceeds the total effective character count, return the end-of-text offset.
    """
    counted = 0
    lines = text.split("\n")
    pos = 0  # character position in the original text

    for line_idx, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            # empty line: skip the entire line (including newline character)
            pos += len(line)
            if line_idx < len(lines) - 1:
                pos += 1  # newline character
            continue

        # non-empty line: count character by character
        for char_idx, char in enumerate(line):
            if not char.strip():
                # leading/trailing whitespace not counted in effective characters, but advance offset
                pos += 1
                continue
            counted += 1
            if counted >= target_count:
                return pos
            pos += 1

        if line_idx < len(lines) - 1:
            pos += 1  # newline character

    return pos


def find_natural_breakpoints(text: str, center_offset: int, window: int = 200) -> list[dict]:
    """Find natural break points near the specified offset (sentence endings, paragraph boundaries, etc.).

    Returns a list of break points, each containing:
    - offset: original text character offset
    - char: break point character
    - type: break point type (sentence/paragraph)
    - distance: number of characters from center_offset
    """
    start = max(0, center_offset - window)
    end = min(len(text), center_offset + window)

    sentence_endings = {"。", "！", "？", "…"}
    breakpoints = []

    for i in range(start, end):
        ch = text[i]
        if ch == "\n" and i + 1 < len(text) and text[i + 1] == "\n":
            breakpoints.append(
                {
                    "offset": i + 1,
                    "char": "\\n\\n",
                    "type": "paragraph",
                    "distance": abs(i + 1 - center_offset),
                }
            )
        elif ch in sentence_endings:
            breakpoints.append(
                {
                    "offset": i + 1,  # split after the punctuation
                    "char": ch,
                    "type": "sentence",
                    "distance": abs(i + 1 - center_offset),
                }
            )

    # sort by distance
    breakpoints.sort(key=lambda bp: bp["distance"])
    return breakpoints
