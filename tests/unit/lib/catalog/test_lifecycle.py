"""Catalog lifecycle derives UTC-today status; candidates/defaults/invocation change with dates, without syncing."""

from __future__ import annotations

from datetime import date

import pytest

from arcreel_catalog_core import parse_catalog
from lib.catalog import query as catalog
from lib.catalog.builtin_models import disabled_without_state
from lib.catalog.errors import ModelDiscontinuedError, ModelNotInCatalogError
from lib.catalog.lifecycle import Lifecycle, entry_lifecycle
from tests.catalog_support import install_catalog, set_catalog_today

SHUTDOWN = date(2026, 11, 24)


def _image(name: str, **extra) -> dict:
    return {
        "maker": "acme",
        "name": name,
        "media_type": "image",
        "display_name": name.title(),
        "facts": {"text_to_image": True},
        **extra,
    }


_CATALOG = {
    "dataset_version": "2026-01-01T00:00:00Z",
    "entries": [
        _image("img-lite", shutdown_date=SHUTDOWN.isoformat(), successor="acme/img-pro"),
        _image("img-pro"),
        _image("img-old", discontinued=True),
        # Locally retained entry absent from every listing.
        _image("img-gone", shutdown_date="2026-01-31", successor="acme/img-pro"),
    ],
    "builtin_providers": {
        "acme": {
            "models": [
                {"api_model_name": "img-lite", "entry": "acme/img-lite"},
                {"api_model_name": "img-pro", "entry": "acme/img-pro"},
                {"api_model_name": "img-old", "entry": "acme/img-old"},
            ],
            "defaults": {"image": "img-lite"},
        },
    },
}


@pytest.fixture
def lifecycle_catalog(monkeypatch: pytest.MonkeyPatch) -> None:
    # Build validation disallows shutdown dates on defaults; instance reads allow them, simulating default expiration.
    install_catalog(monkeypatch, parse_catalog(_CATALOG))


@pytest.fixture
def today(monkeypatch: pytest.MonkeyPatch):
    """Injected clock returns a function representing configured today."""

    def _set(day: date) -> None:
        set_catalog_today(monkeypatch, day)

    return _set


def _entry(name: str):
    entry = catalog.find_entry(name, media_type="image")
    assert entry is not None
    return entry


@pytest.mark.usefixtures("lifecycle_catalog")
class TestEntryLifecycle:
    @pytest.mark.parametrize(
        ("day", "status"),
        [
            (date(2026, 11, 23), "retiring"),  # Future shutdown date.
            (SHUTDOWN, "discontinued"),  # Delist on shutdown day itself.
            (date(2026, 11, 25), "discontinued"),  # Shutdown date has passed.
        ],
    )
    def test_status_follows_the_shutdown_date(self, day, status):
        assert entry_lifecycle(_entry("img-lite"), day) == Lifecycle(status, SHUTDOWN, "acme/img-pro")

    def test_discontinued_flag_wins_without_a_date(self):
        assert entry_lifecycle(_entry("img-old"), date(2026, 1, 1)).status == "discontinued"

    def test_entry_without_lifecycle_or_without_entry_is_available(self):
        assert entry_lifecycle(_entry("img-pro"), SHUTDOWN) == Lifecycle("available")
        assert entry_lifecycle(None).status == "available"


@pytest.mark.usefixtures("lifecycle_catalog")
class TestCrossingTheShutdownDate:
    """Same process/catalog without sync: crossing into shutdown day immediately changes candidates/defaults/invocation."""

    def test_candidates_default_and_call_change_on_the_shutdown_day(self, today):
        today(date(2026, 11, 23))
        assert "img-lite" not in disabled_without_state("acme")
        assert catalog.default_model("acme", "image") == "img-lite"
        assert catalog.require_callable("acme", "img-lite", "image").display_name == "Img-Lite"
        view = next(v for v in catalog.provider_models("acme") if v.model_id == "img-lite")
        assert (view.lifecycle.status, view.model.hidden) == ("retiring", False)

        today(SHUTDOWN)
        assert "img-lite" in disabled_without_state("acme")
        # Delisted default acts as unspecified; do not substitute another model.
        assert catalog.default_model("acme", "image") is None
        with pytest.raises(ModelDiscontinuedError) as excinfo:
            catalog.require_callable("acme", "img-lite", "image")
        assert (excinfo.value.code, excinfo.value.params) == (
            "model_discontinued",
            {"provider": "acme", "model": "img-lite", "shutdown_date": "2026-11-24", "successor": "acme/img-pro"},
        )
        # Still present in provider listings, with status.
        view = next(v for v in catalog.provider_models("acme") if v.model_id == "img-lite")
        assert (view.lifecycle.status, view.model.hidden, view.model.default) == ("discontinued", True, False)

    def test_unlisted_reference_reports_the_retained_entry_lifecycle(self, today):
        today(SHUTDOWN)
        with pytest.raises(ModelDiscontinuedError) as excinfo:
            catalog.require_callable("acme", "img-gone", "image")
        assert excinfo.value.params["shutdown_date"] == "2026-01-31"
        assert excinfo.value.params["successor"] == "acme/img-pro"
        # Reference lacks even a local entry: report unlisted and ask reselection.
        with pytest.raises(ModelNotInCatalogError) as excinfo:
            catalog.require_callable("acme", "img-unknown", "image")
        assert excinfo.value.code == "model_not_in_provider_catalog"

    def test_discontinued_search_hits_follow_the_date(self, today):
        today(date(2026, 11, 23))
        assert next(h for h in catalog.search_entries("img-lite") if h.name == "img-lite").discontinued is False
        today(SHUTDOWN)
        assert next(h for h in catalog.search_entries("img-lite") if h.name == "img-lite").discontinued is True
