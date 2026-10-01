"""内容确认领域错误到 HTTP 错误的映射。"""

from __future__ import annotations

import pytest

from lib.infra.api_errors import UnprocessableError
from server.routers._script_review_errors import raise_review_error
from server.services.project.script_review import ScriptReviewError


def test_unregistered_references_are_reported_per_item_to_the_user() -> None:
    with pytest.raises(UnprocessableError) as exc_info:
        raise_review_error(
            ScriptReviewError("unregistered_references", "E1S01: 王五; E1S02: 旧宅"), 1, lambda key, **_: key
        )

    assert exc_info.value.key == "script_review_unregistered_references"
    assert exc_info.value.params == {"details": "E1S01: 王五; E1S02: 旧宅"}
