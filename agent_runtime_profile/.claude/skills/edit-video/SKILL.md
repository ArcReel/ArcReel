---
name: edit-video
description: Edit an episode on an editing timeline, then render a final cut or export a Jianying draft as requested. Use when all episode videos are ready and an edit is needed; also use when the user requests changes by clip number (such as “c3 drags”), BGM, final output, a Jianying draft, timeline duplication or renaming, or restoration of an older revision.
---

# Edit video

An episode's final composition is recorded only on the editing timeline: previews assemble it, and final cuts and Jianying drafts are generated from it. Perform all editing and output through `mcp__arcreel__*` tools. Tool descriptions govern each tool's parameters, allowed values, and write semantics.

## One editing round

Run one round for each editing request from the user. Every round ends with a delivery.

1. **Choose the editing timeline**: Use `mcp__arcreel__list_timelines` to list this episode's editing timelines.
   - If the episode has none, use `mcp__arcreel__create_timeline` to mechanically create one from the script, with the display name “Full version”.
   - Use the timeline the user names; otherwise use the most recently modified timeline in `list_timelines`. If the user's wording matches several and the intended one is unclear, ask first.

   Completion criterion: obtain exactly one editing timeline ID.
2. **Read**: Use `mcp__arcreel__read_timeline` to read the selected timeline and map the user's requests to clip numbers (such as c3). If a referenced clip cannot be found, clarify before editing. For output-only requests, go directly to “Output” after reading. For a first editing round (a newly created timeline or a request to make an edit), if `issues` contains `narration_missing`, first follow `generate-narration-audio` to fill missing narration audio and reread the timeline (narration overruns and collisions require measured voiceover durations). Then review all material as described under “Full first-round review” before step 3.

   Completion criterion: every request maps to a clip, or the information needed from the user is identified; a first round also has a footage review report for each video unit, and TTS projects have narration audio filled in or the units that cannot be filled recorded.
3. **Editing decisions (L1)**: Use the user's requests, each clip's duration, status, editing rationale, and `issues` to decide this round's operations:
   - **Structure**: Decide which clips to keep, reorder, or reuse according to narrative order. Duplicate before major restructuring (reordering most clips or changing the overall rhythm): use `mcp__arcreel__create_timeline` (`from: "timeline"`, with `timeline` set to the currently selected ID), giving it a creator-recognizable name such as “Fast-paced version”. Edit the copy, keep the original as a fallback, and tell the user the new timeline's name.
   - **Inspect visuals**: Base the first round on footage review reports. When the user asks about particular visuals (whether a clip breaks down, where action starts or drags, or which candidate is better), use `mcp__arcreel__inspect_video_units` to inspect the relevant units' contact sheets and judge the images yourself. Contact sheets also label machine signals: BLACK black intervals and FREEZE frozen intervals are suspected defects; confirm visually before deciding whether to trim them. CUT shot changes are structural information, not defects. Signals are only hints; base choices on the visuals.
   - **Pacing**: Decide trims or tail freezes using user-specified intervals or visually determined times. Contact-sheet times and in/out points share the same time axis; place in/out points at the CUT frame or the frame immediately before it, not mid-transition. When trimming black or frozen beginnings or endings, the interval's end is a candidate in-point and its start a candidate out-point.
   - **Connections**: Transitions can be set between adjacent clips. The script's `segment_break` (scene change) can suggest transition placement.
   - **Sound**: Adjust original audio volume as requested. For adding, replacing, or adjusting BGM, follow “Add BGM”.
   - **Narration**: If `issues` contains `narration_overrun` or `narration_source_collision`, follow “When narration does not fit”.
   - **Versions**: When the user specifies another video version, or reports or visual inspection show a clearly better candidate, switch directly with `mcp__arcreel__select_video_version` (no permission needed), reread the timeline, and decide trims again.
   - **Creator decisions pending**: Record unusable material, video units needing regeneration, and voiceover needing rewriting for the checklist in step 5.
   - **Rationale**: Write one creator-facing editing rationale for each retained or newly modified clip; put reasons for deletions in this round's summary.

   Completion criterion: every request maps to specific clips and operations; record why any request cannot be fulfilled for explanation in step 5.
