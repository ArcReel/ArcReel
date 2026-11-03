"""图片后缀只在 ``lib.infra.image_utils`` 定义一处：导入按内容确定的后缀，上传校验与生成侧都认得。"""

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
    """导入保存用的后缀与手动上传接受的后缀同出一处：导入能存下的图片，上传同样接受。"""
    suffix = image_suffix(_encode(fmt))

    assert suffix == IMAGE_FORMAT_SUFFIXES[fmt][0]
    assert suffix in IMAGE_FILE_EXTENSIONS
    validate_upload(f"sheet{suffix}", None, kind="image")


def test_upload_and_file_serving_use_the_shared_suffixes():
    assert UPLOAD_IMAGE_EXTENSIONS == IMAGE_FILE_EXTENSIONS
    assert files._IMAGE_EXTS == IMAGE_FILE_EXTENSIONS
