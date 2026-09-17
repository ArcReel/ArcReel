"""容量表：provider × media_type → 上限的三个装载路径（env / 声明默认 / DB）与三态 get。

与 ``test_generation_worker_module`` 同属生成 worker，但被测对象是 ``CapacityTable`` 自身——
调度、租约与孤儿扫描不经过它，故单独成文件。
"""

import pytest

from lib.generation_worker import CapacityTable
from tests.fakes import bind_safe_session_factory


class TestCapacityTable:
    """容量表：provider × media_type → 上限，三态 get + reload 只换数字。"""

    def test_get_three_states(self):
        table = CapacityTable(
            _limits={"known": {"image": 4, "video": 0}},
            _defaults={"image": 5, "video": 3},
        )
        # 已知 + lane 在表 → 登记值（含 0=不支持该 lane）
        assert table.get("known", "image") == 4
        assert table.get("known", "video") == 0
        # 已知 + lane 不在表 → 0
        assert table.get("known", "audio") == 0
        # 完全未知 → 默认，且纯查询不写回
        assert table.get("unknown", "image") == 5
        assert table.get("unknown", "video") == 3
        assert "unknown" not in table._limits

    def test_replace_only_swaps_numbers(self):
        table = CapacityTable(_limits={"p": {"image": 3, "video": 3}}, _defaults={"image": 5, "video": 3})
        table.replace({"p": {"image": 9, "video": 1}})
        assert table.get("p", "image") == 9
        assert table.get("p", "video") == 1
        # 默认不受 replace 影响
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
                if lane not in meta.media_types:
                    assert table.get(pid, lane) == 0  # 不支持的 lane → 投影为 0
                elif lane not in meta.default_concurrency:
                    # 未声明出厂默认的 lane 必须落到硬编码全局默认。这条断言不读
                    # meta.default_concurrency，故非重言——能抓住装载回退漂移。声明了默认的
                    # lane（如 agnes video）由该 provider 的专项测试钉死字面值；此处不用 meta
                    # 反推，否则对任何声明值都恒真，等于不设防。
                    assert table.get(pid, lane) == global_default

    def test_from_env_reads_env(self, monkeypatch):
        monkeypatch.setenv("IMAGE_MAX_WORKERS", "7")
        monkeypatch.setenv("VIDEO_MAX_WORKERS", "2")
        table = CapacityTable.from_env()
        # 未知 provider 的懒默认跟随 env
        assert table.get("unknown", "image") == 7
        assert table.get("unknown", "video") == 2

    async def test_from_db_known_providers_and_unsupported_lanes_zero(self):
        """from_db：所有 registry provider 已知，不支持的 lane 强制 0。

        只断言与 config 数值无关的确定不变量（已知 + 不支持→0），避免本地 dev DB
        的 config 覆盖导致 env 耦合。
        """
        from lib.config.registry import PROVIDER_REGISTRY

        table = await CapacityTable.from_db()
        for pid, meta in PROVIDER_REGISTRY.items():
            assert pid in table._limits
            if "image" not in meta.media_types:
                assert table.get(pid, "image") == 0
            if "video" not in meta.media_types:
                assert table.get(pid, "video") == 0

    @staticmethod
    def _stub_from_db_sources(
        monkeypatch,
        configs: dict[str, dict[str, str]],
        custom_providers=None,
        custom_endpoints=None,
    ) -> None:
        """把 from_db 的三个数据源（provider config / 自定义供应商 / 自定义端点）替换为内存数据。

        custom_providers：``list_providers_with_models`` 的返回值，形如
        ``[(provider, models), ...]``；默认空。
        custom_endpoints：``自增 id → custom_endpoint 行``，模型行挂 ``ce-`` 端点时按此解析；默认空。
        """
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
    def _fake_custom_provider(pid: str, *, image=None, video=None, audio=None, endpoints=("openai-images",)):
        """构造 ``(provider, models)`` 元组，供 list_providers_with_models 返回。"""
        from types import SimpleNamespace

        provider = SimpleNamespace(
            provider_id=pid,
            image_max_workers=image,
            video_max_workers=video,
            audio_max_workers=audio,
        )
        models = [SimpleNamespace(endpoint=ep, is_enabled=True) for ep in endpoints]
        return provider, models

    async def test_from_db_custom_provider_columns_used(self, monkeypatch):
        """自定义供应商：列有值 → 取列值（投影到其支持的 lane）。"""
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
        # 不支持的 lane（无 audio 模型）投影为 0
        assert table.get("custom-1", "audio") == 0

    async def test_from_db_custom_endpoint_model_takes_its_lane_from_the_definition(self, monkeypatch):
        """模型行挂 ce- 端点：lane 由解析出的端点 spec 的媒体类型决定，键前缀不蕴含媒体类型。"""
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

    async def test_from_db_skips_a_model_row_whose_endpoint_is_gone(self, monkeypatch):
        """端点解析不出来：不凭该行开 lane，其余供应商的容量照常刷新。"""
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
        """自定义供应商：列为 None → 回退全局默认（两层回退，无声明默认层）。"""
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

        # 全局默认 image=5 / video=3（env 已在 _stub 中清除）
        assert table.get("custom-9", "image") == 5
        assert table.get("custom-9", "video") == 3

    @pytest.mark.parametrize("dirty", ["", "3.7", "abc"])
    async def test_from_db_dirty_value_falls_back_per_key(self, monkeypatch, caplog, dirty):
        """单个 key 的存量脏值只回退该 key 默认值并告警，不拖垮整表加载。"""
        import logging

        self._stub_from_db_sources(
            monkeypatch,
            {
                "ark": {"image_max_workers": dirty, "video_max_workers": "2"},
                "gemini-aistudio": {"image_max_workers": "7"},
            },
        )

        with caplog.at_level(logging.WARNING, logger="lib.generation_worker"):
            table = await CapacityTable.from_db()

        # 脏 key 回退 env 默认值（image=5）并告警
        assert table.get("ark", "image") == 5
        assert "image_max_workers" in caplog.text
        # 同 provider 其余合法 key、其他 provider 的配置正常生效
        assert table.get("ark", "video") == 2
        assert table.get("gemini-aistudio", "image") == 7

    async def test_from_db_negative_value_clamps_to_zero(self, monkeypatch, caplog):
        """可解析的负数沿用 clamp 语义（→0），不视为脏值回退默认，但留告警可观测。"""
        import logging

        self._stub_from_db_sources(monkeypatch, {"ark": {"video_max_workers": "-1"}})

        with caplog.at_level(logging.WARNING, logger="lib.generation_worker"):
            table = await CapacityTable.from_db()

        assert table.get("ark", "video") == 0
        assert "video_max_workers" in caplog.text

    @staticmethod
    def _registry_with_declared_defaults(monkeypatch) -> None:
        """注入带 per-lane 默认并发声明的合成注册表，避免耦合任何真实供应商真值。

        - ``declared-video``：支持 image+video，声明 video 默认 1（声明默认 < 全局默认 3）。
        - ``declared-audio``：支持 audio，声明 audio 默认 12（声明默认 > 全局默认 10），覆盖
          audio lane 的正向回退（声明默认胜过全局默认）。
        - ``plain-video``：支持 video，无声明 → 走全局默认。
        - ``declared-unsupported``：仅支持 video，却声明 image 默认 → 该 lane 仍投影为 0。
        """
        from lib.config.registry import ModelInfo, ProviderMeta

        def _model(media_type: str) -> ModelInfo:
            return ModelInfo(display_name="M", media_type=media_type, capabilities=[])

        registry = {
            "declared-video": ProviderMeta(
                display_name="t",
                description="",
                required_keys=[],
                models={"img": _model("image"), "vid": _model("video")},
                default_concurrency={"video": 1},
            ),
            "declared-audio": ProviderMeta(
                display_name="t",
                description="",
                required_keys=[],
                models={"aud": _model("audio")},
                default_concurrency={"audio": 12},
            ),
            "plain-video": ProviderMeta(
                display_name="t",
                description="",
                required_keys=[],
                models={"vid": _model("video")},
            ),
            "declared-unsupported": ProviderMeta(
                display_name="t",
                description="",
                required_keys=[],
                models={"vid": _model("video")},
                default_concurrency={"image": 2},
            ),
        }
        monkeypatch.setattr("lib.config.registry.PROVIDER_REGISTRY", registry)

    def test_from_env_uses_registry_declared_default(self, monkeypatch):
        self._registry_with_declared_defaults(monkeypatch)
        monkeypatch.delenv("IMAGE_MAX_WORKERS", raising=False)
        monkeypatch.delenv("VIDEO_MAX_WORKERS", raising=False)
        monkeypatch.delenv("AUDIO_MAX_WORKERS", raising=False)

        table = CapacityTable.from_env()

        # 声明了 video=1 → 取声明默认，而非全局默认 3
        assert table.get("declared-video", "video") == 1
        # 同 provider 未声明的 image lane → 全局默认 5
        assert table.get("declared-video", "image") == 5
        # 支持 audio 且声明 audio=12 → 取声明默认（高于全局默认 10），audio lane 正向回退
        assert table.get("declared-audio", "audio") == 12
        # 未声明默认的 provider → 全局默认
        assert table.get("plain-video", "video") == 3
        # 声明了 image 默认但该 provider 不支持 image → 投影为 0（_lane_limits 不回归）
        assert table.get("declared-unsupported", "image") == 0

    async def test_from_db_declared_default_when_user_unconfigured(self, monkeypatch):
        """用户未配 → 取注册表声明默认（而非全局默认）；未声明 provider 仍走全局默认。"""
        self._registry_with_declared_defaults(monkeypatch)
        self._stub_from_db_sources(monkeypatch, {})

        table = await CapacityTable.from_db()

        assert table.get("declared-video", "video") == 1
        assert table.get("declared-video", "image") == 5
        assert table.get("declared-audio", "audio") == 12
        assert table.get("plain-video", "video") == 3
        assert table.get("declared-unsupported", "image") == 0

    async def test_from_db_user_value_overrides_declared_default(self, monkeypatch):
        """用户配了值 → 覆盖注册表声明默认。"""
        self._registry_with_declared_defaults(monkeypatch)
        self._stub_from_db_sources(monkeypatch, {"declared-video": {"video_max_workers": "4"}})

        table = await CapacityTable.from_db()

        assert table.get("declared-video", "video") == 4

    async def test_from_db_and_from_env_consistent_fallback(self, monkeypatch):
        """两条装载路径对同一回退语义（用户未配）逐 lane 结果一致。"""
        self._registry_with_declared_defaults(monkeypatch)
        self._stub_from_db_sources(monkeypatch, {})

        db_table = await CapacityTable.from_db()
        env_table = CapacityTable.from_env()

        for pid in ("declared-video", "declared-audio", "plain-video", "declared-unsupported"):
            for lane in ("image", "video", "audio"):
                assert db_table.get(pid, lane) == env_table.get(pid, lane)

    def test_agnes_video_default_one_outranks_active_global_env(self, monkeypatch):
        """真实注册表：Agnes 视频 lane 出厂钉死 1，即便部署把全局 VIDEO_MAX_WORKERS 调高也不解除。

        这是本切片要的 503 规避保证——声明默认压过「活跃的」全局 env（而非仅压过缺省值）。
        顺带验证两条隔离性质：声明只作用于 video lane（image 仍随全局默认）；钉死是 Agnes 专属，
        未声明默认的视频供应商（ark）仍跟随全局 env。机制本身（声明默认 > 全局、用户值 > 声明默认、
        跨 from_env/from_db 一致）已由上面的合成 declared-* 测试覆盖，此处只钉真实条目的接线。
        """
        monkeypatch.delenv("IMAGE_MAX_WORKERS", raising=False)
        monkeypatch.setenv("VIDEO_MAX_WORKERS", "5")

        table = CapacityTable.from_env()

        assert table.get("agnes", "video") == 1  # 声明默认压过活跃的全局 5
        assert table.get("agnes", "image") == 5  # 声明不外溢到未声明的 image lane
        assert table.get("ark", "video") == 5  # 未声明默认的视频供应商仍跟随全局 env
