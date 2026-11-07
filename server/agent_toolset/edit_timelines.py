"""Edit-timeline declarations: create from script or copy, list, read, batch-edit, rename, revision history and restoration, and list available BGM."""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.bgm import ListBgmRequest, bgm_list_summary, list_bgm
from server.media_tools.edit_timelines import (
    CreateTimelineRequest,
    EditTimelineRequest,
    ListRevisionsRequest,
    ListTimelinesRequest,
    ReadTimelineRequest,
    RenameTimelineRequest,
    RestoreRevisionRequest,
    create_timeline,
    edit_timeline,
    list_revisions,
    list_timelines,
    read_timeline,
    rename_timeline,
    restore_revision,
    revision_history_summary,
    timeline_list_summary,
    timeline_readout_summary,
    timeline_renamed_summary,
    timeline_write_summary,
)

CREATE_TIMELINE = ToolDeclaration(
    name="create_timeline",
    description=(
        "Create a named edit timeline; multiple timelines may belong to one episode, but display names must be unique (timeline_name_conflict). from=script arranges the episode’s video units in current script order as an editing starting point: full clips, hard cuts, source volume 0.3 for off-screen-speech units and 1.0 for dialogue / speechless units, narration placed on the unit’s first clip. Missing episode returns episode_not_found. from=timeline copies timeline revision (latest if omitted) into a new timeline in the same episode, preserving order, trims, source volume, holds, transitions, narration placement, BGM, and clip IDs; the new timeline starts at revision 1. Useful as a fallback before major changes or an alternate edit alongside the original; the source timeline is unaffected. Missing revision returns revision_not_found. Clip visuals always use each video unit’s current video version. Timelines do not automatically follow script changes; differences appear in issues. Result shape matches read_timeline."
    ),
    request_model=CreateTimelineRequest,
    migration=BLOCKED,
    domain_key="edit_timeline",
    handler=create_timeline,
    summary=timeline_readout_summary,
)

LIST_TIMELINES = ToolDeclaration(
    name="list_timelines",
    description=(
        "List project edit timelines: id, episode ID, display name, latest revision, clip count, and last modified time, ordered by episode and creation. Read-only, no side effects."
    ),
    request_model=ListTimelinesRequest,
    migration=BLOCKED,
    domain_key="edit_timelines",
    handler=list_timelines,
    summary=timeline_list_summary,
)

READ_TIMELINE = ToolDeclaration(
    name="read_timeline",
    description=(
        "Read the full latest edit-timeline revision; revision is its current revision number. Read-only, no side effects. Missing ID returns timeline_not_found. All times are seconds, at most three decimal places. clips are in playback order, each with id (such as c3, stable within this timeline; use it when discussing clips with the creator), unit_id, start and duration (server-calculated absolute start and duration), source_volume (0–1), trim (in/out points and basis_version), source_duration (full current-video length, null without usable video), hold (tail freeze-frame extension), transition_to_next (null for hard cut), narration (start/end, null end means no narration audio yet), and status: ready usable; video_missing temporarily uses planned duration; unit_deleted is skipped during rendering with zero duration. Each issue has code, severity (blocking prevents final cuts; warning/info do not), applies_to (all, or with_narration affecting only narrated variants), and relevant clip_ids / unit_id: video_missing means no usable video; unit_deleted means the referenced unit left the script; unit_unused means a script unit is absent from this timeline; trim_ignored means the trim’s basis version is no longer current, so rendering temporarily uses the whole video; hold_too_long means a clip’s hold extension exceeds 2 seconds. Three narration issues are reported only for TTS projects: narration_missing for off-screen-speech units without narration (blocking, narrated variant only); narration_overrun for overlap into the next narration (params.cause=next_narration, clip_ids identify both carrier clips) or past timeline end (cause=timeline_end); narration_source_collision for narration extending into dialogue clips (cause=dialogue) or clips with source volume above 0.3 (cause=source_volume), with carrier and overlapped clip IDs. subtitle_missing_glyphs (warning) lists subtitle characters absent from the bundled font in params.characters; these may display incorrectly when burned in. bgm is ordered by start; each clip has id (such as b2), bgm_id and name, start, end (actual end clipped to timeline end), source_in / source_out (selected BGM range), volume, and effective fade_in / fade_out. bgm_missing (blocking) means referenced BGM no longer exists in the project; clip_ids identifies that BGM clip."
    ),
    request_model=ReadTimelineRequest,
    migration=BLOCKED,
    domain_key="edit_timeline",
    handler=read_timeline,
    summary=timeline_readout_summary,
)

