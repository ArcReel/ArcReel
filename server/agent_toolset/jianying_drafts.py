"""Jianying-draft declaration: export an edit timeline as a Jianying draft."""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.jianying_drafts import (
    ExportJianyingDraftRequest,
    export_jianying_draft,
    jianying_draft_projection,
    jianying_draft_summary,
)

EXPORT_JIANYING_DRAFT = ToolDeclaration(
    name="export_jianying_draft",
    description=(
        "Export an edit timeline as a Jianying draft, generated locally without charges and registered as an artifact; the creator enters the local Jianying draft directory in the Web interface before downloading. Call only when the creator requests a Jianying draft export. The draft has a video track and subtitle track, plus a narration track for narrated variants. Overlapping narration or subtitles create additional tracks, with no overlap within each track; additional subtitle tracks shift upward. Trims, source volume, hold extensions, and transitions follow the edit timeline. BGM adds a separate track, with volume (upload-normalized loudness multiplied by clip volume) and fades recorded per clip. Check selected revision and narration variant before submission; reject without queueing for applicable blocking issues (such as video_missing), returning jianying_draft_blocked with params.issues; no exportable clips returns jianying_draft_empty; with_narration on non-TTS projects returns jianying_draft_narration_unavailable. Omit revision to export the latest revision at submission; edits during export or explicit selection of an old revision make the draft immediately stale but downloadable. Retain only the latest draft per timeline and narration variant; re-export overwrites it and increments version. Terminal jianying_draft includes revision, narration, version, artifact_path, duration (timeline seconds), and warnings (warning-level issues)."
    ),
    request_model=ExportJianyingDraftRequest,
    migration=BLOCKED,
    domain_key="jianying_draft",
    handler=export_jianying_draft,
    long_task=True,
    summary=jianying_draft_summary,
    projection=jianying_draft_projection,
)

JIANYING_DRAFT_TOOLS = (EXPORT_JIANYING_DRAFT,)

__all__ = ["EXPORT_JIANYING_DRAFT", "JIANYING_DRAFT_TOOLS"]
