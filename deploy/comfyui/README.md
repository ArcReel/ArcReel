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

## Isolated motion, structure, and identity qualification

The runner accepts `motion-track`, `structural-control`, and `identity-reference` modes for bounded local
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
identity references without conflicting character panels. Use the adapter's
prescribed reference dimensions and match the output frame rate and duration. Motion tests can start short;
Ingredients tests require the trained bucket of 768×448, 121 frames at 24 fps,
with the still reference repeated across the full clip.

These modes use the dev transformer, control LoRA strength 1.0, distilled LoRA
strength 0.5, and single-stage full-resolution generation. Unlike the keyframe
strength-1.0 shortcut above, these modes **read the separate distilled LoRA file**.
This weight composition follows the official single-stage distilled workflows
at ComfyUI-LTXVideo revision `220e1d45aa0469f518e982102fca4dca92a61862`.
The pinned MLX runtime uses deterministic Euler rather than the workflows'
Euler ancestral CFG++ sampler; this is not numerical parity with ComfyUI.

An isolated runtime that explicitly supports `ic-lora --sampler` can be tested
with `"sampler": "euler_ancestral_cfg_pp"` and an optional `"negative_prompt"`
string in the runner configuration. The default remains Euler. CFG++ evaluates
an additional negative prediction at each nonterminal step, so budget for the
extra compute. It does not use OpenAI or alter approved audio stems. The current
installed runtime must be qualified separately before enabling this option;
passing sampler tests does not establish motion or character quality. Sampler
controls on ordinary generation modes and negative text with Euler are rejected
instead of silently ignored.

Motion and structure tests accept 49/97/121 frames at 768×448 or 1024×576; identity tests accept
only 121 frames at 768×448. These modes reject alternative recipes and missing
control files or a missing dev transformer. Motion tests reject an end anchor.
Identity tests can pair `image` and `end_image` with the identity
reference: both images go to the IC pipeline as temporal anchors at frame 0 and
the last frame. They do not switch to the separate keyframe pipeline or replace
identity conditioning. An ending guide requires a starting image. This combined
route remains a qualification experiment; matching two stills does not prove
correct movement between them.
The 19 GiB start requirement, 8 GiB reserve, swap guard, timeout, and cancellation
handling apply. Do not run simultaneous GPU experiments.

Motion and Union use reference downscale factor 2; Ingredients uses factor 1.
The pinned runtime uses one global factor. Do not
combine them in one call. No weights, account credentials, or gated terms are
installed or accepted automatically. Verify each downloaded model against its
upstream revision and checksum before testing. Record the exact input hashes,
command, runtime revision, result, failures, and any temporarily paused services'
restoration with each trial.

### Dense depth contact fixture

`structural-control` is an isolated Union Control experiment, not a production
capability. It requires a starting image and accepts an optional `end_image` as
a final-frame anchor within the same IC-LoRA pipeline. Both image anchors and
the depth guide remain connected; this does not switch to keyframe-only
generation. Check that the anchors and guide describe compatible poses before
inference. Endpoint accuracy is not proof of a valid path between them. Supply the
LTX 2.3 Union adapter and a grayscale depth video at half the output dimensions,
matching the reference downscale factor in its metadata. Keep all guides at the
same frame count and 24 fps. The versioned single-stage workflow is
[LTX 2.3 Union Control at 6df1f3f](https://github.com/Lightricks/ComfyUI-LTXVideo/blob/6df1f3ff90f03437911756d0dcb51ac7580bebe9/example_workflows/2.3/LTX-2.3_ICLoRA_Union_Control_Distilled.json).
Do not substitute a newer model's workflow or combine adapters with different
reference scales.

Generate a deterministic 49-frame contact diagnostic with:

```bash
uv run python -m scripts.prepare_contact_guide --output-dir /absolute/new/guide-directory
```

Add `--reaction` to test a creature head dip followed by a small upper-body
counter-lean and settle. The feet stay planted, arm lengths and contact are still
checked, and the first/last poses match the contact-only fixture. This isolates
the performed guide from the image anchors, prompt, and seed. The proxy does not
provide facial acting, a walk cycle, or an approved character rig.

Use `--expressive` instead for a larger shoulder yield toward the pulling bag,
recovery after palm support, and a separate neck-pivot head tilt. It keeps the
same planted feet and contact checks. Its final head pose differs from the
contact-only fixture: pair it with a reviewed matching end image. Eye direction
and facial expression must come from that image; depth silhouettes do not encode
them. Neither preset is production-qualified without reviewing its actual video.

Use `--performed` for a 49-frame acting diagnostic: the creature anticipates and
nods, then the human recoils while carrying the supported prop, recovers, and
looks back. This avoids asking for a visible recoil with an almost fully extended
arm locked to a stationary prop. Review the source performance before inference.
Generate the character motion without glitter or other effects; any later effect
must be timed against the actual reviewed output action, not the requested frame.
The proxy has no facial rig and cannot establish eye acting or a convincing sneeze.
Do not promote it or its neural output on geometry tests alone.

Use `--continuation` to reproduce the earlier 97-frame supported reaction. Its first
pose equals the expressive catch's final pose, and contact is required from frame
zero. A creature head pulse precedes a small human recoil and recovery. Its fixed
viewport is `(240, 0, 480, 280)` in the 768x448 source camera: use the same crop of
the actual preceding video endpoint as its starting image. This is a same-axis
cut-in, not a newly solved camera angle. The depth guide supplies body motion;
facial acting, particles and sound still need independent output review.

This orthographic proxy checks fixed arm lengths, arm/bag clearance, and a palm
supporting the bag underside. It records frame coordinates, source and video
hashes, and validation metrics; existing output directories are never replaced.
Its synthetic inverse depth uses fixed bounds throughout the clip. It is not an
approved character rig, a learned depth estimate, a whole-body physics check, or
a guarantee of generated contact. The model's training distribution may differ.
Inspect the decoded guide before inference, and review every generated frame
for contact, occlusion, form, and timing afterward. Source geometry passing
does not qualify the generated take or authorize a production episode.
