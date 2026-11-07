# Content and generation modes

ArcReel separates content type from the video-generation path:

- `content_mode` is `narration`, `drama`, or `ad`.
- `generation_mode` is `storyboard` or `reference_video`.
- `grid_storyboard` changes only storyboard assembly, not a third generation mode.

The plan specifies applicable steps for each combination. Follow `steps[].required` and `next_action.args`, without a separate mode-to-step table or inferred preprocessors.

Storyboard projects use per-shot/segment images as video inputs. Reference video uses self-contained units and directly parses named asset references. Narration, drama, and ads may have different script structures, but those structures cannot override the plan.

Reference video skips storyboard images, not narration selection. In every generation mode, before each video generation with narration, ask whether to use current TTS or leave narration to post-production.

Mode and content settings are project-level choices.