4. **Submit**: Combine this round's timeline adjustments into one `mcp__arcreel__edit_timeline`, based on the latest read, with a one-sentence creator-facing change summary. If there are no executable adjustments, proceed to step 5. If operations are rejected, correct them using the returned locations and resubmit the whole batch; for `revision_conflict`, reread and reconsider the round's adjustments against the latest content.

   Completion criterion: adjustments were submitted successfully, or it is clear that this round has none to submit.
5. **Deliver**: Following “Delivery at the end of a round”, deliver the change summary, “Jump here to inspect” links, and a checklist when decisions are pending, together.

## Full first-round review

In the first round, review every video unit used by the timeline (current versions, comparing candidates where available), delegating parallel work to read-only `review-footage` subagents. Contact sheets enter only the subagents' contexts; you read their text reports only.

1. **Group**: Collect used video unit IDs from `read_timeline` clips, deduplicate, and group by scene or neighboring units, around 6 to 10 units per group. A unit and its candidates belong to only one group, compared by the same subagent.
2. **Dispatch**: In one message, invoke one `review-footage` subagent per group so they run in parallel. Specify the script file, the group's unit IDs, and report requirements. Reports must at least cover usability, usable intervals and evidence, problems and timestamps, candidate ranking, whether regeneration is recommended (and whether prompts need changes), and one sentence usable directly as editing rationale.
3. **Consolidate**: Decide step 3 operations per unit from the reports: select versions, trim to usable intervals, and write rationales; record units recommended for regeneration as creator decisions pending.

Completion criterion: every unit used by the timeline has a footage review report. Dispatch once more for units missing reports.

## Delivery at the end of a round

End each editing round with one delivery, containing all three items in the same message:

1. **Change summary**: List what changed and why by clip number, including selected video versions. Then list remaining blocking and warning problems in result `issues`, and explain unfulfilled requests and reasons.
2. **“Jump here to inspect” links**: Attach an in-app Markdown link to every problem. Paths start with `/app/`, omit the domain, and URL-encode the project name as a path segment:
   - For a problem on a timeline clip: `/app/projects/{项目名}/episodes/{集 ID}?view=edit&tl={剪辑时间线ID}&t={秒}`. `t` is global timeline time: the clip's `start` plus “problem timestamp minus this clip's in-point”.
   - For a problem on a unit outside the timeline: `/app/projects/{项目名}/episodes/{集 ID}?unit={单元ID}&t={秒}`. `t` is time within that unit's own video.
3. **Checklist**: When creator decisions are pending, after sending the delivery message, ask them all at once using AskUserQuestion multi-select (`multiSelect: true`), one question per category:
   - **Regenerate**: First preview with `mcp__arcreel__generate_videos` (`preview: true`, `target` set to `selected` with all candidate units listed, `force: true`). Each option states the video unit, problem and timestamp, whether prompts change, estimated cost, and duration tier (if the tier changes, state the planned duration and requested tier); also include “Regenerate all” with total cost. Units marked `blocked` in the preview must not become options; explain the gaps in the change summary.
   - **Rewrite voiceover**: Each option states original text → revised text and estimated seconds saved.

   If a question cannot fit all candidates, combine them by scene into an option identifying included units and total cost. Do not send a checklist when no decisions are pending.

Completion criterion: every delivery problem has a link; every pending decision appears in the checklist with all information above.

## Execute selected items

Confirm paid actions or script changes only once per round: execute the creator's checklist selections directly without asking again.

- **Regenerate**: First modify prompts with `mcp__arcreel__patch_episode_script` for units requiring changes. Then submit all selected items in one `generate_videos`: set `target` to `selected`, list selected units, use `force: true`, and pass `confirmed_request_durations` unchanged from the preview's field of the same name so duration tiers do not require confirmation again. If reconfirmation is requested, explain that tiers or costs changed, give the creator the updated quote, and let them decide whether to continue.

  Acceptance after completion: record each unit's current version number before regeneration, and dispatch `review-footage` to compare old and new versions. Make the better version current (use `select_video_version` to switch back if the old one is better), reset in/out points from the report, write them into the timeline with `edit_timeline`, then report results following “Delivery at the end of a round”. If the new version still has problems, report them in delivery without automatically regenerating a second time.
- **Rewrite voiceover**: Rewrite selected voiceover with `mcp__arcreel__patch_episode_script`, then regenerate narration audio with `mcp__arcreel__generate_narration_audio`, naming these video units in `segment_ids`.

## When narration does not fit

