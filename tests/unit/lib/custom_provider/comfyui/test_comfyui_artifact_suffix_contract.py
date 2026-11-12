"""前端使用记录详情按 media_type 现取的扩展名清单，与后端产物白名单同口径。"""

import json
from pathlib import Path

from lib.custom_provider.comfyui.artifacts import ARTIFACT_SUFFIXES_BY_MEDIA_TYPE, expected_suffixes_text

#: 前端 `COMFYUI_EXPECTED_SUFFIXES` 的契约文件，前端测试核对同一份文件。
_CONTRACT = (
    Path(__file__).resolve().parents[5] / "frontend" / "src" / "test" / "contracts" / "comfyui-artifact-suffixes.json"
)


def test_expected_suffixes_match_the_frontend_contract():
    contract = json.loads(_CONTRACT.read_text(encoding="utf-8"))
    assert contract == {
        media_type: expected_suffixes_text(media_type) for media_type in ARTIFACT_SUFFIXES_BY_MEDIA_TYPE
    }
