"""Project-cover selection, computed on read.

When listing projects in the lobby, choose a relative resource path in this preference order:

    1. First frame of generated video, `video_thumbnail`.
       Stored in script-entry `generated_assets.video_thumbnail`: storyboard video uses
       `segments` (narration) / `scenes` (drama) / `shots` (ad), reference video uses `video_units`.
       Array keys come from skeleton registry `SKELETONS`.
       `lib/infra/thumbnail.extract_video_thumbnail` extracts/persists these after generation.
       Highest priority because it best represents current project output progress.

    2. Generated `storyboard_image`.
       Second choice when storyboard generation finished but video has not.
       Reference video always has None here, so it is naturally skipped.

    3. Scene reference image, `scene_sheet`.
       Key reference-video fallback: show the project’s art style even before any video exists.
       scene > character because environments/spatial context resemble covers more closely.

    4. Character reference image, `character_sheet`.
       Last fallback; only story-oriented projects may lack scenes while retaining characters.

    5. None, letting the frontend show a placeholder FolderOpen icon.

Like project-summary projection, compute on read without redundant storage: never write `cover_thumbnail`
to project.json; list_projects resolves current disk state on every call."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lib.infra.path_safety import try_safe_join
from lib.script.script_models import get_generated_assets
from lib.script.script_skeleton import SKELETONS

if TYPE_CHECKING:
    from lib.project.project_manager import ProjectManager

logger = logging.getLogger(__name__)


def resolve_project_cover(
    manager: ProjectManager,
    project_name: str,
    project: dict,
    *,
    preloaded_scripts: dict[str, dict] | None = None,
) -> str | None:
    """Choose the first available cover path by preference and return an `/api/v1/files/...` URL with ``?v=`` when present, otherwise None.

    ``preloaded_scripts`` lets callers such as list_projects load scripts once and also feed project-summary projection,
    avoiding duplicate JSON I/O. Keys are original ``episode['script_file']`` values and values are script JSON dicts;
    missing episodes (keys absent from the map) fall back to ``manager.load_script``."""

    def _url(rel: str) -> str:
        # Use the files static route rather than building disk paths directly; pass relative paths unchanged to FileResponse.
        # Existing files add ?v=<mtime_ns>, matching artifact fingerprints, so regenerated covers change URLs
        # for long-lived lobby caching. Missing/invalid paths omit version keys and let files return its normal 404.
        url = f"/api/v1/files/{project_name}/{rel.lstrip('/')}"
        try:
            project_dir = manager.get_project_path(project_name)
        except (OSError, ValueError):
            return url
        resolved = try_safe_join(project_dir, rel, require_file=True)
        if resolved is None:
            return url
        try:
            mtime_ns = resolved.stat().st_mtime_ns
        except OSError:
            return url
        return f"{url}?v={mtime_ns}"

    # Scan every episode script for video_thumbnail first, then scan all scripts for
    # storyboard_image. Separate passes rather than per-item alternatives ensure
    # any episode’s video first frame outranks storyboard-only images regardless of episode order.
    scripts: list[dict] = []
    for ep in project.get("episodes") or []:
        script_file = ep.get("script_file")
        if not script_file:
            continue
        if preloaded_scripts is not None and script_file in preloaded_scripts:
            scripts.append(preloaded_scripts[script_file])
            continue
        try:
            scripts.append(manager.load_script(project_name, script_file))
        except (OSError, ValueError) as e:
            # Missing/corrupt/out-of-bounds scripts must not block project lists; log debug and try other episodes.
            # OSError covers FileNotFoundError and other I/O failures; ValueError covers JSONDecodeError
            # and ProjectManager._safe_subpath path validation, matching list_projects outer
            # preload exception handling.
            logger.debug("加载剧本失败 project=%s script=%s err=%s", project_name, script_file, e)
            continue

    def _iter_items(script: dict) -> list[dict]:
        # Merge items from all SKELETONS shapes (segments / scenes / shots / video_units), rather than
        # selecting one parsed shape: coexisting keys or empty shells must not hide another shape’s real assets.
        # Skip nonlist arrays/nondict items as absent; asset paths require nonempty strings, ignoring empty shells and malformed values.
        items: list[dict] = []
        for kind in SKELETONS:
            raw = script.get(kind)
            if isinstance(raw, list):
                items.extend(item for item in raw if isinstance(item, dict))
        return items

    for script in scripts:
        for item in _iter_items(script):
            thumb = get_generated_assets(item).get("video_thumbnail")
            if isinstance(thumb, str) and thumb:
                return _url(thumb)

    for script in scripts:
        for item in _iter_items(script):
            sb = get_generated_assets(item).get("storyboard_image")
            if isinstance(sb, str) and sb:
                return _url(sb)

    # Project-level assets: scene > character, as explained in the module docstring.
    for data in (project.get("scenes") or {}).values():
        sheet = (data or {}).get("scene_sheet")
        if sheet:
            return _url(sheet)

    for data in (project.get("characters") or {}).values():
        sheet = (data or {}).get("character_sheet")
        if sheet:
            return _url(sheet)

    return None
