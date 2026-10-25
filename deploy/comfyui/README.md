# Local LTX adapter

This directory is the source of truth for the Mac adapter and ArcReel endpoint.
Existing standalone render scripts and the ComfyUI node use the same runner.
Video stays local. No models or dependencies are downloaded by installation.

## Install and recover

With the ComfyUI queue idle, run `python deploy/comfyui/install.py --comfy-root /absolute/path/to/ComfyUI`.
The installer verifies the installed CLI revision and memory/preset dependencies,
backs up replaced files, and records installed hashes under `user/local-adapter-install.json`.
Restart idle ComfyUI, validate and import `local-ltx-endpoint.json` into the existing
ArcReel custom endpoint, and set its model duration override to `[2, 4, 5]`.
Restart ArcReel after updating its inference rules. Preserve the old endpoint definition
before importing. To roll back, restore the recorded backup and endpoint, then restart.
Do not install or restart while generation is running.

## Effective configuration

| Control | Configuration and reason |
|---|---|
| Model | Installed LTX 2.3 q8 distilled 1.1, Gemma 3 12B 4-bit; retain the established baseline. |
| Resolution and rate | 1024×576, 24 fps; do not respeed the picture to fit narration. |
| Duration | 2/4/5 seconds become 49/97/121 frames. The extra frame means media duration is 2.042/4.042/5.042 seconds; the timeline must trim deliberately. |
| Start/end image | Start only uses `generate` with frame 0. Supplying an end anchor selects dedicated dev `keyframe --start --end`, strength 1.0, followed by distilled refinement. The installed engine warns against distilled interpolation. Use matching identity, geography, scale, and direction, with a visibly different causal endpoint. Two anchors do not guarantee physically correct motion between them. |
| Seed | Honor a requested unsigned 32-bit seed; generate and retain a seed when absent. A seed is not a character identity lock. |
| Identity | Approved character references must be used to prepare every anchor. This endpoint does not expose independent identity-reference conditioning. |
| Prompt | Preserve all text, including `Avoid:` instructions when no negative input exists. Start-only distilled does not support negative conditioning or CFG/STG tuning. Dedicated keyframe uses its published default guidance; this adapter does not expose additional guidance knobs. |
| Audio | Native audio remains available as a scratch track. Final narration and music use approved continuous timeline stems; a new shot must not replace the speaker or restart the score. |
| Memory | Start/end interpolation requires 19 GiB available, an 8 GiB reserve, and no more than 256 MiB new swap writes, including when Animated dialogue is selected. Start-only Animated dialogue keeps its existing lighter limits. Block streaming, sequential text encoding, decode budget, cancellation and timeout remain in force. |
| A2V, retake, extend, HQ | Installed experimental routes, not silently substituted for distilled. Require their own measured qualification before default use. |
| Motion and identity IC-LoRA | Separate CLI qualification modes described below. The shipped endpoint still does not accept these controls. Installed weights alone do not qualify a route for episode generation. |
| Other pose/depth controls, LipDub, LTX 2.5 | Not qualified by this adapter. Do not advertise these as working local controls. |

Rendering success means a media file was produced. It does not mean character,
physics, sound, or editorial review passed. Use the existing `scripts/refine_episode.py`
recipe for deterministic soundtrack assembly and preserve its creative-review status.

## Verification

`uv run pytest tests/unit/lib/custom_provider/comfyui/test_local_mac_endpoint.py`
exercises the shipped endpoint through the real workflow builder to the actual CLI
arguments. It checks optional anchors, unsigned seeds, frame timing, capability
reporting, prompt preservation, and rejection of controls the adapter cannot use.
Live qualification must also check ComfyUI history, the saved command/report,
decoded frame count/rate, and the resulting footage. Keep test outputs separate
from approved episode assets.

The pinned low-RAM keyframe recipe requires both `transformer-dev.safetensors`
and `transformer-distilled-1.1.safetensors` in the installed pack. At LoRA strength
1.0, its refinement implementation swaps to the pre-fused distilled transformer.
An explicit `--distilled-lora` name enables that path; no separate LoRA file is
read in this configuration. Changing the runtime revision or these flags requires
requalification; do not silently fall back to distilled interpolation.

A matched-input 49-frame qualification removed an unwanted passerby but still
missed the intended action with near-identical guides. That is evidence for
pipeline selection, not a guarantee of action quality. Review every frame and
the moving joins before accepting a take; do not mark a technical success final.

## Isolated motion and identity qualification

The runner accepts `motion-track` and `identity-reference` modes for bounded local
experiments. These modes are not part of the ComfyUI node or ArcReel endpoint
schema. Keep their outputs outside approved episode assets until full motion,
identity, and adjacent-shot reviews pass. `identity-reference` means the requested
conditioning route, not a verified identity guarantee.

In addition to the existing model pack and Gemma encoder, provide the matching
official control LoRA, a control video, and the separate distilled LoRA file:

```json
{
  "name": "motion-qualification-01",
  "mode": "motion-track",
  "width": 1024,
  "height": 576,
  "frames": 49,
  "seed": 12345,
  "prompt": "One natural hand raise while the subject remains standing.",
  "image": "/absolute/path/to/start.png",
  "control_lora": "/absolute/path/to/ltx-2.3-22b-ic-lora-motion-track-control-ref0.5.safetensors",
  "control_video": "/absolute/path/to/motion-tracks.mp4",
  "distilled_lora": "/absolute/path/to/ltx-2.3-22b-distilled-lora-384-1.1.safetensors"
}
```

Use the existing standalone runner entry point with this JSON configuration.
Motion tests require a start image and a correctly timed track video generated
with the official track rasterizer. Identity tests instead use the Ingredients
LoRA and a static reference video; `image` is optional so a test can ask for a new
composition rather than merely preserve its starting frame. Prepare clean
identity references without conflicting character panels. Match control video
dimensions, frame rate, and duration to the output. Motion tests can start short;
Ingredients tests require the trained bucket of 768×448, 121 frames at 24 fps,
with the still reference repeated across the full clip.

Both modes use the dev transformer, control LoRA strength 1.0, distilled LoRA
strength 0.5, and single-stage full-resolution generation. Unlike the keyframe
strength-1.0 shortcut above, these modes **read the separate distilled LoRA file**.
This weight composition follows the official single-stage distilled workflows
at ComfyUI-LTXVideo revision `220e1d45aa0469f518e982102fca4dca92a61862`.
The pinned MLX runtime uses deterministic Euler rather than the workflows'
Euler ancestral CFG++ sampler; this is not numerical parity with ComfyUI.

Motion tests accept 49/97/121 frames at 768×448 or 1024×576; identity tests accept
only 121 frames at 768×448. Both modes reject alternative recipes and missing
control files or a missing dev transformer. Motion tests reject an end anchor.
Identity tests can pair `image` and `end_image` with the identity
reference: both images go to the IC pipeline as temporal anchors at frame 0 and
the last frame. They do not switch to the separate keyframe pipeline or replace
identity conditioning. An ending guide requires a starting image. This combined
route remains a qualification experiment; matching two stills does not prove
correct movement between them.
The 19 GiB start requirement, 8 GiB reserve, swap guard, timeout, and cancellation
handling apply. Do not run simultaneous GPU experiments.

Motion and Ingredients are separate tests: their reference downscale factors are
2 and 1 respectively, while the pinned runtime uses one global factor. Do not
combine them in one call. No weights, account credentials, or gated terms are
installed or accepted automatically. Verify each downloaded model against its
upstream revision and checksum before testing. Record the exact input hashes,
command, runtime revision, result, failures, and any temporarily paused services'
restoration with each trial.
