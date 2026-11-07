"""Capacity tables: provider × media_type limits through environment / declared defaults / DB loading, and three-state get.

Related to ``test_generation_worker_module`` but testing ``CapacityTable`` itself; scheduling, leases and orphan scans bypass it, so this has a separate file."""

import pytest

from lib.catalog import query as catalog
from lib.generation.generation_worker import CapacityTable
from tests.fakes import bind_safe_session_factory


class TestCapacityTable:
    """Capacity tables: provider × media_type limits, three-state get and reload changing numbers only."""

    def test_get_three_states(self):
        table = CapacityTable(
            _limits={"known": {"image": 4, "video": 0}},
            _defaults={"image": 5, "video": 3},
        )
        # Known provider with lane present: registered value, including zero for unsupported lanes.
        assert table.get("known", "image") == 4
        assert table.get("known", "video") == 0
        # Known provider with lane absent: zero.
        assert table.get("known", "audio") == 0
        # Entirely unknown provider: lazy default, without writing on pure lookup.
        assert table.get("unknown", "image") == 5
        assert table.get("unknown", "video") == 3
        assert "unknown" not in table._limits

    def test_replace_only_swaps_numbers(self):
        table = CapacityTable(_limits={"p": {"image": 3, "video": 3}}, _defaults={"image": 5, "video": 3})
        table.replace({"p": {"image": 9, "video": 1}})
        assert table.get("p", "image") == 9
        assert table.get("p", "video") == 1
        # replace does not change defaults.
        assert table.get("unknown", "image") == 5

    def test_from_env_derives_support_and_defaults(self, monkeypatch):
        from lib.config.registry import PROVIDER_REGISTRY

        monkeypatch.delenv("IMAGE_MAX_WORKERS", raising=False)
        monkeypatch.delenv("VIDEO_MAX_WORKERS", raising=False)
        monkeypatch.delenv("AUDIO_MAX_WORKERS", raising=False)
        table = CapacityTable.from_env()
        for pid, meta in PROVIDER_REGISTRY.items():
            assert pid in table._limits
            for lane, global_default in (("image", 5), ("video", 3), ("audio", 10)):
                if lane not in catalog.provider_media_types(pid):
                    assert table.get(pid, lane) == 0  # Unsupported lanes project to zero.
                elif lane not in meta.default_concurrency:
                    # Lanes without declared factory defaults use hardcoded global defaults. This assertion never reads
                    # meta.default_concurrency, avoiding tautology and catching load fallback drift. Provider-specific tests pin declared
                    # defaults such as agnes video; do not derive expectations from metadata here,
                    # which would accept every declaration and protect nothing.
                    assert table.get(pid, lane) == global_default

    def test_from_env_reads_env(self, monkeypatch):
        monkeypatch.setenv("IMAGE_MAX_WORKERS", "7")
        monkeypatch.setenv("VIDEO_MAX_WORKERS", "2")
        table = CapacityTable.from_env()
        # Unknown-provider lazy defaults follow environment.
        assert table.get("unknown", "image") == 7
        assert table.get("unknown", "video") == 2

    async def test_from_db_known_providers_and_unsupported_lanes_zero(self):
        """from_db knows every registry provider and forces unsupported lanes to zero. Assert configuration-independent invariants only (known + unsupported → zero), avoiding local development DB overrides coupling tests to environment."""
        from lib.config.registry import PROVIDER_REGISTRY

        table = await CapacityTable.from_db()
        for pid in PROVIDER_REGISTRY:
            assert pid in table._limits
            if "image" not in catalog.provider_media_types(pid):
                assert table.get(pid, "image") == 0
            if "video" not in catalog.provider_media_types(pid):
                assert table.get(pid, "video") == 0

    @staticmethod
    def _stub_from_db_sources(
        monkeypatch,
        configs: dict[str, dict[str, str]],
        custom_providers=None,
        custom_endpoints=None,
    ) -> None:
        """Replace from_db provider config / custom providers / custom endpoints with in-memory data.

        custom_providers is ``list_providers_with_models`` output, shaped ``[(provider, models), ...]``, empty by default. custom_endpoints maps ``自增 id → custom_endpoint 行`` for models bound to ``ce-`` endpoints, also empty by default."""
        from unittest.mock import AsyncMock, MagicMock

        for env in ("IMAGE_MAX_WORKERS", "VIDEO_MAX_WORKERS", "AUDIO_MAX_WORKERS"):
            monkeypatch.delenv(env, raising=False)

        class _FakeSessionCtx:
            async def __aenter__(self):
                return MagicMock()

            async def __aexit__(self, *exc_info):
                return False

        bind_safe_session_factory(monkeypatch, lambda: _FakeSessionCtx())
        monkeypatch.setattr(
            "lib.config.service.ConfigService.get_all_provider_configs",
            AsyncMock(return_value=configs),
        )
        monkeypatch.setattr(
            "lib.db.repositories.custom_provider_repo.CustomProviderRepository.list_providers_with_models",
            AsyncMock(return_value=custom_providers or []),
        )
        endpoint_rows = custom_endpoints or {}

        async def _get_endpoint(_self, endpoint_id: int):
            return endpoint_rows.get(endpoint_id)

        monkeypatch.setattr("lib.db.repositories.custom_endpoint_repo.CustomEndpointRepository.get", _get_endpoint)

    @staticmethod
    def _fake_custom_provider(
        pid: str,
        *,
        image=None,
        video=None,
        audio=None,
        endpoints=("openai-images",),
        discovery_format="openai",
    ):
        """Build ``(provider, models)`` tuples for list_providers_with_models."""
        from types import SimpleNamespace

        provider = SimpleNamespace(
            provider_id=pid,
            discovery_format=discovery_format,
            image_max_workers=image,
            video_max_workers=video,
            audio_max_workers=audio,
        )
        models = [SimpleNamespace(endpoint=ep, is_enabled=True) for ep in endpoints]
        return provider, models

    async def test_from_db_custom_provider_columns_used(self, monkeypatch):
        """Custom providers use nonempty columns, projected onto supported lanes."""
        self._stub_from_db_sources(
            monkeypatch,
            {},
            custom_providers=[
                self._fake_custom_provider(
                    "custom-1",
                    image=2,
                    video=7,
                    endpoints=("openai-images", "newapi-video"),
                )
            ],
        )

        table = await CapacityTable.from_db()

        assert table.get("custom-1", "image") == 2
        assert table.get("custom-1", "video") == 7
        # Unsupported lanes without audio models project to zero.
        assert table.get("custom-1", "audio") == 0

    async def test_from_db_custom_endpoint_model_takes_its_lane_from_the_definition(self, monkeypatch):
        """Models on ce- endpoints derive lanes from resolved spec media types, never endpoint-key prefixes."""
        from types import SimpleNamespace

        from tests.factories import custom_endpoint_definition

        self._stub_from_db_sources(
            monkeypatch,
            {},
            custom_providers=[
                self._fake_custom_provider(
                    "custom-2",
                    image=None,
                    video=4,
                    endpoints=("ce-7",),
                )
            ],
            custom_endpoints={7: SimpleNamespace(id=7, definition=custom_endpoint_definition())},
        )

        table = await CapacityTable.from_db()

        assert table.get("custom-2", "video") == 4
        assert table.get("custom-2", "image") == 0

    async def test_from_db_reads_the_endpoint_lane_through_the_real_repositories(self, monkeypatch, file_db_factory):
        """Exercise ce- lane projection with real DB/repositories. Other cases replace three sources with memory for speed but miss SQL/row shape; actual rows/repositories make query/column drift fail."""
        from lib.db.repositories.custom_endpoint_repo import CustomEndpointRepository
        from lib.db.repositories.custom_provider_repo import CustomProviderRepository
        from tests.factories import comfyui_endpoint_definition

        for env in ("IMAGE_MAX_WORKERS", "VIDEO_MAX_WORKERS", "AUDIO_MAX_WORKERS"):
            monkeypatch.delenv(env, raising=False)
        definition = comfyui_endpoint_definition(media_type="image")
        async with file_db_factory() as session:
            endpoint = await CustomEndpointRepository(session).create(
                definition=definition,
                kind="comfyui",
                schema_version=definition["schema_version"],
                media_type="image",
                display_name="我的画图 workflow",
            )
            await CustomProviderRepository(session).create_provider(
                display_name="我的 ComfyUI",
                discovery_format="comfyui",
                base_url="http://comfy.invalid:8188",
                api_key="",
                models=[
                    {
                        "model_id": "wan-i2i",
                        "display_name": "wan-i2i",
                        "endpoint": f"ce-{endpoint.id}",
                    }
                ],
                image_max_workers=6,
            )
            await session.commit()
        bind_safe_session_factory(monkeypatch, file_db_factory)

        table = await CapacityTable.from_db()

        # Endpoint definitions determine image lanes; key prefixes do not imply media types.
        assert table.get("custom-1", "image") == 6
        assert table.get("custom-1", "video") == 0

    async def test_from_db_comfyui_provider_defaults_each_lane_to_one(self, monkeypatch):
        """ComfyUI with NULL concurrency defaults to one image and one video, not global five/three. Usually one GPU serves an instance; additional concurrency only queues remotely."""
        from types import SimpleNamespace

        from tests.factories import comfyui_endpoint_definition

        image_definition = comfyui_endpoint_definition(media_type="image")
        self._stub_from_db_sources(
            monkeypatch,
            {},
            custom_providers=[
                self._fake_custom_provider(
                    "custom-9",
                    endpoints=("ce-1", "ce-2"),
                    discovery_format="comfyui",
                )
            ],
            custom_endpoints={
                1: SimpleNamespace(id=1, definition=comfyui_endpoint_definition()),
                2: SimpleNamespace(id=2, definition=image_definition),
            },
        )

        table = await CapacityTable.from_db()

        assert table.get("custom-9", "video") == 1
        assert table.get("custom-9", "image") == 1

    async def test_from_db_comfyui_provider_keeps_an_explicit_column(self, monkeypatch):
        """Explicitly increased user columns outrank protocol defaults, which apply only to NULL."""
        from types import SimpleNamespace

        from tests.factories import comfyui_endpoint_definition

        self._stub_from_db_sources(
            monkeypatch,
            {},
            custom_providers=[
                self._fake_custom_provider(
                    "custom-9",
                    video=3,
                    endpoints=("ce-1",),
                    discovery_format="comfyui",
                )
            ],
            custom_endpoints={1: SimpleNamespace(id=1, definition=comfyui_endpoint_definition())},
        )

        table = await CapacityTable.from_db()

        assert table.get("custom-9", "video") == 3

    async def test_from_db_other_protocols_keep_the_global_default(self, monkeypatch):
        """Protocol defaults belong only to comfyui; other custom-provider NULL columns use global defaults."""
        self._stub_from_db_sources(
            monkeypatch,
            {},
            custom_providers=[self._fake_custom_provider("custom-4", endpoints=("newapi-video",))],
        )

        table = await CapacityTable.from_db()

        assert table.get("custom-4", "video") == 3

    async def test_from_db_skips_a_model_row_whose_endpoint_is_gone(self, monkeypatch):
        """Unresolvable endpoints open no lane for that row; refresh other providers normally."""
        self._stub_from_db_sources(
            monkeypatch,
            {},
            custom_providers=[
                self._fake_custom_provider("custom-2", image=None, video=4, endpoints=("ce-7",)),
                self._fake_custom_provider("custom-3", image=None, video=5, endpoints=("newapi-video",)),
            ],
            custom_endpoints={},
        )

        table = await CapacityTable.from_db()

        assert table.get("custom-2", "video") == 0
        assert table.get("custom-3", "video") == 5

    async def test_from_db_custom_provider_null_falls_back_to_global_default(self, monkeypatch):
        """Custom-provider None columns fall back globally, with two fallback layers and no declared-default layer."""
        self._stub_from_db_sources(
            monkeypatch,
            {},
            custom_providers=[
                self._fake_custom_provider(
                    "custom-9",
                    image=None,
                    video=None,
                    endpoints=("openai-images", "newapi-video"),
                )
            ],
        )

        table = await CapacityTable.from_db()

        # Global image=5 / video=3 defaults; _stub cleared environment.
        assert table.get("custom-9", "image") == 5
        assert table.get("custom-9", "video") == 3

    @pytest.mark.parametrize("dirty", ["", "3.7", "abc"])
    async def test_from_db_dirty_value_falls_back_per_key(self, monkeypatch, caplog, dirty):
        """One dirty legacy key warns/falls back for itself without failing the whole table."""
        import logging

        self._stub_from_db_sources(
            monkeypatch,
            {
                "ark": {"image_max_workers": dirty, "video_max_workers": "2"},
                "gemini-aistudio": {"image_max_workers": "7"},
            },
        )

        with caplog.at_level(logging.WARNING, logger="lib.generation.generation_worker"):
            table = await CapacityTable.from_db()

        # Dirty keys warn and fall back to environment defaults, image=5.
        assert table.get("ark", "image") == 5
        assert "image_max_workers" in caplog.text
        # Other valid keys for this/other providers still apply normally.
        assert table.get("ark", "video") == 2
        assert table.get("gemini-aistudio", "image") == 7

    async def test_from_db_negative_value_clamps_to_zero(self, monkeypatch, caplog):
        """Parseable negatives retain clamp-to-zero semantics, not dirty-value fallback, but still warn."""
        import logging

        self._stub_from_db_sources(monkeypatch, {"ark": {"video_max_workers": "-1"}})

        with caplog.at_level(logging.WARNING, logger="lib.generation.generation_worker"):
            table = await CapacityTable.from_db()

        assert table.get("ark", "video") == 0
        assert "video_max_workers" in caplog.text

    @staticmethod
    def _registry_with_declared_defaults(monkeypatch) -> None:
        """Inject synthetic registry per-lane defaults instead of coupling to real providers.

        - ``declared-video`` supports image+video, declaring video one below global three.
        - ``declared-audio`` supports audio, declaring twelve above global ten, covering positive declared-over-global fallback.
        - ``plain-video`` supports video without declarations and uses global defaults.
        - ``declared-unsupported`` supports video only but declares image; that lane still projects zero."""
        from arcreel_catalog_core import AudioFacts, CatalogEntry, ImageFacts, VideoFacts
        from lib.config.registry import ProviderMeta
        from tests.catalog_support import catalog_of, install_catalog

        def _meta(**kwargs) -> ProviderMeta:
            return ProviderMeta(display_name="t", required_keys=[], **kwargs)

        registry = {
            "declared-video": _meta(default_concurrency={"video": 1}),
            "declared-audio": _meta(default_concurrency={"audio": 12}),
            "plain-video": _meta(),
            "declared-unsupported": _meta(default_concurrency={"image": 2}),
        }
        entries = (
            CatalogEntry(maker="t", name="img", media_type="image", display_name="M", facts=ImageFacts()),
            CatalogEntry(maker="t", name="vid", media_type="video", display_name="M", facts=VideoFacts(durations=(5,))),
            CatalogEntry(maker="t", name="aud", media_type="audio", display_name="M", facts=AudioFacts()),
        )
        providers = {
            "declared-video": ("t/img", "t/vid"),
            "declared-audio": ("t/aud",),
            "plain-video": ("t/vid",),
            "declared-unsupported": ("t/vid",),
        }
        monkeypatch.setattr("lib.generation.generation_worker.PROVIDER_REGISTRY", registry)
        install_catalog(monkeypatch, catalog_of(entries, providers))

    def test_from_env_uses_registry_declared_default(self, monkeypatch):
        self._registry_with_declared_defaults(monkeypatch)
        monkeypatch.delenv("IMAGE_MAX_WORKERS", raising=False)
        monkeypatch.delenv("VIDEO_MAX_WORKERS", raising=False)
        monkeypatch.delenv("AUDIO_MAX_WORKERS", raising=False)

        table = CapacityTable.from_env()

        # Declared video=1 overrides global default three.
        assert table.get("declared-video", "video") == 1
        # Undeclared image lane uses global default five.
        assert table.get("declared-video", "image") == 5
        # Supported audio with declared audio=12 beats global ten, covering positive audio fallback.
        assert table.get("declared-audio", "audio") == 12
        # Providers without declarations use global defaults.
        assert table.get("plain-video", "video") == 3
        # Declared image default on image-unsupported provider still projects zero, protecting _lane_limits.
        assert table.get("declared-unsupported", "image") == 0

    async def test_from_db_declared_default_when_user_unconfigured(self, monkeypatch):
        """Without user values, use registry defaults; undeclared providers still use global defaults."""
        self._registry_with_declared_defaults(monkeypatch)
        self._stub_from_db_sources(monkeypatch, {})

        table = await CapacityTable.from_db()

        assert table.get("declared-video", "video") == 1
        assert table.get("declared-video", "image") == 5
        assert table.get("declared-audio", "audio") == 12
        assert table.get("plain-video", "video") == 3
        assert table.get("declared-unsupported", "image") == 0

    async def test_from_db_user_value_overrides_declared_default(self, monkeypatch):
        """User values override registry-declared defaults."""
        self._registry_with_declared_defaults(monkeypatch)
        self._stub_from_db_sources(monkeypatch, {"declared-video": {"video_max_workers": "4"}})

        table = await CapacityTable.from_db()

        assert table.get("declared-video", "video") == 4

    async def test_from_db_and_from_env_consistent_fallback(self, monkeypatch):
        """Both load paths produce identical per-lane fallback when users have not configured values."""
        self._registry_with_declared_defaults(monkeypatch)
        self._stub_from_db_sources(monkeypatch, {})

        db_table = await CapacityTable.from_db()
        env_table = CapacityTable.from_env()

        for pid in ("declared-video", "declared-audio", "plain-video", "declared-unsupported"):
            for lane in ("image", "video", "audio"):
                assert db_table.get(pid, lane) == env_table.get(pid, lane)

    def test_agnes_video_default_one_outranks_active_global_env(self, monkeypatch):
        """Real registry: Agnes video declares factory default one even if deployment raises VIDEO_MAX_WORKERS.

        This guards against 503 by overriding active global environment, not merely its defaults. Also verify isolation: only video uses the declaration (image remains global), and undeclared Ark video remains global. Synthetic declared-* tests already cover mechanism (declared > global, user > declared, from_env/from_db consistency); here pin real-entry wiring."""
        monkeypatch.delenv("IMAGE_MAX_WORKERS", raising=False)
        monkeypatch.setenv("VIDEO_MAX_WORKERS", "5")

        table = CapacityTable.from_env()

        assert table.get("agnes", "video") == 1  # Declared default overrides active global five.
        assert table.get("agnes", "image") == 5  # Declarations do not spill into undeclared image lanes.
        assert table.get("ark", "video") == 5  # Undeclared video providers still follow global environment.
