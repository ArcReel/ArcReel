"""server.services.generation_tasks 测试共享的替身与 helper。"""

import json
from contextlib import contextmanager
from pathlib import Path

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifest,
    ProjectArtifactManifestAdapter,
)
from lib.config.resolver import ProviderModel
from lib.project_schema import CURRENT_PROJECT_SCHEMA_VERSION
from server.services import generation_tasks
from server.services.generation_context import AudioLaneResult, GenerationContext, ImageLaneResult, VideoLaneResult
from tests.fakes import persist_fake_script


def async_return(value):
    """Create an async function that always returns the given value (ignoring args)."""

    async def _inner(*args, **kwargs):
        return value

    return _inner


def fake_resolve_ctx(
    generator,
    *,
    image_provider=("openai", "gpt-image-2"),
    image_resolution=None,
    video_provider=("ark", "seedance"),
    video_backend_model=None,
    video_resolution="720p",
    supported_durations=(4, 6, 8),
    voice_consistency="soft",
    requested_generate_audio=True,
    seen_lane_requests=None,
):
    """lane 感知的假 resolve_generation_context：按调用方声明的 lane 拼装 frozen dataclass 产物。

    ``seen_lane_requests`` 传入 list 时记录每次调用声明的 lane 请求，供断言任务只声明
    自己用到的 lane。
    """

    async def _resolve(
        project_name, payload, *, project, user_id="default", episode=None, image=None, video=None, audio=None
    ):
        if seen_lane_requests is not None:
            seen_lane_requests.append({"image": image, "video": video, "audio": audio})
        image_lane = None
        if image is not None:
            provider, model = image_provider
            image_lane = ImageLaneResult(
                provider_model=ProviderModel(provider, model),
                backend_name=provider,
                backend_model=model,
                resolution=image_resolution,
            )
        video_lane = None
        if video is not None:
            provider, model = video_provider
            backend_model = video_backend_model or model
            video_lane = VideoLaneResult(
                provider_model=ProviderModel(provider, model),
                backend_name=provider,
                backend_model=backend_model,
                resolution=video_resolution,
                resolution_or_fallback=video_resolution or "720p",
                supported_durations=tuple(supported_durations),
                max_duration=None,
                max_reference_images=None,
                voice_consistency=voice_consistency,
                requested_generate_audio=requested_generate_audio,
            )
        audio_lane = None
        if audio is not None:
            audio_lane = AudioLaneResult(
                provider_model=ProviderModel("dashscope", "configured-tts"),
                backend_name="dashscope",
                backend_model="actual-tts",
                narration_voice="Cherry",
                narration_speed=1.1,
                voices=(),
            )
        return GenerationContext(
            generator=generator,
            image_lane=image_lane,
            video_lane=video_lane,
            audio_lane=audio_lane,
        )

    return _resolve


class _FakePM:
    def __init__(self, project_path: Path, *, register_script: bool = True):
        self.project_path = project_path
        self.project = {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
            "content_mode": "narration",
            "style": "Anime",
            "style_description": "cinematic",
            "characters": {
                "Alice": {
                    "description": "hero",
                    "character_sheet": "characters/Alice.png",
                    "reference_image": "characters/refs/Alice-ref.png",
                }
            },
            "scenes": {"祠堂": {"description": "temple", "scene_sheet": "scenes/祠堂.png"}},
            "props": {"玉佩": {"description": "jade", "prop_sheet": "props/玉佩.png"}},
            "products": {
                "保温杯": {
                    "description": "不锈钢保温杯",
                    "product_sheet": "",
                    "brand": "",
                    "reference_images": ["products/refs/保温杯_1.jpg", "products/refs/missing.jpg"],
                    "selling_points": [],
                }
            },
        }
        self.script = {
            "episode": 1,
            "content_mode": "narration",
            "segments": [
                {
                    "segment_id": "E1S01",
                    "duration_seconds": 4,
                    "segment_break": False,
                    "characters_in_segment": [],
                    "scenes": [],
                    "props": [],
                    "image_prompt": "首镜头",
                },
                {
                    "segment_id": "E1S02",
                    "duration_seconds": 4,
                    "segment_break": False,
                    "characters_in_segment": ["Alice"],
                    "scenes": ["祠堂"],
                    "props": ["玉佩"],
                    "image_prompt": {
                        "scene": "在雨夜街道",
                        "composition": {
                            "shot_type": "Medium Shot",
                            "lighting": "暖光",
                            "ambiance": "薄雾",
                        },
                    },
                },
                {
                    "segment_id": "E1S03",
                    "duration_seconds": 4,
                    "segment_break": True,
                    "characters_in_segment": ["Alice"],
                    "scenes": ["祠堂"],
                    "props": ["玉佩"],
                    "image_prompt": "切场后的镜头",
                },
            ],
        }
        self.updated_assets = []
        self.project_path.mkdir(parents=True, exist_ok=True)
        (self.project_path / "project.json").write_text(json.dumps(self.project, ensure_ascii=False), encoding="utf-8")
        scripts_dir = self.project_path / "scripts"
        scripts_dir.mkdir(exist_ok=True)
        (scripts_dir / "episode_1.json").write_text(
            json.dumps(self.script, ensure_ascii=False),
            encoding="utf-8",
        )
        if register_script:
            _register_episode_script_artifact(self.project_path)

    def load_project(self, project_name: str):
        return self.project

    def get_project_path(self, project_name: str):
        return self.project_path

    def load_script(self, project_name: str, script_file: str):
        persist_fake_script(self.project_path, script_file, self.script)
        return self.script

    def update_scene_asset(self, **kwargs):
        on_commit = kwargs.pop("on_commit", None)
        self.updated_assets.append(kwargs)
        if on_commit is not None:
            on_commit(self.project_path / "scripts" / kwargs["script_filename"])

    @contextmanager
    def locked_script(self, project_name, script_filename, *, validate=True, on_commit=None):
        yield self.script
        if on_commit is not None:
            on_commit(self.project_path / "scripts" / script_filename)

    def _set_scene_asset_in_script(self, script, scene_id, asset_type, asset_path):
        items, id_field, _kind = generation_tasks.resolve_items(script)
        item = next(candidate for candidate in items if str(candidate.get(id_field)) == str(scene_id))
        assets = item.setdefault("generated_assets", {})
        assets[asset_type] = asset_path
        assets["status"] = "storyboard_ready" if asset_type == "storyboard_image" else "completed"
        return item

    def save_project(self, project_name: str, project: dict):
        self.project = project

    def update_project(self, project_name: str, mutate_fn, *, on_commit=None):
        mutate_fn(self.project)
        if on_commit is not None:
            on_commit(self.project_path / "project.json")

    def project_exists(self, project_name: str) -> bool:
        return True

    def _update_asset_sheet(
        self, asset_type: str, project_name: str, name: str, sheet_path: str, *, on_commit=None
    ) -> dict:
        from lib.asset_types import ASSET_SPECS

        spec = ASSET_SPECS[asset_type]
        self.project.setdefault(spec.bucket_key, {}).setdefault(name, {})[spec.sheet_field] = sheet_path
        if on_commit is not None:
            on_commit(self.project_path / "project.json")
        return self.project

    def update_project_character_sheet(self, project_name: str, name: str, sheet_path: str) -> dict:
        self.project.setdefault("characters", {}).setdefault(name, {})["character_sheet"] = sheet_path
        return self.project


