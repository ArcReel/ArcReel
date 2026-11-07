---
name: generate-narration-audio
description: Generate per-shot narration audio (TTS) for narration/commentary scripts. Use to fill missing audio during first automatic editing in TTS projects, for user-requested shot/episode narration generation or regeneration, or to complete an interrupted narration batch.
---

# Generate narration audio

For each narration/commentary shot, synthesize its `novel_text` source into narration audio,
writing `generated_assets.narration_audio` (output `audio/segment_{segment_id}.wav`).
It depends only on scripts, not storyboard images/videos, and can proceed as soon as scripts exist.

## Tool calls

**Important: narration must be enqueued through the MCP tools below. This skill provides no Python/Shell scripts; never call `python .../scripts/*.py` through Bash.**

Enqueue through MCP:

| Operation | Tool |
|------|------|
| Fill episode gaps (default, all shots missing audio) | `mcp__arcreel__generate_narration_audio({"script": "episode_1.json"})` |
| Explicit batch scope | `mcp__arcreel__generate_narration_audio({"script": "episode_1.json", "segment_ids": ["E1S01", "E1S02"]})` |
| Regenerate one shot | `mcp__arcreel__generate_narration_audio({"script": "episode_1.json", "segment_ids": ["E1S05"]})` |

> **Selection**: without `segment_ids`, enqueue only shots missing `narration_audio`; stale but usable old audio is reused, not automatically regenerated.
> Explicitly selected shots are resynthesized even with existing audio, for example after voice/rate changes.
>
> **Timing**: fill missing audio during first automatic editing in TTS projects; TTS selection already authorizes this without further confirmation.
> Users may request generation anytime. Resynthesize stale audio only on request; post-production-voiceover projects do not require TTS.
> Video generation does not depend on narration audio; neither waits for the other.
> Ask the user to audition and confirm newly generated narration.
>
> **Dependencies**: the generation worker must be online (independent audio lane); project narration delivery is TTS. Model, voice, and rate
> come from the project settings’ TTS snapshot, independent of global defaults.
>
> **Change voice/rate**: when users request “voice X / rate 1.2 for this project”, call
> `mcp__arcreel__patch_project({"settings": {"narration_voice": "X", "narration_speed": 1.2}})`
> to change the project TTS snapshot (this project only; `narration_speed` set to `null` omits rate from provider calls). Audio generated using old model/voice/rate
> becomes stale, but remains auditionable and usable in editing. Episode gap-filling does not regenerate it; name those shots explicitly to adopt new settings.

## Workflow

1. **Check state**: read the script, inspect each shot’s `generated_assets.narration_audio`, count missing shots, and inform the user
2. **Enqueue generation**: call MCP; workers process the generation queue and the tool waits for all tasks before returning per-shot results
3. **Report**: summarize successes/failures for the user

## Resume

After interruption (restart, task failure, session disconnection), call **episode gap-filling without `segment_ids`** again:
existing audio is skipped; only missing shots generate, without duplicate charges.

## Errors

- One failed shot does not fail the batch: results are `requested / succeeded / failed / blocked` per ID
- Use each `failed` / `blocked` item’s code and next action to decide retry versus input repair; do not infer from prose
- Retry retryable shots precisely using `segment_ids`
- For `narration_delivery_post_production`, explain that narration is delivered in post-production and video is unaffected;
  if users want in-app narration, ask them to change delivery to TTS in Web project settings, then retry
- For `narration_tts_*` configuration issues, likewise explain post-production voiceover remains available; **continuing video production does not require TTS**.
  Guide users to complete TTS model/voice settings only if they actively want in-app narration
