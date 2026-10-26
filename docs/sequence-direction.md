# Sequence direction in drama projects

Newly generated drama script plans require a shared `sequence_direction` and each scene's `continuity`. Existing scripts and drafts remain readable without these fields.

The shared plan records dramatic intent, the causal action arc, necessary geography, identity anchors, audio continuity and allowed filming adaptations. Shot continuity records purpose, incoming state, outgoing state and cut motivation. These are revisable filmmaking choices, not exact-pose or pixel-placement quality gates. Their presence does not establish visual quality.

Content confirmation copies both layers from the revision-checked draft into the formal script. Prompt authoring uses that formal script: a targeted rewrite sees the shared plan and preceding/following shot content, while its output may change only the selected shots' visual prompts. It never reloads a stale planning draft or attaches generated media from distant shots to the prompt.

The native `patch_episode_script` tool accepts `set_sequence_direction` with a complete `direction` object. Combine it with `update` operations on affected scenes' `continuity` in one `base_revision`-checked atomic batch. Reauthor affected visual prompts before generating replacements. This operation does not delete or regenerate paid media automatically. Non-drama scripts reject the operation.

After a move, insert, removal or story change, the director must reassess the neighboring states and update them as needed. The schema does not infer semantic continuity from matching text and does not enforce literal pose equality. Review the actual motion, adjacent cuts and continuous soundtrack before accepting a sequence; valid planning JSON is not a creative pass.