class FakeGenerator:
    def __init__(self, project_path: Path | None = None):
        # 传入项目目录时把产出落到产物的规范路径，让任务按生产口径登记清单
        self.project_path = project_path
        self.image_calls = []
        self.image_reference_bytes = []
        self.video_calls = []
        self.versions = self
        self.current_versions = {}

    def generate_image(self, **kwargs):
        self.image_calls.append(kwargs)
        return Path("/tmp/image.png"), 1

    async def generate_image_async(self, **kwargs):
        self.image_calls.append(kwargs)
        self._materialize_image(kwargs["resource_type"], kwargs["resource_id"])
        self.image_reference_bytes.append(
            [
                (reference["image"] if isinstance(reference, dict) else reference).read_bytes()
                for reference in kwargs.get("reference_images") or []
            ]
        )
        self.current_versions[(kwargs["resource_type"], kwargs["resource_id"])] = 1
        return Path("/tmp/image.png"), 1

    def _materialize_image(self, resource_type: str, resource_id: str) -> None:
        if self.project_path is None:
            return
        from lib.resource_paths import resource_relative_path

        target = self.project_path / resource_relative_path(resource_type, resource_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"png")

    def generate_video(self, **kwargs):
        self.video_calls.append(kwargs)
        return Path("/tmp/video.mp4"), 2, "ref", "uri"

    async def generate_video_async(self, **kwargs):
        self.video_calls.append(kwargs)
        return Path("/tmp/video.mp4"), 2, "ref", "uri"

    def get_versions(self, resource_type, resource_id):
        return {"versions": [{"version": 1, "created_at": "2026-01-01T00:00:00Z"}]}

    def get_current_version(self, resource_type, resource_id):
        return self.current_versions.get((resource_type, resource_id), 0)

    def reject_current_version(self, resource_type, resource_id, *, rejected_version, current_file, on_reject=None):
        key = (resource_type, resource_id)
        if self.current_versions.get(key) != rejected_version:
            return False
        self.current_versions[key] = 0
        if on_reject is not None:
            on_reject()
        return True


def prepare_files(tmp_path: Path):
    project_path = tmp_path / "projects" / "demo"
    (project_path / "storyboards").mkdir(parents=True, exist_ok=True)
    (project_path / "characters").mkdir(parents=True, exist_ok=True)
    (project_path / "characters" / "refs").mkdir(parents=True, exist_ok=True)
    (project_path / "scenes").mkdir(parents=True, exist_ok=True)
    (project_path / "props").mkdir(parents=True, exist_ok=True)
    (project_path / "storyboards" / "scene_E1S01.png").write_bytes(b"png")
    (project_path / "characters" / "Alice.png").write_bytes(b"png")
    (project_path / "characters" / "refs" / "Alice-ref.png").write_bytes(b"png")
    (project_path / "scenes" / "祠堂.png").write_bytes(b"png")
    (project_path / "props" / "玉佩.png").write_bytes(b"png")
    (project_path / "products" / "refs").mkdir(parents=True, exist_ok=True)
    (project_path / "products" / "refs" / "保温杯_1.jpg").write_bytes(b"jpg")
    return project_path