Narration starts at its host clip's beginning and plays for its measured audio duration. If longer than the visuals, it extends into following clips, overlaps the next narration or exceeds the end (`narration_overrun`), and may cover dialogue or clips whose original audio has not been lowered (`narration_source_collision`). Handle this in three levels according to authorization boundaries:

- **Timeline-only changes: act directly.** Loosen trims, switch video versions, insert additional clips of the same unit, extend tail freezes, reattach narration with `place_narration`, or lower covered clips' original audio. Submit these alongside other adjustments in this round and explain them in clip editing rationales.
- **Rewrite voiceover: obtain creator confirmation first.** Add it to the checklist and follow “Execute selected items”.
- **Regenerate a longer video: change planned duration first, then obtain consent.** This costs money: first explain which unit's planned duration should change to what value and the estimated cost; only after the creator agrees, change the duration and regenerate.

Both issues are warnings and do not block output. If narration still does not fit after timeline adjustments, state remaining overruns and collisions in the summary, letting the creator choose output with warnings or approve rewriting or regeneration.

## Add BGM

The creator uploads BGM on the Web editing view's BGM track. BGM belongs to the entire project, and its loudness is normalized on upload. Submit BGM operations with this round's other adjustments in the same `edit_timeline`.

1. **Choose BGM**: Use `mcp__arcreel__list_bgm` to list project BGM. Use the track the user names; otherwise choose by name the one best fitting the episode's mood, and explain the selection in the summary. If there is no BGM or none suitable, ask the creator to upload on the editing view's BGM track, then place it after upload.
2. **Place**: Use `insert_bgm` with an absolute timeline start. Keep default volume and fades unless the user requests changes.
3. **Join multiple segments**: If BGM is shorter than the required coverage, place another segment after it: its `start` equals the preceding segment's end (the preceding `start` + `source_out` − `source_in`), touching without overlap. Use another passage of the BGM if appropriate to avoid repeating the same phrase. Shorten fades on both sides of joins to around 0.5 seconds for continuity.
4. **Ending**: Material beyond the timeline end is automatically truncated and faded there; no need to calculate the last out-point exactly.

Completion criterion: BGM clips cover the requested interval without overlap; `issues` has no `bgm_missing`. If `bgm_missing` occurs, choose existing project BGM or ask the creator to upload again.

## Restore older revisions and organize timelines

Every submission appends a revision; history is not rewritten.

- **Restore an older revision**: When the user wants to undo changes or return to an older state, find the target with `mcp__arcreel__list_revisions`. For “undo the last change” or an explicitly named revision, roll back directly using `mcp__arcreel__restore_revision`; if unclear, list revision summaries for the user to choose. Rollback replaces the entire current content. Read the timeline afterward and describe its current state using the change-summary format in “Delivery at the end of a round”.
- **Rename**: Use `mcp__arcreel__rename_timeline`; it changes only the display name and creates no revision.
- **Delete**: The Agent has no timeline deletion tool (`delete_project_file` deletes only unreferenced files and historical versions; timelines are rejected). If the user wants one deleted, tell them to open the menu beside its timeline tab in the Web editing view and choose “Delete”.

## Output

There are two deliverables: final cuts (`mcp__arcreel__render_final_cut`) and Jianying drafts (`mcp__arcreel__export_jianying_draft`). Produce whichever the user requests; if they only say “output”, produce a final cut. Export a Jianying draft only when requested.

1. **Disclose issues**: Use `issues` from the latest read or submission result.
   - For blocking problems: list them, explain which clips prevent output and how to fix them; do not submit until resolved. Problems with `applies_to` set to `with_narration` block only narrated versions. The user decides on paid remedies (such as generating missing videos).
   - For warning problems: list them, explain output will include these problems, then submit. If the user asks to fix them first, return to “One editing round”.
2. **Submit**: Submit the selected timeline using the corresponding tool and wait for a terminal result. Select narration (both deliverables) and burned-in subtitles (final cuts only) as requested; omit unspecified options to use defaults: TTS projects include narration, and final cuts burn in subtitles. If rejected, tell the user returned problem codes and affected clips, and suggest fixes supported by the tools. After user confirmation, run another editing round.
3. **Deliver**:
   - Final cut: provide the download link and acceptance results. Explicitly flag a significant difference between measured audio/video duration and timeline duration.
   - Jianying draft: the result has no download link. Tell the user the draft was generated and should be downloaded from the Web interface, entering their local Jianying draft directory when downloading.
