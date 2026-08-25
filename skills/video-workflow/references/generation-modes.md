# Content and generation modes

ArcReel separates content type from video generation path:

- `content_mode` is `narration`, `drama`, or `ad`.
- `generation_mode` is `storyboard` or `reference_video`.
- `grid_storyboard` only changes storyboard image assembly; it is not a third generation mode.

The plan expresses which steps apply to each combination. Do not keep a parallel mode-by-step table or infer a preprocessor: use `steps[].required` and `next_action.args`.

Storyboard projects use per-shot or per-segment storyboard images as video inputs. Reference-video projects use self-contained video units and resolve named asset references directly. Narration, drama, and ad can therefore expose different script structures, but those structures do not override the plan.

Reference-video projects skip storyboard images, not narration choices. For each narrated video request in either generation mode, ask the user whether to use current TTS or leave narration for post-production.

Mode and content settings are project-level choices. Use ArcReel's content readers and transactional editors for the selected project; do not inspect or modify host-local project files.
