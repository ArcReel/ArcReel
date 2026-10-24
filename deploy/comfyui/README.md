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
| Start/end image | Explicit frame 0 and `last` anchors, strength 1.0. End anchor is optional. Use matching identity, geography, scale, and direction. Two anchors do not guarantee physically correct motion between them. |
| Seed | Honor a requested unsigned 32-bit seed; generate and retain a seed when absent. A seed is not a character identity lock. |
| Identity | Approved character references must be used to prepare every anchor. This endpoint does not expose independent identity-reference conditioning. |
| Prompt | Preserve all text, including `Avoid:` instructions when no negative input exists. Distilled does not support negative conditioning or CFG/STG tuning. |
| Audio | Native audio remains available as a scratch track. Final narration and music use approved continuous timeline stems; a new shot must not replace the speaker or restart the score. |
| Memory | Existing block streaming, sequential text encoding, decode budget, swap guard, cancel cleanup, and timeout remain in force. |
| A2V, retake, extend, HQ | Installed experimental routes, not silently substituted for distilled. Require their own measured qualification before default use. |
| Pose/depth/control/identity LoRA, LipDub, LTX 2.5 | Required weights are absent. Do not advertise these as working local controls. |

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
