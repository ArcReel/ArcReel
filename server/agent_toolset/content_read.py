"""Project-content reading tool declarations."""

from __future__ import annotations

from server.agent_toolset.declaration import READ_CHECK, Exempt, ToolDeclaration
from server.tool_runtime import (
    EpisodeScriptRequest,
    NoArguments,
    ProjectFileRequest,
    ScriptPlanContentRequest,
    SourceTextRequest,
    get_episode_script,
    get_project_content,
    get_script_plan_content,
    get_source_text,
    list_project_files,
    list_source_files,
    read_project_file,
)

_READ_ONLY_FILE = Exempt(
    "Read-only direct project-file access by path, without artifact manifests or write credentials. Must remain readable for diagnosis and repair after migration failure."
)

GET_PROJECT_CONTENT = ToolDeclaration(
    name="get_project_content",
    description=(
        "Read the complete project.json (title, content mode, generation mode, style, episodes, asset definitions, etc.) and its canonical revision. episodes[] order is playback order; each episode value is an episode ID (an internal identifier, not its ordinal position). Read-only, no side effects."
    ),
    request_model=NoArguments,
    migration=_READ_ONLY_FILE,
    domain_key="project_content",
    handler=get_project_content,
)

LIST_SOURCE_FILES = ToolDeclaration(
    name="list_source_files",
    description=(
        "List source-text files under project source/ (UTF-8 .txt / .md), returning each file’s project-relative path, byte size, and etag, plus a revision for the full list. Read-only, no side effects. Use get_source_text for body text."
    ),
    request_model=NoArguments,
    migration=_READ_ONLY_FILE,
    domain_key="source_files",
    handler=list_source_files,
)

GET_SOURCE_TEXT = ToolDeclaration(
    name="get_source_text",
    description=(
        "Read the full text of one UTF-8 source file under source/, with revision and etag. Read-only, no side effects. Paths outside source/, non-text files, symlinks, and files over the size limit are rejected."
    ),
    request_model=SourceTextRequest,
    migration=_READ_ONLY_FILE,
    domain_key="source_text",
    handler=get_source_text,
)

GET_EPISODE_SCRIPT = ToolDeclaration(
    name="get_episode_script",
    description=(
        "Read an episode script’s JSON body and canonical revision. Pass the returned revision unchanged as patch_episode_script base_revision; reread after changes, as revision changes with the script. Read-only, no side effects. If project-data upgrade failed, no revision is issued: returns project_migration_failed; repair according to its details and call retry_project_migration."
    ),
    request_model=EpisodeScriptRequest,
    migration=READ_CHECK,
    domain_key="episode_script",
    handler=get_episode_script,
)

GET_SCRIPT_PLAN_CONTENT = ToolDeclaration(
    name="get_script_plan_content",
    description=(
        "Read the specified episode’s current formal script_plan body (the script-planning intermediate under drafts/episode_{episode ID}/) and canonical revision. Read-only, no side effects. Returns script_plan_not_applicable if the project does not use script_plan, or file_not_found if this episode has no script_plan yet."
    ),
    request_model=ScriptPlanContentRequest,
    migration=_READ_ONLY_FILE,
    domain_key="script_plan_content",
    handler=get_script_plan_content,
)

LIST_PROJECT_FILES = ToolDeclaration(
    name="list_project_files",
    description=(
        "List project business-text files available for diagnostic reading: project.json, source/ text, scripts/ scripts, and script_plan under drafts/episode_N/. Returns paths, byte sizes, etags, and a revision for the full list. Sensitive files, symlinks, and other paths are excluded. Read-only, no side effects."
    ),
    request_model=NoArguments,
    migration=_READ_ONLY_FILE,
    domain_key="project_files",
    handler=list_project_files,
)

READ_PROJECT_FILE = ToolDeclaration(
    name="read_project_file",
    description=(
        "Read a project business-text file with revision and etag: JSON files return parsed objects, text files return strings. The readable scope matches list_project_files; sensitive files, symlinks, out-of-bounds paths, and oversized files are rejected. Read-only, no side effects. Prefer dedicated readers when available (get_project_content, get_source_text, get_episode_script, get_script_plan_content)."
    ),
    request_model=ProjectFileRequest,
    migration=_READ_ONLY_FILE,
    domain_key="project_file",
    handler=read_project_file,
)

CONTENT_READ_TOOLS = (
    GET_PROJECT_CONTENT,
    LIST_SOURCE_FILES,
    GET_SOURCE_TEXT,
    GET_EPISODE_SCRIPT,
    GET_SCRIPT_PLAN_CONTENT,
    LIST_PROJECT_FILES,
    READ_PROJECT_FILE,
)

__all__ = ["CONTENT_READ_TOOLS"]
