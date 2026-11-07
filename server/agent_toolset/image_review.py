"""Image-inspection declaration: deliver asset sheets or storyboard images to the Agent as image content blocks."""

from __future__ import annotations

from server.agent_toolset.declaration import BLOCKED, ToolDeclaration
from server.media_tools.image_review import (
    MAX_IMAGE_EDGE,
    MAX_IMAGES_PER_CALL,
    InspectImagesRequest,
    inspect_images,
    inspect_images_images,
    inspect_images_projection,
    inspect_images_summary,
)

INSPECT_IMAGES = ToolDeclaration(
    name="inspect_images",
    description=(
        f"Inspect asset sheets or storyboard images: retrieve character, scene, prop, product, or character-derivative sheets, or storyboard images by ID, attaching image content blocks to results. Read-only, free, triggers no generation. Assess usability: defects, fidelity to definitions or prompts, and character consistency. One call covers one resource_type; version applies to every item, defaulting to each current version. Call separately per version to compare history. If default current has no version record but a current image is registered, inspection still works with null version and empty available_versions, without backfilling records. At most {MAX_IMAGES_PER_CALL} images per call; exceeding returns image_limit_exceeded, so batch calls. Images become JPEGs preserving aspect ratio, with longest edge at most {MAX_IMAGE_EDGE} px. Results images follow request order, each with id, version, available_versions (all version numbers), status, and on ok an image (one-based attachment index) plus prompt (recorded generation prompt; omitted if unrecorded). status ok means usable; image_missing means no image or absent version file; image_unreadable means reading/decoding failed, with detail explaining. Missing IDs return resource_not_found with params.ids; missing versions return version_not_found with params.available_versions."
    ),
    request_model=InspectImagesRequest,
    migration=BLOCKED,
    domain_key="inspect_images",
    handler=inspect_images,
    summary=inspect_images_summary,
    projection=inspect_images_projection,
    images=inspect_images_images,
)

IMAGE_REVIEW_TOOLS = (INSPECT_IMAGES,)

__all__ = ["IMAGE_REVIEW_TOOLS", "INSPECT_IMAGES"]
