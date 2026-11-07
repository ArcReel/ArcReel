---
status: accepted
---

# Project style stores expanded prompt snapshots, exclusive with style-reference images

Saving only `style_template_id` and expanding at generation would propagate template upgrades automatically but suddenly change completed projects after registry edits, breaking final-cut continuity. When selecting a template, expand the whole visual-style prompt into project.json's `style`, retaining `style_template_id` as an origin marker. PATCH with a new template id re-expands; read-time migration only resolves legacy short labels lacking ids once, never re-expanding stored ids. Later registry changes do not actively rewrite old projects. Project style has three mutually exclusive final states: template / custom style-reference image / none. Write paths prevent simultaneous activation so prompt assembly consumes one already-expanded source.

## Consequences

- Template improvements do not propagate to existing projects. `style` changes from a short label to long text, transparent to LLM input but breaking in meaning.
- Creation / PATCH / migration all enforce exclusivity: writing templates clears `style_image`; writing reference images clears `style_template_id`; explicitly clearing a template (null id) also clears expanded `style`, leaving no orphan text. For historical-data races, style_image wins and template_id is actively cleared.
