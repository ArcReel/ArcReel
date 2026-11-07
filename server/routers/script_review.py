"""Web content-confirmation routes from script_plan to prompt_authoring.

Expose plan review/edit/confirmation: generated plans are visible/editable in Web. Explicit user confirmation converts
the whole plan to a formal script with pending markers; Agent generate_episode_script then supplies visual layers
from that script. Drama (utterances + source_text) and narration (structured novel_text) share this mechanism."""

import logging

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, ConfigDict, Field

from lib.i18n import render_message
from lib.i18n.display_names import DisplayNames
from lib.infra.api_errors import NotFoundError
from lib.project.project_change_hints import project_change_source
from lib.project.project_manager import get_project_manager
from lib.script.script_review import overwrite_with_text
from server.dependencies import require_project_migration_ok
from server.i18n import DisplayNamesCatalog, Translator
from server.routers._script_review_errors import raise_review_error
from server.services.project.script_review import ScriptReviewError, ScriptReviewService

logger = logging.getLogger(__name__)

router = APIRouter()


async def _attach_duration_tiers(
    service: ScriptReviewService, project_name: str, episode: int, state: dict, _t: Translator, names: DisplayNames
) -> dict:
    """Attach per-unit tiers and localized degradation hints to state for all GET/PUT/POST endpoints. Otherwise save/confirm
    ``adopt()`` replaces GET’s narrowed results with a response missing ``duration_tiers``, making effective tiers
    indistinguishable from i2v failures.

    Let ``get_reference_duration_tiers`` decide by script_plan_kind, without short-circuiting on
    ``state["supported_durations"] is not None``. That separate method also returns None for unresolved models;
    using it as a gate would deprive such projects even of unnarrowed tiers."""
    content = state.get("content")
    units = content.get("units") if isinstance(content, dict) else None
    state["duration_tiers"] = await service.get_reference_duration_tiers(
        project_name, episode, units if isinstance(units, list) else ()
    )
    for soft in state.get("soft_violations") or []:
        soft["message"] = render_message(soft["code"], soft["params"], _t, names)
    return state


def _attach_overwrite_text(state: dict, _t: Translator) -> dict:
    """Attach server-rendered loss text to overwrite lists; Web confirmation displays only this text, sharing Agent responses’ source."""
    state["script_overwrite"] = overwrite_with_text(state.get("script_overwrite"), _t)
    return state


def localize_draft_view(view: dict | None, _t: Translator, names: DisplayNames) -> dict | None:
    """Localize fixed-message violations and degradation hints in draft views using ``_t``.

    ``quarantine_unreadable`` arises only from corrupt envelopes or missing/corrupt revalidation meta, both fixed strings
    without interpolation. Entries with ``params`` are video-request-fact failures, either episode-wide or located by
    ``item_id``; problem codes are translation keys, matching content-confirmation 422 presentation. Neither uses
    preinterpolated output-time templates of other ``lib.script.reference_video.draft_validation`` violation kinds;
    limit violation localization to these two categories. Degradation hints always use ``code`` as their translation key.
    Replace provider/model IDs in parameters with display names from ``names``."""
    if view is None:
        return None
    for violation in view["violations"]:
        if violation["code"] == "quarantine_unreadable":
            violation["message"] = _t("script_review_quarantine_unreadable")
        elif isinstance(violation.get("params"), dict):
            violation["message"] = render_message(violation["code"], violation["params"], _t, names)
    for soft in view.get("soft_violations") or []:
        soft["message"] = render_message(soft["code"], soft["params"], _t, names)
    return view


@router.get("/projects/{project_name}/episodes/{episode}/script-review")
async def get_script_review(project_name: str, episode: int, _t: Translator, names: DisplayNamesCatalog):
    """Read structured episode script_plan and confirmation state for Web rendering/editing.

    Merge ``quarantine`` separately, non-None only for reference_video with existing drafts. Revalidate on read using
    the original output validator, independently of ``get_state`` disk operations.
    Read ``quarantine`` before ``state``: if Agent promotion clears drafts and updates formal planning between reads,
    this order can pair new content with old violations. The panel conservatively blocks confirmation until the next
    poll corrects it. Reversing order could pair old content with quarantine None, falsely allow confirmation, and
    promote new content the user never reviewed."""
    try:
        service = ScriptReviewService(get_project_manager())
        quarantine = await service.get_quarantine_info(project_name, episode)
        state = await service.get_state(project_name, episode)
        await _attach_duration_tiers(service, project_name, episode, state, _t, names)
        _attach_overwrite_text(state, _t)
        state["quarantine"] = localize_draft_view(quarantine, _t, names)
        return state
    except ScriptReviewError as exc:
        raise_review_error(exc, episode, _t)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


