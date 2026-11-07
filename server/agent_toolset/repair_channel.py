"""Repair-channel declarations: controlled writes still available after project-data upgrade failure, and rerunning the upgrade chain.

These write exemptions apply only to the Agent-tool repair channel, not REST: script-body write routes remain migration-blocked."""

from __future__ import annotations

from server.agent_toolset.declaration import Exempt, ToolDeclaration
from server.tool_runtime import (
    EPISODE_META_FIELDS,
    PROJECT_OVERVIEW_FIELDS,
    PROJECT_SETTINGS,
    MergeAssetRequest,
    NoArguments,
    PatchEpisodeMetaRequest,
    PatchProjectRequest,
    RenameAssetRequest,
    merge_asset,
    patch_episode_meta,
    patch_project,
    rename_asset,
    retry_project_migration,
)

_REPAIR_WRITE = Exempt(
    "Repair channel: after project-data upgrade failure, only controlled metadata writes may repair named episodes / files before retrying upgrades; "
    "writes undergo structural validation and do not touch script bodies."
)

PATCH_PROJECT = ToolDeclaration(
    name="patch_project",
    description=(
        f"Add or modify project.json using exactly one of three forms; multiple or no forms are rejected: (1) asset upsert (table + entries), keyed by asset table and name: add absent names, merge changed fields into existing names; (2) top-level settings (settings), allowlisted fields {list(PROJECT_SETTINGS)}, with null clearing the value; (3) project overview editing (overview), allowlisted fields {list(PROJECT_OVERVIEW_FIELDS)}, changing only supplied fields and creating the overview if absent. Invalid resulting structure is not persisted and returns a problem. Do not rename assets here (that creates a new name and leaves the old one); use rename_asset. Still available for repair after project-data upgrade failure."
    ),
    request_model=PatchProjectRequest,
    migration=_REPAIR_WRITE,
    domain_key="project_patch",
    handler=patch_project,
)

PATCH_EPISODE_META = ToolDeclaration(
    name="patch_episode_meta",
    description=(
        f"Edit top-level metadata of an episode script (not shot-level fields), allowlisted fields {list(EPISODE_META_FIELDS)}. The script’s top-level title is the sole source of truth for the episode title; edits automatically mirror into the project.json episode list. Use patch_episode_script for fields within shots. Still available for repair after project-data upgrade failure."
    ),
    request_model=PatchEpisodeMetaRequest,
    migration=_REPAIR_WRITE,
    domain_key="episode_meta_patch",
    handler=patch_episode_meta,
)

RENAME_ASSET = ToolDeclaration(
    name="rename_asset",
    description=(
        "Cascade-rename an asset in one operation: asset-table key, name references in all episode scripts and script_plan drafts (reference arrays / speaker / @[name] mentions), and associated name-based files and version history. A name conflict within the same table rejects everything without persistence. Renaming does not affect the global asset library. Always use this tool for asset renames, not patch_project. Still available for repair after project-data upgrade failure."
    ),
    request_model=RenameAssetRequest,
    migration=_REPAIR_WRITE,
    domain_key="asset_rename",
    handler=rename_asset,
)

MERGE_ASSET = ToolDeclaration(
    name="merge_asset",
    description=(
        "Merge two same-table assets representing the same identity (characters / scenes / props): redirect source references in all episode scripts and script_plan drafts (reference arrays / speaker / @[name] in bodies and visual descriptions) to target atomically. Keep target unchanged; add source’s name and aliases as target aliases, move source derivatives to target, and merge same-name derivatives into existing target derivatives. as_derivative=true (characters only) makes source a derivative of target: visual references become target/source, speaker becomes target, and source’s name is not added as an alias. Source description, asset sheet and version history, voice settings, original images, and reference audio are not merged; they move to project trash. Restoring them does not undo rewritten references. First use dry_run=true for per-episode impact, relay it, obtain user agreement, then execute. Use rename_asset for renaming. Still available for repair after project-data upgrade failure."
    ),
    request_model=MergeAssetRequest,
    migration=_REPAIR_WRITE,
    domain_key="asset_merge",
    handler=merge_asset,
)

RETRY_PROJECT_MIGRATION = ToolDeclaration(
    name="retry_project_migration",
    description=(
        "Rerun this project’s data-upgrade chain (including artifact backfill). Upgrade failure blocks the project; the only writing tools still available are patch_project / patch_episode_meta / rename_asset / merge_asset; patch_episode_script is always rejected. Use those tools to repair named episodes / files according to failure details, then call this tool. For locations they cannot change (such as script-body violations), report the blocker faithfully instead of retrying repeatedly. Idempotent: returns success directly when already current. Success returns a new workflow_plan; failure returns project_migration_failed with structured details (episode / file / violation) in params.details."
    ),
    request_model=NoArguments,
    migration=Exempt("The repair entry itself: rerunning upgrades is how migration blocking is lifted."),
    domain_key="migration_retry",
    handler=retry_project_migration,
)

REPAIR_CHANNEL_TOOLS = (PATCH_PROJECT, PATCH_EPISODE_META, RENAME_ASSET, MERGE_ASSET, RETRY_PROJECT_MIGRATION)

__all__ = [
    "MERGE_ASSET",
    "PATCH_EPISODE_META",
    "PATCH_PROJECT",
    "RENAME_ASSET",
    "REPAIR_CHANNEL_TOOLS",
    "RETRY_PROJECT_MIGRATION",
]
