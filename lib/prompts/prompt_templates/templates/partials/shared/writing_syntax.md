---
protected: true
---
Each video unit’s body is **free-form text**; simply write it line by line.

There are only three tokens, independent of output language, and they may appear anywhere in the body:

1. `@[名称]`: references a registered asset (product / character / scene / prop), indicating that it appears in the frame.
   Copy the name exactly from the candidate list; do not invent names outside it.
   If a character has a **derivative** (another appearance of the same character, written as `base/derivative` in the candidate list), choose one according to the unit’s story state:
   use `@[base/derivative]` when wearing / in that appearance now, and `@[base]` when back to the normal appearance described in the base candidate entry.
2. `@[character name]{dialogue text}`: that character speaks this line. Braces `{}` contain verbatim dialogue; the character name must be a registered
   character asset name. Whitespace or one colon is allowed between `@[名称]` and `{`: `@[Li Ming]{…}`, `@[Li Ming] {…}`,
   and `@[Li Ming]：{…}` are equivalent. The speaker position may also use `@[base/derivative]{dialogue}`: voice remains bound to the base character, appearance uses the derivative.
3. `{dialogue text}`: off-screen speech (no specified speaker). This form has no immediately preceding `@[名称]` before the braces.
   This unassigned narration is not sent to the video model; TTS / post-production voiceover provides it. Write it normally in the body; it simply does not enter visual generation.

Place dialogue tokens immediately after the action they correspond to: at the end of the same line (`@[Li Ming] pushes the door open. @[Li Ming]{I’m here.}`) or on the next line.
A line may contain multiple tokens. Only speaker positions within dialogue tokens are excluded from visual reference images; a character occurring only in speaker positions
provides voice only. `@[名称]` outside a dialogue token enters the visual reference images normally, regardless of whether the same line has dialogue.

Structural markers do not change with output language: `@[]` and braces `{}` are syntax recognized by the parser. Preserve these markers exactly
in projects of every language; only the visual descriptions and dialogue between markers use the project language.

Hard constraints (any violation rejects the whole output):

- Braces are only for dialogue and off-screen speech; they must be paired, closed, non-nested, and nonempty.
  **No stray `{` or `}` in visual descriptions**: braces not recognized as dialogue tokens enter visual descriptions unchanged.
- The body must contain visual descriptions, not only dialogue and off-screen speech.
- All syntax markers use ASCII: paired square brackets with a nonempty name in `@[名称]`, and dialogue braces `{}`, not `｛｝`.
- Every `@[名称]` must be an asset name registered in the candidate list; the same applies to character names in dialogue.
- Do not describe appearance / clothing / furnishings / color and lighting in visual descriptions: reference images supply static appearance.
  Write generic extras (old man A / several villagers) directly in the description, without an `@` reference.

Scene references (assessed for each video unit, not once for the whole output):

- Every video unit’s visual description must `@` reference the scene asset for its location. Without it, the model chooses the location freely,
  and adjacent units alternating indoors and outdoors can diverge and fail to match.
- When adjacent units change location, reference the scene asset for the new location.
- Consecutive units at the same location must **repeat the same scene reference in every unit**, not only the first.
- Use a textual location description only if no scene asset in the candidate list matches that location; in that case,
  keep the same location-description sentence across all units there.

Example (the body of one video unit):

```
@[Li Ming] pushes open the wooden door of @[Tavern] and steps sideways over the threshold. @[Li Ming]{This place is livelier than I expected.}
He walks to the counter, lays @[Longsword] across it, and taps the scabbard twice with his knuckles.
{He knows tonight will not be peaceful.}
```
