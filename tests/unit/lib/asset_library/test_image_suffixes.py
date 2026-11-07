"""Image suffixes have one definition in ``lib.infra.image_utils``: import-by-content, upload validation, and generation agree."""

from __future__ import annotations

from io import BytesIO

import pytest
from PIL import Image

from lib.asset_library.importing import image_suffix
from lib.infra.image_utils import IMAGE_FILE_EXTENSIONS, IMAGE_FORMAT_SUFFIXES
from server.routers import files
from server.services.currency.upload_finalize import UPLOAD_IMAGE_EXTENSIONS, validate_upload


def _encode(fmt: str) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (4, 4), color="red").save(buf, format=fmt)
    return buf.getvalue()


def test_every_suffix_belongs_to_the_format_pil_assigns_it():
    registered = Image.registered_extensions()
    for fmt, suffixes in IMAGE_FORMAT_SUFFIXES.items():
        for suffix in suffixes:
            assert registered[suffix] == fmt


@pytest.mark.parametrize("fmt", list(IMAGE_FORMAT_SUFFIXES))
def test_imported_image_suffix_passes_upload_validation(fmt: str):
    """Import-save and upload-accepted suffixes share one source, so uploaded images accept every importable format."""
    suffix = image_suffix(_encode(fmt))

    assert suffix == IMAGE_FORMAT_SUFFIXES[fmt][0]
    assert suffix in IMAGE_FILE_EXTENSIONS
    validate_upload(f"sheet{suffix}", None, kind="image")


def test_upload_and_file_serving_use_the_shared_suffixes():
    assert UPLOAD_IMAGE_EXTENSIONS == IMAGE_FILE_EXTENSIONS
    assert files._IMAGE_EXTS == IMAGE_FILE_EXTENSIONS