def _register_episode_script_artifact(project_path: Path) -> None:
    """Register episode 1's bound script in the Manifest — the only ledger of generated artifacts."""

    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
        ArtifactKey.episode_script(1),
        artifact_path="scripts/episode_1.json",
        basis=ArtifactBasis.build("test/episode-script", kind_version=1, inputs={}),
    )


def persist_active_fake_project(fake_pm: _FakePM, *, register_script: bool = True) -> None:
    """Persist the fake manager's mutated state back onto its schema-8 project on disk."""

    fake_pm.project.update(
        {
            "schema_version": CURRENT_PROJECT_SCHEMA_VERSION,
            "generation_mode": "storyboard",
            "aspect_ratio": "9:16",
            "episodes": [{"episode": 1, "script_file": "scripts/episode_1.json"}],
        }
    )
    fake_pm.script["episode"] = 1
    scripts_dir = fake_pm.project_path / "scripts"
    scripts_dir.mkdir(exist_ok=True)
    (fake_pm.project_path / "project.json").write_text(
        json.dumps(fake_pm.project, ensure_ascii=False),
        encoding="utf-8",
    )
    (scripts_dir / "episode_1.json").write_text(
        json.dumps(fake_pm.script, ensure_ascii=False),
        encoding="utf-8",
    )
    if register_script:
        _register_episode_script_artifact(fake_pm.project_path)


def seed_current_storyboard(fake_pm: _FakePM, resource_id: str = "E1S01") -> None:
    """Record one already-generated storyboard the way production does.

    A video task consumes the storyboard through the script pointer plus the Manifest
    claim; a file that merely sits under ``storyboards/`` is not a generated artifact.
    """

    artifact_path = f"storyboards/scene_{resource_id}.png"
    items, id_field, _kind = generation_tasks.resolve_items(fake_pm.script)
    item = next(candidate for candidate in items if str(candidate.get(id_field)) == resource_id)
    item.setdefault("generated_assets", {})["storyboard_image"] = artifact_path
    register_stale_visual_claim(
        fake_pm.project_path,
        ArtifactKey.episode_storyboard(1, resource_id),
        artifact_path,
    )


def register_asset_sheet_claims(fake_pm: _FakePM) -> None:
    """Register every asset sheet the fake project already has on disk.

    A sheet is only injectable once the Manifest claims it, so a fixture that wants
    references assembled has to record them the way a finished generation would.
    """

    from lib.asset_types import ASSET_SPECS

    for asset_type, spec in ASSET_SPECS.items():
        bucket = fake_pm.project.get(spec.bucket_key) or {}
        for name, entry in bucket.items():
            sheet = (entry or {}).get(spec.sheet_field)
            if isinstance(sheet, str) and sheet and (fake_pm.project_path / sheet).is_file():
                register_stale_visual_claim(
                    fake_pm.project_path,
                    ArtifactKey.asset_sheet(asset_type, name),
                    sheet,
                )


def build_currency_resolver(project_path: Path, project: dict):
    """Persist the project and build the resolver production hands the reference collectors."""

    from lib.artifact_activation import active_artifact_currency_resolver

    (project_path / "project.json").write_text(json.dumps(project, ensure_ascii=False), encoding="utf-8")
    return active_artifact_currency_resolver(project_path, project)


def register_stale_visual_claim(project_path: Path, key: ArtifactKey, artifact_path: str) -> None:
    ArtifactManifest(ProjectArtifactManifestAdapter(project_path)).register(
        key,
        artifact_path=artifact_path,
        basis=ArtifactBasis.build("test/visual-reference", kind_version=1, inputs={}),
    )


def ad_pm(project_path: Path, *, with_sheet: bool) -> _FakePM:
    """ad 项目 fixture：商品分镜 E1S02（引用保温杯）+ 氛围分镜 E1S01/E1S03。"""
    pm = _FakePM(project_path)
    pm.project["content_mode"] = "ad"
    if with_sheet:
        pm.project["products"]["保温杯"]["product_sheet"] = "products/保温杯.png"
    pm.script = {
        "episode": 1,
        "content_mode": "ad",
        "shots": [
            {
                "shot_id": "E1S01",
                "section": "hook",
                "duration_seconds": 4,
                "voiceover_text": "开场",
                "characters_in_shot": ["Alice"],
                "scenes": ["祠堂"],
                "props": [],
                "products_in_shot": [],
                "image_prompt": "氛围开场",
            },
            {
                "shot_id": "E1S02",
                "section": "product_reveal",
                "duration_seconds": 4,
                "voiceover_text": "商品亮相",
                "characters_in_shot": ["Alice"],
                "scenes": ["祠堂"],
                "props": [],
                "products_in_shot": ["保温杯"],
                "image_prompt": "商品特写",
            },
        ],
    }
    register_asset_sheet_claims(pm)
    return pm
