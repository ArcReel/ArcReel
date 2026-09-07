"""Tests for ad_product_fidelity."""

from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.services import generation_tasks
from tests.integration.server.services.generation_tasks_support import (
    FakeGenerator,
    ad_pm,
    async_return,
    build_currency_resolver,
    fake_resolve_ctx,
    prepare_files,
    seed_current_storyboard,
)


def _ref_paths(refs: list) -> list:
    return [r["image"] if isinstance(r, dict) else r for r in refs]


class TestAdProductFidelityStoryboard:
    """商品保真注入二元化——分镜层。"""

    def _patch(self, monkeypatch, pm, generator):
        monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
        monkeypatch.setattr(generation_tasks, "resolve_generation_context", fake_resolve_ctx(generator))
        monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", lambda *_a, **_kw: True)

    async def test_product_shot_injects_sheet_then_originals_before_other_sheets(self, tmp_path, monkeypatch):
        """有确认 sheet 的商品分镜：注入集为「sheet 多角度 + 原图压阵」，排序绝对优先于角色/场景 sheet。"""
        project_path = prepare_files(tmp_path)
        (project_path / "products" / "保温杯.png").write_bytes(b"png")
        pm = ad_pm(project_path, with_sheet=True)
        generator = FakeGenerator()
        self._patch(monkeypatch, pm, generator)

        await generation_tasks.execute_storyboard_task(
            "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "商品特写"}
        )

        refs = generator.image_calls[0]["reference_images"]
        paths = _ref_paths(refs)
        # 商品参考全量注入且排首位：sheet 在前、原图压阵，先于角色/场景 sheet
        assert [reference["kind"] for reference in refs[:2]] == ["sheet", "original"]
        assert [path.name for path in paths[:2]] == ["0000-保温杯.png", "0001-保温杯_1.jpg"]
        assert generator.image_reference_bytes[0][:2] == [b"png", b"jpg"]
        # 既有装配照常跟在商品参考之后（角色/场景 sheet）
        assert [path.name for path in paths[2:]] == ["0002-Alice.png", "0003-祠堂.png"]
        # 商品参考在声明行里按序位点名并附完全一致约束，参考图本身不带任何标签
        prompt = generator.image_calls[0]["prompt"]
        assert prompt.startswith(
            "Style: Anime\nVisual style: cinematic\n"
            "Reference_Images: 图1、图2为商品参考图，画面中的商品须与之完全一致；图3为角色参考图；图4为场景参考图。\n"
        )
        assert "\n\n商品特写\n\n" in prompt
        assert "保温杯" not in prompt

    async def test_product_shot_without_sheet_injects_originals_directly(self, tmp_path, monkeypatch):
        """无 sheet 的商品分镜：原图直注、仍排首位；声明但缺失的原图跳过。"""
        project_path = prepare_files(tmp_path)
        pm = ad_pm(project_path, with_sheet=False)
        generator = FakeGenerator()
        self._patch(monkeypatch, pm, generator)

        await generation_tasks.execute_storyboard_task(
            "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "商品特写"}
        )

        refs = generator.image_calls[0]["reference_images"]
        paths = _ref_paths(refs)
        assert refs[0]["kind"] == "original"
        assert paths[0].name == "0000-保温杯_1.jpg"
        assert generator.image_reference_bytes[0][0] == b"jpg"
        # 全量注入 = 存在的原图都进；声明的 missing.jpg 不指向任何文件，不出现
        assert all("missing" not in str(p) for p in paths)
        assert "Reference_Images: 图1为商品参考图，画面中的商品须与之完全一致；" in generator.image_calls[0]["prompt"]

    async def test_declaration_only_numbers_products_with_injected_references(self, tmp_path, monkeypatch):
        """声明行只为实际注入了参考图的商品编号：图全缺的商品不占序位，也不会凭空多出一张商品参考图。"""
        project_path = prepare_files(tmp_path)
        pm = ad_pm(project_path, with_sheet=False)
        pm.project["products"]["杯刷"] = {
            "description": "配套杯刷",
            "product_sheet": "",
            "brand": "",
            "reference_images": ["products/refs/不存在.jpg"],
            "selling_points": [],
        }
        pm.script["shots"][1]["products_in_shot"] = ["保温杯", "杯刷"]
        generator = FakeGenerator()
        self._patch(monkeypatch, pm, generator)

        await generation_tasks.execute_storyboard_task(
            "demo", "E1S02", {"script_file": "episode_1.json", "prompt": "双商品同框"}
        )

        prompt = generator.image_calls[0]["prompt"]
        assert (
            "Reference_Images: 图1为商品参考图，画面中的商品须与之完全一致；图2为角色参考图；图3为场景参考图。\n"
            in prompt
        )
        assert len(generator.image_calls[0]["reference_images"]) == 3

    async def test_atmosphere_shot_zero_product_images(self, tmp_path, monkeypatch):
        """氛围分镜（products_in_shot 为空）：零商品图，场景/角色 sheet 照常注入，prompt 无保真指令。"""
        project_path = prepare_files(tmp_path)
        (project_path / "products" / "保温杯.png").write_bytes(b"png")
        pm = ad_pm(project_path, with_sheet=True)
        generator = FakeGenerator()
        self._patch(monkeypatch, pm, generator)

        await generation_tasks.execute_storyboard_task(
            "demo", "E1S01", {"script_file": "episode_1.json", "prompt": "氛围开场"}
        )

        refs = generator.image_calls[0]["reference_images"]
        assert [reference["image"].name for reference in refs] == ["0000-Alice.png", "0001-祠堂.png"]
        assert generator.image_reference_bytes[0] == [b"png", b"png"]
        prompt = generator.image_calls[0]["prompt"]
        assert prompt.startswith(
            "Style: Anime\nVisual style: cinematic\nReference_Images: 图1为角色参考图；图2为场景参考图。\n"
        )
        assert "\n\n氛围开场\n\n" in prompt
        assert "商品参考图" not in prompt

    def test_collect_shot_product_references_skips_non_list_products_in_shot(self, tmp_path):
        """products_in_shot 为 str/dict 等非列表脏数据：跳过不抛，零商品参考（str 不得被逐字符迭代）。"""
        project_path = prepare_files(tmp_path)
        project = {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "products": {"保温杯": {"reference_images": ["products/refs/保温杯_1.jpg"]}},
        }
        resolver = build_currency_resolver(project_path, project)

        for dirty in ("保温杯", {"保温杯": True}, 7):
            item = {"shot_id": "E1S02", "products_in_shot": dirty}
            assert (
                generation_tasks.collect_shot_product_references(
                    project, project_path, item, currency_resolver=resolver
                )
                == []
            )

        # 缺失 / None / 空列表是氛围分镜的正常表达，同样返回空列表
        for empty in (None, []):
            item = {"shot_id": "E1S01", "products_in_shot": empty}
            assert (
                generation_tasks.collect_shot_product_references(
                    project, project_path, item, currency_resolver=resolver
                )
                == []
            )
        assert (
            generation_tasks.collect_shot_product_references(
                project, project_path, {"shot_id": "E1S01"}, currency_resolver=resolver
            )
            == []
        )

    def test_collect_product_references_resolves_nfd_registered_name_by_nfc_query(self, tmp_path):
        """商品以 NFD key 登记、分镜 products_in_shot 传入 NFC 名字：
        collect_product_references_for_names 须按归一形式查找 bucket 命中，不能因编码
        形式不同静默跳过。"""
        import unicodedata

        project_path = prepare_files(tmp_path)
        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        assert name_nfc != name_nfd
        project = {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "products": {name_nfd: {"reference_images": ["products/refs/保温杯_1.jpg"]}},
        }

        refs = generation_tasks.collect_product_references_for_names(
            project,
            project_path,
            [name_nfc],
            currency_resolver=build_currency_resolver(project_path, project),
        )
        assert [r["image"] for r in refs] == [project_path / "products" / "refs" / "保温杯_1.jpg"]

    def test_collect_product_references_dedupes_nfc_nfd_pair(self, tmp_path):
        """同一商品的 NFC/NFD 两种编码形式同时出现在 products_in_shot：归一后是同一商品，
        只应注入一份参考图，不能各自命中同一 bucket 条目各注入一份，否则会重复消耗参考位、
        挤掉真正的角色/场景参考。"""
        import unicodedata

        project_path = prepare_files(tmp_path)
        name_nfc = unicodedata.normalize("NFC", "Hiếu")
        name_nfd = unicodedata.normalize("NFD", "Hiếu")
        assert name_nfc != name_nfd
        project = {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "products": {name_nfd: {"reference_images": ["products/refs/保温杯_1.jpg"]}},
        }

        refs = generation_tasks.collect_product_references_for_names(
            project,
            project_path,
            [name_nfc, name_nfd],
            currency_resolver=build_currency_resolver(project_path, project),
        )
        assert [r["image"] for r in refs] == [project_path / "products" / "refs" / "保温杯_1.jpg"]


