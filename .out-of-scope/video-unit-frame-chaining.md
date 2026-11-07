# Automatic first/last-frame chaining between shots

ArcReel does not automatically extract one generated video’s real last frame as the next video’s first frame, in either storyboard-to-video or reference-to-video mode. Reference-to-video also gains no first/last-frame transition slots.

## Why this is out of scope

### Storyboard-to-video: chaining conflicts structurally with arrangement

Storyboards are each shot’s visual truth and first frame, reviewed before paying for video. Chaining makes the second and subsequent first frames unknowable until preceding videos finish, removing review checkpoints. Batch admission with independent execution (`docs/adr/0061`) becomes serial dependency: wall time grows linearly with shot count, and failures block downstream shots. Regeneration invalidates every later shot’s basis (`docs/adr/0062` stale semantics do not automatically regenerate, silently losing desired continuity). Extracted endings often contain blur, closed eyes, or composition drift, accumulating errors through chaining; reanchoring every shot is a storyboard purpose.

#1289 fixed first frames to storyboards without customization and excluded fully automatic next-storyboard-as-last-frame continuity; this document also excludes previous-real-last-frame extraction. #2182 reopens next-storyboard-as-last-frame with explicit per-boundary marks, no full automation, manual-last-frame priority, and only model-supported/user-enabled application, pending manual tests.

`extract_video_last_frame` in `lib/infra/thumbnail.py` (currently unused in production) and task-order dependencies (already used for storyboard-image chains) exist. The issue is what this would break, not implementability.

### Reference-to-video: no frame slots in the input contract

`docs/adr/0055` distinguishes generation modes by model inputs: first-frame-driven i2v versus reference-set-driven r2v, locked at creation. Video units have no image fields; bodies’ `@[名称]` references derive visuals on read (`docs/adr/0064`). Last-frame services explicitly reject reference-video projects; execution checkpoints reject storyboard media. Adding first/last slots changes the mode criterion, and reference+first-frame combinations vary by vendor without a unified contract. Each unit independently uses references, without inter-unit visual continuity responsibilities.

### Viable alternatives

Continuity belongs to **boundaries between adjacent shots**, not global production switches. Script entries no longer carry `transition_to_next`; timeline clips now carry it for editing/final-output transitions only, without affecting video generation. #2182 proposes continuous boundaries that use the next storyboard as the last frame during video execution: no extraction, no serial execution, each storyboard remains its shot’s anchor. It needs boundaries fixed before generation and cannot directly reuse timeline-clip boundaries. Assumptions/validation live in #2182 (parked, awaiting manual tests). Available manual approach: select shot B’s storyboard among the episode’s images as shot A’s last frame.

## Revisit conditions

- A provider offers native continuation / extend from the previous video, removing extraction/first-slot dependencies;
- Reference-to-video: a backend explicitly supports reference+first-frame combinations with real demand; a new ADR must revise mode criteria.

## Prior requests

- #2164 第 5 条 — 参考生视频模式增加「这段首帧衔接上段尾帧」 (item 5: connect current first frame to preceding last frame in reference-video mode)
- #2164 第 6 条 — 分镜图生视频自动读取上一个视频的尾帧 (item 6: automatically use the preceding video’s last frame in storyboard mode)
- #2104 — 每一个分镜之间的转场功能 (transitions between shots; automatic previous-last-frame extraction is excluded here; transition types/manual last frames already exist, automation tracked in #2182)
