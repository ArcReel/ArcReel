"""Project-entry declarations: list/create projects and upload/edit source.

``source/`` is a protected Agent write path, written only through these two tools. Whole-source edits remap the episode ledger using alignment before and after the change."""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration, UnscopedToolDeclaration
from server.tool_runtime import (
    CreateProjectToolRequest,
    EditSourceTextRequest,
    NoArguments,
    UploadSourceRequest,
    create_project,
    edit_source_text,
    list_projects,
    upload_source,
)

LIST_PROJECTS = UnscopedToolDeclaration(
    name="list_projects",
    description=(
        "List ArcReel projects under the current projects root addressable by subsequent tools, returning name, title, content mode, and generation mode. Excludes directories missing project.json or unreadable directories. Read-only, no side effects."
    ),
    request_model=NoArguments,
    domain_key="projects",
    handler=list_projects,
)

CREATE_PROJECT = UnscopedToolDeclaration(
    name="create_project",
    description=(
        "Create an ArcReel project with complete metadata (title, content mode, generation mode, aspect ratio, narration delivery, etc.), returning normalized name and project.json content; subsequent tools address the project by name. Narration delivery defaults to post-production voiceover. With use_tts, omitted TTS model, voice, and rate are prefilled from global defaults and persisted as a project snapshot. Returns project_exists for an existing same-name project without overwriting. Ad / short-film projects (content_mode=ad) do not accept default_duration or storyboard grids; target_duration and brief are available only for ad / short-film projects. Rolls back the created project directory if metadata writing fails."
    ),
    request_model=CreateProjectToolRequest,
    domain_key="project",
    handler=create_project,
)

UPLOAD_SOURCE = ToolDeclaration(
    name="upload_source",
    description=(
        "Normalize source text to UTF-8, write it under project source/, and register it, returning project-relative path, detected original encoding, and chapter count. role=whole_source (default) appends a whole-source file to the list for episode planning; accepts only .txt / .md filenames, rejecting episode_N.txt names reserved for episode files. on_conflict controls existing same-name files. role=episode registers an episode with its own source: allocate a new episode ID, write source/episode_{episode ID}.txt, append in playback order, and also return the episode ID. Upload user-split episodes individually this way. For drama, source_kind indicates novel (default) or the user’s finished screenplay; other content modes ignore it. When on_conflict=replace overwrites a registered whole-source file, keep its list position and remap extracted episode source ranges by old/new text alignment. If extracted episodes are affected, write nothing initially; return confirmation_required=true, grouped affected episodes impact, and revision. Relay the list faithfully and retry with the same revision after confirmation. Reject if episodes to remove or retire have queued or running tasks. source/ can be written only by this tool and edit_source_text. Writes project files."
    ),
    request_model=UploadSourceRequest,
    migration=BLOCKED,
    domain_key="source",
    handler=upload_source,
)

EDIT_SOURCE_TEXT = ToolDeclaration(
    name="edit_source_text",
    description=(
        "Edit registered source: filename identifies one whole-source file; episode_id identifies source belonging to an own-source or no-source episode (writing converts no-source to own-source). Extracted episode text derives from whole source: edit the containing whole-source file instead. replacements replaces passages, each old_text appearing exactly once in current text; text rewrites the whole body. Whole-source edits remap affected extracted episodes by old/new alignment: unchanged episode text shifts only; changed text marks the episode source replanned; fully removed passages retire or remove episodes. If extracted episodes are affected, write nothing initially; return confirmation_required=true, grouped affected episodes impact, and revision. Relay the list faithfully and retry with the same revision after confirmation. Reject when source changed outside ArcReel and the episode ledger has not been updated in the Web Episodes view. The user deletes or reorders whole-source files in that view. Writes project files."
    ),
    request_model=EditSourceTextRequest,
    migration=BLOCKED,
    domain_key="source_change",
    handler=edit_source_text,
)

PROJECT_ENTRY_TOOLS = (LIST_PROJECTS, CREATE_PROJECT, UPLOAD_SOURCE, EDIT_SOURCE_TEXT)

__all__ = [
    "CREATE_PROJECT",
    "EDIT_SOURCE_TEXT",
    "LIST_PROJECTS",
    "PROJECT_ENTRY_TOOLS",
    "UPLOAD_SOURCE",
]