@router.put("/projects/{project_name}/episodes/{episode}/script-review/content")
async def update_script_review_content(
    project_name: str,
    episode: int,
    _t: Translator,
    names: DisplayNamesCatalog,
    content: dict = Body(...),
    base_fingerprint: str | None = None,
):
    """Save manually/Agent-edited structured intermediate content and return the episode to pending confirmation.

    Query ``base_fingerprint`` is the editor’s GET content fingerprint. If supplied, compare under lock; competing
    script_plan writes during editing return 409 without persistence. Omission skips comparison for direct calls without baselines.

    Merge ``quarantine`` like GET: saving changes the formal planning document, independently of the repairable draft file.
    Agent generation may create a new draft during saving. Omitting this field makes ``adopt()`` infer no draft, falsely
    show clean state and allow confirmation, while confirm() still rejects draft existence with 409.

    Fetch ``quarantine`` immediately after saving, before ``_attach_duration_tiers`` awaits capability resolution. Promotion
    during that await may clear the draft; later reads increasingly risk pairing saved content with quarantine null, allowing
    confirmation of unseen promoted content. Like GET, read the relatively older conservative snapshot first: read skew may
    block confirmation for one extra poll, but cannot incorrectly authorize it."""
    try:
        service = ScriptReviewService(get_project_manager())
        with project_change_source("webui"):
            state = await service.save_content(project_name, episode, content, base_fingerprint)
        quarantine = await service.get_quarantine_info(project_name, episode)
        await _attach_duration_tiers(service, project_name, episode, state, _t, names)
        _attach_overwrite_text(state, _t)
        state["quarantine"] = localize_draft_view(quarantine, _t, names)
        return state
    except ScriptReviewError as exc:
        raise_review_error(exc, episode, _t)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc


class ConfirmScriptReviewRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overwrite_revision: str | None = Field(
        default=None,
        description="Formal-script revision accepted for overwrite, from script_overwrite.revision; unnecessary when the episode has no formal script",
    )


@router.post(
    "/projects/{project_name}/episodes/{episode}/script-review/confirm",
    dependencies=[Depends(require_project_migration_ok)],
)
async def confirm_script_review(
    project_name: str,
    episode: int,
    _t: Translator,
    names: DisplayNamesCatalog,
    req: ConfirmScriptReviewRequest | None = None,
):
    """Explicit user confirmation converts the whole script_plan to a formal script with all shots pending authoring, enabling prompt_authoring visuals.

    Existing formal scripts require ``overwrite_revision``, the overwrite list’s ``revision``. Missing/mismatched revisions
    return 409 with current removed-shot/artifact summaries in diagnostic ``script_overwrite``, writing neither scripts nor confirmation.

    Merge ``quarantine`` like GET/PUT for identical response shapes. Although ``confirm()`` rejects pending draft existence
    internally, responses must still accurately expose draft state at confirmation completion rather than inconsistently
    omitting the field across endpoints."""
    try:
        service = ScriptReviewService(get_project_manager())
        # Confirmation writes formal scripts/registers assets; without origin, filesystem broadcasts make frontend treat it as external and navigate to assets.
        with project_change_source("webui"):
            state = await service.confirm(
                project_name, episode, overwrite_revision=req.overwrite_revision if req is not None else None
            )
        await _attach_duration_tiers(service, project_name, episode, state, _t, names)
        _attach_overwrite_text(state, _t)
        state["quarantine"] = localize_draft_view(await service.get_quarantine_info(project_name, episode), _t, names)
        return state
    except ScriptReviewError as exc:
        raise_review_error(exc, episode, _t)
    except FileNotFoundError as exc:
        raise NotFoundError("project_not_found", name=project_name) from exc
