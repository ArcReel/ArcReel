# Unattributed narration does not use video-model audio or prompt voice constraints

Narration has only post-production dubbing and current-TTS delivery. Do not add a third option, referencing character voice cards for video-model narration from reference audio. Unattributed narration (bare `{台词}` voiceover in the body, called narrative narration below) also stays out of video prompts as a voice constraint.

## Why this is out of scope

Speech composition (Spec #1740; ownership mechanically derived by `lib/speech/speech_composition`; batch rejection of mixed speech in `docs/adr/0061`; dialogue→video and narration→TTS routing in `docs/adr/0040`) preserves: **character speech uses native video audio; non-character narrative narration uses separate TTS**. Units mixing them are rejected. Narration must invalidate, regenerate, and layer independently: text/voice edits redo only TTS without paying for video; real TTS duration also selects requested video tiers. Video-model narration makes each narration edit regenerate video, with widely varying voice-reproduction capabilities and worse final consistency.

Most actual needs are already covered:

- **The narration is really a character speaking** (offscreen explanation, inner monologue): use character speech, `@[角色]{台词}` in reference-video bodies, or speaker-tagged speech entries in drama storyboard-to-video. Models supporting native audio references reproduce that character’s voice (Spec #1486); others use soft `voice_style` prompt constraints. #2180 will make prompt constraints the project default and audio references optional enhancement, without adding narration to video prompts.
- **The narrator needs a fixed voice**: TTS already has one (project `narration_voice`, falling back globally when absent), naturally consistent across segments.

## Revisit conditions

- TTS backends broadly support reference-audio voice cloning; consider narrator voice cards on the TTS side, still outside video models;
- Character offscreen speech cannot be expressed with `@[角色]{台词}` or speaker-tagged speech entries.

## Prior requests

- #2164 第 3 条 — 旁白交付增加「@角色集下面的角色声音卡」 (item 3: add character voice cards under the character set to narration delivery)
