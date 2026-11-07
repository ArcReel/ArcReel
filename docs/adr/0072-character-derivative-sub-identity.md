---
status: accepted
---

# Character derivatives are sub-identities: reference `@[角色/衍生]`; generate derivative asset images by editing the base image or upload replacements

One character asset image cannot express the same person's changes of clothing, transformations, or disguises. A derivative is a sub-identity under a character, sharing its name, voice, speaker binding, and global-namespace position. Existing fields in the character entry remain the base form; a `derivatives` map inside that entry stores derivatives keyed by derivative name, unique only within the owning character. Scripts reference `@[角色/衍生]`, with the same string in storyboard-route `characters_in_*` arrays. Since `/` is already forbidden in asset names, this syntax is unambiguous. `speaker` accepts only base names; derivative names in body speaker positions resolve to the base and bind only voice. Model the mechanism generically through AssetSpec; this decision enables it only for character.

**Derivatives have no originals; asset images may be generated or uploaded as replacements.** A derivative holds only a description of appearance changes relative to the base and one asset image. Generation edits the base asset image (ADR 0050 semantics), using the change description as editing instructions. Regenerating the base asset image makes generated derivative images stale without automatically regenerating them; derivatives cannot be generated without a base asset image. Authors may also upload a replacement derivative asset image through the same upload entry as base asset images; the upload becomes the current version and enters version history. Uploaded derivative images are finished artifacts, registered by the image itself (see `docs/adr/0062`): regenerating the base image, changing base or derivative descriptions, or changing style does not make them stale. Selecting a generated version restores the basis of editing the base asset image; restoring an uploaded version again registers by the image itself. Uploading does not require a base asset image. Uploads replace only the asset image and are not base images for further edited generation, so derivatives still have no originals. Character appearance in storyboard and video prompts = base description + current form's change description, including when the asset image was uploaded.

**Selecting forms within one entry**: no limit. Inject one asset image for every referenced base or derivative (including the base alongside a derivative in one shot, or multiple derivatives together), explaining in prompts that they are different forms of the same character. Inject exactly the references authored, enabling shots such as before-and-after transformation forms together.

**Disk layout**: `characters/derivatives/{角色}/{衍生}.png`, with version history in `versions/characters/derivatives/{角色}/`. Characters may be named `refs`, so derivatives cannot live directly under `characters/{角色}/`. Renaming characters moves directories; renaming derivatives changes file stems. Both join ADR 0057's cascading rename transaction. Artifact-inventory keys are `(character, "角色/衍生")`.

The **global asset library** carries derivatives in child table `asset_derivatives`, moving them in/out together with the character; conflict strategy `rename` changes only the character name. Derivative images imported from the library are finished artifacts, registered by the image itself (see `docs/adr/0062`): regenerating the base image or changing base/derivative descriptions does not make them stale. Regeneration inside the project returns to the basis of editing the base asset image.

## Explicitly rejected

- **Registering derivatives as separate characters with a "same person" association**: no schema changes, but speakers, reference audio, and identity descriptions require repeated maintenance across entries, and Agents would treat them as two people, contradicting "character = a specific identity."
- **Making everything a form, migrating the base into a derivative named "default"**: more symmetric, but requires schema migration, file relocation, artifact-inventory rekeying, and rewriting all existing scripts; the asymmetry of the base as "persistent appearance" already exists in creative practice.
- **Giving derivative description a complete appearance that replaces the base description**: repeats base facial/body descriptions and loses "edit based on the base" generation semantics.
- **Uploading a derivative base image and editing it using the change description**: effectively gives derivatives originals, forcing a choice of generation basis between the base asset image and derivative original and adding another original-image slot to the interface. Users want to directly replace unsatisfactory generated results with satisfactory images; asset-image replacement uploads already meet that need.
- **Derivative-specific voice_style or reference audio**: voice belongs to identity; a form with a different voice is another character.
- **Using `#`, `·`, or `-` as separators**: all are valid asset-name characters and produce ambiguous parsing.

## Consequences

- Increase the project schema version; `derivatives` defaults to an empty map, without rewriting existing projects.
- Expand the character-name set in validation from flat names to "base names ∪ base names/derivative names"; `_unregistered_refs` remains the sole equivalence-check entry point.
- Asset analysis (analyze-assets) identifies derivatives, with criteria in that agent definition; script_plan / prompt_authoring write `@[角色/衍生]` according to story state.
- Storyboard-reference popovers, avatar stacks, and asset cards must display derivatives. An asset-card derivative button opens a floating layer for viewing/editing derivatives.
