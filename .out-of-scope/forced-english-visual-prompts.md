# Do not globally force visual prompts to English

ArcReel does not globally fix image/video visual prompts (`image_prompt`, `video_prompt`, reference-video unit bodies, etc.) to English. Like dialogue/narration, visual prompts use project content language (`source_language`); embedded-Agent prompt language follows the session language.

## Why this is out of scope

- **Prompts are creator-readable/editable content**: storyboard `image_prompt` / `video_prompt` and reference-video bodies appear directly in the Web editor. Globally forcing English makes non-English creators read/write outside their creative language, increasing editing/review cost.
- **Mainstream models support non-English prompts well**: most built-in image/video providers understand project languages such as Chinese directly, without evidence of universal benefit from global English prompts.
- **One body would mix languages**: reference-video bodies embed verbatim speech with `@[角色]{台词}`, which must retain project language. English visuals would mix two languages in one body, harming verbatim fidelity/readability.

## Revisit conditions

The problem exists for some models: certain models understand Chinese poorly, notably self-hosted ComfyUI workflows and image models such as Midjourney / Stable Diffusion. Solutions remain under evaluation, oriented to a particular model/endpoint: rewrite prompts before submitting to that model or select prompt language at project/endpoint level, rather than globally changing it.

## Prior requests

- PR #3051: refactor: separate visual and dialogue prompts across languages (the English-visual-prompt portion)