EDIT_TIMELINE = ToolDeclaration(
    name="edit_timeline",
    description=(
        "Modify an edit timeline through a batch of operations addressed by clip ID, atomically committing one new revision with a change summary. base_revision comes from read_timeline revision and governs the whole batch; operations run in order, each seeing prior results. Any invalid operation leaves the whole batch unapplied, returning operation_invalid with params.operation_index (zero-based), clip_id, field, and allowed values. If base_revision is outdated but none of this batch’s clips changed since it, apply normally to the latest revision and list intervening concurrent_revisions; otherwise return revision_conflict with latest_revision and conflicting_clip_ids, requiring a reread before editing. Operations: insert creates a server-assigned ID returned in the result, allowing repeated use of one video unit; omitted source_volume defaults to 0.3 for off-screen speech and 1.0 for dialogue / speechless units; delete; move; set_trim uses current video version and becomes invalid on version changes, temporarily using the full video; set_volume; set_hold; set_reason; set_transition; place_narration. Times are seconds, at most three decimals, with server-normalized values in results. Transitions attach to the preceding clip toward the next and do not change total duration: the window straddles the cut equally, and the sum of transitions on both sides of a clip cannot exceed twice its duration. insert/delete/move reset cuts with changed adjacency to hard cuts, including the moved clip’s own transition; subsequently set_transition again in the same batch if needed. A unit’s narration attaches to only one of its clips, starting at that clip’s beginning and running for measured audio duration, possibly into later clips. Inserted clips carry narration if no clip of that unit does yet; deleting its carrier transfers it to the first remaining clip of the unit. place_narration moves narration to a specified clip of an off-screen-speech unit, unloading the old carrier. BGM track: insert_bgm places BGM listed by list_bgm with a server-assigned ID (such as b2); start is absolute timeline time, source_in / source_out choose a range (whole track if omitted), default volume 0.25, default fade_in / fade_out 1 second each. set_bgm changes only supplied fields; delete_bgm removes it. BGM uses absolute positions and does not move when the main track changes. Only one track may sound at a time: changed BGM overlapping another BGM clip or starting outside the timeline returns operation_invalid. Rendering truncates BGM past timeline end and fades out for 1 second there; if summed fades exceed clip duration, shorten them proportionally. Upload already normalizes loudness; volume is a multiplier above it, rendered consistently at both outputs. Result contains only new revision, one-line message, new states of affected clips (read_timeline fields) and bgm, deleted_clip_ids (including BGM), total duration, and updated issues; not the full timeline."
    ),
    request_model=EditTimelineRequest,
    migration=BLOCKED,
    domain_key="timeline_edit",
    handler=edit_timeline,
    summary=timeline_write_summary,
)

RENAME_TIMELINE = ToolDeclaration(
    name="rename_timeline",
    description=(
        "Change an edit timeline’s display name; duplicates within an episode return timeline_name_conflict. Changes only the name: content, revision history, and timeline ID stay unchanged, no revision is created, and existing final cuts / Jianying drafts do not become stale."
    ),
    request_model=RenameTimelineRequest,
    migration=BLOCKED,
    domain_key="edit_timeline_summary",
    handler=rename_timeline,
    summary=timeline_renamed_summary,
)

LIST_REVISIONS = ToolDeclaration(
    name="list_revisions",
    description=(
        "List edit-timeline revision history oldest first: number, parent, author (creator, arcreel_agent, external_agent), summary, created_at, clip_count, changed_clip_ids (clips changed by the revision, null when unrecorded), and restored_from (the source revision number when rollback created this revision). Read-only, no side effects; missing ID returns timeline_not_found. Confirm revision numbers here before copying or restoring a revision."
    ),
    request_model=ListRevisionsRequest,
    migration=BLOCKED,
    domain_key="edit_timeline_revisions",
    handler=list_revisions,
    summary=revision_history_summary,
)

LIST_BGM = ToolDeclaration(
    name="list_bgm",
    description=(
        "List uploaded project BGM: id (such as bgm-3f9a0c21, used by edit_timeline insert_bgm), name, and duration in seconds, ordered by upload. BGM is project-wide and usable by every episode timeline. The creator uploads it via the BGM track in the editing view; if empty, ask the creator to upload first. Read-only, no side effects."
    ),
    request_model=ListBgmRequest,
    migration=BLOCKED,
    domain_key="bgm",
    handler=list_bgm,
    summary=bgm_list_summary,
)

RESTORE_REVISION = ToolDeclaration(
    name="restore_revision",
    description=(
        "Restore an edit timeline to an old revision by appending a new revision with that content (restored_from records the source), without rewriting history; the previously latest revision remains restorable. Restoration always replaces the entire current content on the latest revision, so use list_revisions and read_timeline first to confirm the target revision and current content. Returns revision_unchanged if target and latest contents match, or revision_not_found if absent. Later edit_timeline calls based on the pre-restore revision detect conflicts for clips changed by restoration. Result shape matches edit_timeline: new revision, message, affected clips in restored state, deleted_clip_ids (clips no longer present), duration, and issues."
    ),
    request_model=RestoreRevisionRequest,
    migration=BLOCKED,
    domain_key="timeline_edit",
    handler=restore_revision,
    summary=timeline_write_summary,
)

EDIT_TIMELINE_TOOLS = (
    CREATE_TIMELINE,
    LIST_TIMELINES,
    READ_TIMELINE,
    EDIT_TIMELINE,
    RENAME_TIMELINE,
    LIST_REVISIONS,
    RESTORE_REVISION,
    LIST_BGM,
)

__all__ = [
    "CREATE_TIMELINE",
    "EDIT_TIMELINE",
    "EDIT_TIMELINE_TOOLS",
    "LIST_BGM",
    "LIST_REVISIONS",
    "LIST_TIMELINES",
    "READ_TIMELINE",
    "RENAME_TIMELINE",
    "RESTORE_REVISION",
]
