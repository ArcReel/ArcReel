"""gate 领域错误 → HTTP 响应的共享映射。

审核 gate 的领域错误从两个 router 冒出来：``script_review``（审阅门自身的读写端点）与
``files``（通用草稿端点对参考生视频 step1 改道 ``ScriptReviewService``）。映射放在两者之外的
共享模块，两个 router 各自从这里取，同一个错误码在不同端点上不会给出不同的状态码或文案。
"""

from typing import NoReturn

from fastapi import HTTPException

from lib.i18n import Translator
from server.services.script_review import ScriptReviewError

# gate 领域错误码 → HTTP 状态。invalid_content / episode_not_found 带参数另行注入。
_ERROR_STATUS: dict[str, int] = {
    "not_applicable": 409,
    "no_step1": 409,
    "invalid_content": 422,
    "episode_not_found": 404,
    "quarantined": 409,
    "conflict": 409,
    "speech_admission": 409,
}
# 仅无参错误码走本映射；invalid_content / episode_not_found 需注参，在 raise_review_error 单独处理。
_ERROR_I18N: dict[str, str] = {
    "not_applicable": "script_review_not_applicable",
    "no_step1": "script_review_no_step1",
    "quarantined": "script_review_quarantined",
    "conflict": "script_review_conflict",
}


def raise_review_error(exc: ScriptReviewError, episode: int, _t: Translator) -> NoReturn:
    """把 ``ScriptReviewError`` 抛成对应的 ``HTTPException``；未登记的错误码落 400。"""
    status = _ERROR_STATUS.get(exc.code, 400)
    if exc.code == "speech_admission" and exc.admission is not None:
        detail = exc.admission.to_dict()
    elif exc.code == "invalid_content":
        detail = _t("script_review_invalid_content", details=exc.message)
    elif exc.code == "episode_not_found":
        detail = _t("episode_not_found", episode=episode)
    else:
        detail = _t(_ERROR_I18N.get(exc.code, "internal_server_error"))
    raise HTTPException(status_code=status, detail=detail)