def _patch_video_path(monkeypatch, pm, generator):
    monkeypatch.setattr(generation_tasks, "get_project_manager", lambda: pm)
    monkeypatch.setattr(generation_tasks, "resolve_generation_context", fake_resolve_ctx(generator))
    monkeypatch.setattr(generation_tasks, "register_current_resource_artifact", lambda *_a, **_kw: True)
    monkeypatch.setattr(generation_tasks, "extract_video_thumbnail", async_return(None))
    monkeypatch.setattr(generation_tasks, "emit_project_change_batch", lambda *a, **kw: None)


class TestAdProductVideoRequest:
    """商品分镜的视频请求走纯图生视频：分镜图作首帧，不带参考图。"""

    async def test_product_shot_video_request_carries_no_reference_images(self, tmp_path, monkeypatch):
        project_path = prepare_files(tmp_path)
        (project_path / "products" / "保温杯.png").write_bytes(b"png")
        (project_path / "storyboards" / "scene_E1S02.png").write_bytes(b"png")
        pm = ad_pm(project_path, with_sheet=True)
        seed_current_storyboard(pm, "E1S02")
        generator = FakeGenerator()
        _patch_video_path(monkeypatch, pm, generator)

        result = await generation_tasks.execute_video_task(
            "demo",
            "E1S02",
            {
                "script_file": "episode_1.json",
                "prompt": {"action": "举起保温杯", "camera_motion": "Static", "dialogue": []},
                "duration_seconds": 4,
            },
        )

        assert result["resource_type"] == "videos"
        call = generator.video_calls[0]
        assert "reference_images" not in call
        assert call["start_image"] == project_path / "storyboards" / "scene_E1S02.png"
        # 参考缺席时不附商品保真指令（指令指向参考图，会误导模型）
        assert "高保真" not in call["prompt"]
