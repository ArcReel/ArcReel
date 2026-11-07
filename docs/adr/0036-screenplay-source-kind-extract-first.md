---
status: accepted
---

# Screenplay sources: record source kind per file, reuse the full extraction-first pipeline, and preserve verbatim only audible content

Drama normally treats source text as a novel, **creating** scripts from prose through episode planning, script planning, and prompt writing. Some users instead upload completed screenplays with their own episodes, scenes, dialogue, voiceovers, and characters; the adaptation pipeline **rewrites them again**, recutting the author's episodes, adapting dialogue, and losing voiceovers. Introduce source-file kind (`source_kind`: default `novel` / `screenplay`) as a third axis orthogonal to content type and generation mode. For `screenplay`, switch the pipeline from "creation" to "extraction first," anchoring verbatim fidelity only to "audible content."

## Record source kind with each source file

A project may contain several sources (multiple whole-source files and episodes with their own source text); "novel main story + screenplay extra" is normal, so record source kind with the source file: once per whole-source file, or on the episode-ledger entry for an episode with its own source. Split episodes inherit the kind of the file containing their source-range start without storing another copy; episodes without source have no kind. Select kind alongside "whole source / per-episode source" when uploading or entering source text, defaulting to novel and allowing changes afterward at any time.

- Only drama has source-file kind; both storyboard-image-to-video and reference-to-video generation modes use it to choose adaptation / extraction. Narration/commentary source text already enters narration verbatim, so finished commentary scripts are uploaded directly as source; ads/short films have no source, and finished copy is entered manually through "start from blank."
- Kind is not source content: changing it does not change source fingerprints, remap split episodes, or alter episode ledgers. Existing splits retain planning under the old kind; creators initiate replanning to re-split under the new kind. Script plans produced under the old kind become stale under artifact freshness; formal scripts do not depend on script plans (see `docs/adr/0080`) and remain unaffected. Creators decide whether to replan scripts under the new kind.
- An AI episode-planning window stops at a kind change, containing only one kind.
- Project overviews use that kind when all source text shares it; mixed sources use the novel basis.

## Extraction first; preserve verbatim only audible content

Extraction first **does not introduce a separate screenplay parser**: it spans every existing episode-planning, script-planning, and project-overview stage. LLMs first use what authors already wrote (in any form, without hardcoded marker regexes), generating only what is missing, fitting the "lazy per-episode consumption" architecture (screenplays can reach 100,000 characters). Stages degrade independently (a character list without episode markers → verbatim character extraction + semantic episode planning). **Never split episodes mechanically**: episode planning remains semantic. If the screenplay supplies episodes in any form, use their boundaries, titles, hooks, and outlines; otherwise choose split points at complete story arcs. Mechanical length-based splits break arcs. **Verbatim fidelity anchors only to "audible content"**: strictly preserve without rewriting, dropping, or polishing only character-dialogue text and voiceover text. LLMs may transcribe or strip formatting and labels, camera/stage directions, visual descriptions, and generic extras; strict verbatim handling would force stage directions, extras, and formatting symbols into structured fields.

Verbatim extracted dialogue and voiceovers enter `utterances` (dialogue `kind=dialogue` with speaker, voiceover `kind=voiceover` without speaker, in occurrence order; see the model in `docs/adr/0040`). Generic speakers (`老人甲` / `年轻人乙`) retain their source labels but are not registered as character assets or included in appearing characters. LLMs distinguish generic from named characters at the prompt layer; "speaker must be an appearing character" is only a prompt instruction without mechanical validation, relaxed for screenplays. Candidate review in episode planning (adjustments use replanning; see `docs/adr/0032`) and content confirmation in script planning cover LLM misinterpretations of arbitrary formats in extracted skeletons (episodes + characters), without a separate mechanism.

## Considered Options

- **One dedicated large parser**: placing 100,000 characters into one call and front-loading all work at upload contradicts "lazy per-episode consumption"; hardcoded structural assumptions for diverse formats are brittle. Extraction first spans existing stages, with each stage using LLM semantic recognition on its own slice and filling only what is missing.
- **Project-level, fixed at creation**: cannot express mixed sources. Web creates projects before uploading sources, requiring kind selection before seeing the source and project recreation after a wrong choice.
- **Project-level but mutable**: fixes wrong choices but still cannot express mixed sources; downstream already reads kind by planning window and episode, with only project overviews needing a whole-project view.

## Consequences

- Prompt templates split into named variants along the source-kind axis (see `docs/adr/0077`): episode planning, script planning for both drama generation modes, and project overviews each have `novel` / `screenplay` variants.
- Data validation accepts only `novel` / `screenplay`, rejecting invalid values such as `screen_play`; project top level no longer has `source_kind`.
- "Voiceover/dialogue dubbing" (drama-TTS) belongs to dubbing and composition, outside this decision.
