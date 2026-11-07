"""Shared mapping of content-confirmation domain errors to HTTP responses.

Content-confirmation errors surface through two routers: ``script_review`` (confirmation’s own read/write endpoints) and
``files`` (generic draft endpoints route reference-video script_plan operations through ``ScriptReviewService``).
Keep the mapping in a shared module outside both routers so identical error codes produce identical status codes and prose
across endpoints."""

from typing import NoReturn

from fastapi import HTTPException

from lib.infra.api_errors import ConflictError, UnprocessableError
from lib.script.script_review import overwrite_with_text
from server.i18n import Translator
from server.services.project.script_review import ScriptReviewError

# Gate domain error code → HTTP status. Inject parameters separately for invalid_content / episode_not_found.
_ERROR_STATUS: dict[str, int] = {
    "not_applicable": 409,
    "no_script_plan": 409,
    "invalid_content": 422,
    "episode_not_found": 404,
    "quarantined": 409,
    "conflict": 409,
    "speech_admission": 409,
}
# Map only parameterless codes here; raise_review_error handles invalid_content / episode_not_found parameters separately.
# Read-only rejection (script_plan_confirmed) and confirmation/conversion codes (overwrite_required / invalid_new_assets /
# unregistered_references / duration_off_tier / conversion_refused / conversion_conflict / video_request_facts / foreign_formal_script）
# carry diagnostics or special statuses and are likewise handled separately in raise_review_error.
_ERROR_I18N: dict[str, str] = {
    "not_applicable": "script_review_not_applicable",
    "no_script_plan": "script_review_no_script_plan",
    "quarantined": "script_review_quarantined",
    "conflict": "script_review_conflict",
}


def raise_review_error(exc: ScriptReviewError, episode: int, _t: Translator) -> NoReturn:
    """Raise the corresponding ``HTTPException`` for ``ScriptReviewError``; unregistered error codes map to 400."""
    if exc.code == "overwrite_required":
        raise ConflictError("script_review_overwrite_required").with_diagnostic(
            {"script_overwrite": overwrite_with_text(exc.overwrite, _t)}
        )
    if exc.code == "invalid_new_assets":
        raise UnprocessableError("script_review_invalid_new_assets").with_diagnostic(exc.message)
    if exc.code == "unregistered_references":
        raise UnprocessableError("script_review_unregistered_references", details=exc.message)
    if exc.code == "duration_off_tier":
        raise UnprocessableError("script_review_duration_off_tier", **exc.params).with_diagnostic(exc.message)
    if exc.code == "conversion_refused":
        raise UnprocessableError("script_review_conversion_refused").with_diagnostic(exc.message)
    if exc.code == "video_request_facts" and exc.problem is not None:
        # Video-request fact codes and parameters form the persisted failure contract, matching preflight and execution codes.
        raise UnprocessableError(exc.problem.code, **exc.problem.parameters())
    if exc.code == "foreign_formal_script":
        raise ConflictError("script_review_foreign_formal_script", episode=episode, filename=exc.script_filename or "")
    if exc.code == "script_plan_confirmed":
        raise ConflictError("script_review_script_plan_confirmed").with_diagnostic({"code": exc.code})
    if exc.code == "draft_agent_owned":
        raise ConflictError("draft_agent_owned").with_diagnostic({"code": exc.code})
    if exc.code == "conversion_conflict":
        raise ConflictError("script_conversion_conflict")
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
