from __future__ import annotations

import unicodedata
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest

from lib.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifest,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
    compose_video_artifact_basis,
)
from lib.reference_video.request_projection import ResolvedReferenceAsset
from lib.script_models import ReferenceResource
from lib.visual_artifact_provenance import (
    GridStoryboardVisual,
    VisualReference,
    build_asset_sheet_visual_basis,
    build_grid_composite_visual_basis,
    build_grid_member_storyboard_visual_basis,
    build_reference_video_artifact_visual_basis,
    build_storyboard_image_visual_basis,
    build_storyboard_video_artifact_visual_basis,
)

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("asset_type", ["character", "scene", "prop", "product"])
def test_asset_sheet_visual_basis_tracks_only_formal_visual_inputs(tmp_path: Path, asset_type: str) -> None:
    first_reference = tmp_path / "first.png"
    same_reference = tmp_path / "same.png"
    changed_reference = tmp_path / "changed.png"
    first_reference.write_bytes(b"same visual bytes")
    same_reference.write_bytes(b"same visual bytes")
    changed_reference.write_bytes(b"changed visual bytes")

    def build(reference: Path, *, description: str = "银发旅人", aspect_ratio: str = "16:9", style: str = "水墨"):
        return build_asset_sheet_visual_basis(
            asset_type=asset_type,
            asset_id="Hiếu",
            description=description,
            style=style,
            style_description="淡彩",
            aspect_ratio=aspect_ratio,
            references=(
                VisualReference(
                    path=reference,
                    role="source",
                    logical_type=asset_type,
                    logical_id="Hiếu",
                    kind="original",
                ),
            ),
        )

    baseline = build(first_reference)

    assert build(same_reference).digest == baseline.digest
    assert build(changed_reference).digest != baseline.digest
    assert build(first_reference, description="黑发旅人").digest != baseline.digest
    assert build(first_reference, aspect_ratio="9:16").digest != baseline.digest
    if asset_type == "product":
        assert build(first_reference, style="写实摄影").digest == baseline.digest
    else:
        assert build(first_reference, style="写实摄影").digest != baseline.digest


def test_asset_sheet_visual_basis_uses_canonical_asset_identity(tmp_path: Path) -> None:
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"visual")
    nfc_name = unicodedata.normalize("NFC", "Hiếu")
    nfd_name = unicodedata.normalize("NFD", nfc_name)

    first = build_asset_sheet_visual_basis(
        asset_type="character",
        asset_id=nfc_name,
        description="主角",
        style="",
        style_description="",
        aspect_ratio="16:9",
        references=(
            VisualReference(
                path=reference,
                role="source",
                logical_type="character",
                logical_id=nfc_name,
            ),
        ),
    )
    same = build_asset_sheet_visual_basis(
        asset_type="character",
        asset_id=nfd_name,
        description="主角",
        style="",
        style_description="",
        aspect_ratio="16:9",
        references=(
            VisualReference(
                path=reference,
                role="source",
                logical_type="character",
                logical_id=nfd_name,
            ),
        ),
    )

    assert nfc_name != nfd_name
    assert same.digest == first.digest


def test_visual_reference_requires_real_image_evidence(tmp_path: Path) -> None:
    missing = tmp_path / "missing.png"
    reference = VisualReference(path=missing, role="source")

    with pytest.raises(FileNotFoundError):
        build_asset_sheet_visual_basis(
            asset_type="character",
            asset_id="阿黎",
            description="主角",
            style="",
            style_description="",
            aspect_ratio="16:9",
            references=(reference,),
        )


def test_storyboard_image_basis_projects_content_canvas_and_actual_references(tmp_path: Path) -> None:
    character_sheet = tmp_path / "character.png"
    changed_sheet = tmp_path / "changed-character.png"
    character_sheet.write_bytes(b"character-v1")
    changed_sheet.write_bytes(b"character-v2")

    def build(
        *,
        image_prompt: object | None = None,
        style: str = "画风：水墨",
        aspect_ratio: str = "16:9",
        sheet: Path = character_sheet,
    ):
        return build_storyboard_image_visual_basis(
            resource_id="E1S01",
            image_prompt=image_prompt
            or {
                "scene": "阿黎站在雨中",
                "composition": {
                    "shot_type": "Medium Shot",
                    "lighting": "冷光",
                    "ambiance": "压抑",
                },
            },
            style=style,
            aspect_ratio=aspect_ratio,
            references=(
                VisualReference(
                    path=sheet,
                    role="asset_sheet",
                    logical_type="character",
                    logical_id="阿黎",
                ),
            ),
        )

    baseline = build()

    assert build(style="水墨").digest == baseline.digest
    assert build(sheet=changed_sheet).digest != baseline.digest
    assert build(aspect_ratio="9:16").digest != baseline.digest
    assert (
        build(
            image_prompt={
                "scene": "阿黎走出雨幕",
                "composition": {
                    "shot_type": "Medium Shot",
                    "lighting": "冷光",
                    "ambiance": "压抑",
                },
            }
        ).digest
        != baseline.digest
    )


def test_legacy_storyboard_text_ignores_style_that_is_not_sent_to_the_request(tmp_path: Path) -> None:
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"visual")
    kwargs = {
        "resource_id": "E1S01",
        "image_prompt": "阿黎站在雨中",
        "aspect_ratio": "16:9",
        "references": (VisualReference(path=reference, role="asset_sheet"),),
    }

    first = build_storyboard_image_visual_basis(style="水墨", **kwargs)
    changed_style = build_storyboard_image_visual_basis(style="写实", **kwargs)

    assert changed_style.digest == first.digest


def _grid_members(*, second_image: str = "雨巷", first_action: str = "阿黎转身") -> tuple[GridStoryboardVisual, ...]:
    return (
        GridStoryboardVisual(
            resource_id="E1S01",
            image_prompt={"scene": "屋顶", "composition": {"shot_type": "Wide Shot"}},
            video_prompt={"action": first_action, "camera_motion": "Pan Left", "ambiance_audio": "雨声"},
        ),
        GridStoryboardVisual(
            resource_id="E1S02",
            image_prompt={"scene": second_image, "composition": {"shot_type": "Medium Shot"}},
            video_prompt={"action": "推门", "camera_motion": "Static", "ambiance_audio": "脚步声"},
        ),
        GridStoryboardVisual(
            resource_id="E1S03",
            image_prompt={"scene": "门厅", "composition": {"shot_type": "Close-up"}},
            video_prompt={"action": "灯熄灭", "camera_motion": "Static", "ambiance_audio": "钟声"},
        ),
    )


def test_grid_composite_and_member_bases_keep_member_changes_local(tmp_path: Path) -> None:
    group_reference = tmp_path / "character.png"
    composite = tmp_path / "grid.png"
    group_reference.write_bytes(b"character")
    composite.write_bytes(b"grid-v1")
    references = (
        VisualReference(
            path=group_reference,
            role="asset_sheet",
            logical_type="character",
            logical_id="阿黎",
        ),
    )
    members = _grid_members()
    changed_second = _grid_members(second_image="雪巷")

    def composite_basis(current_members: tuple[GridStoryboardVisual, ...]):
        return build_grid_composite_visual_basis(
            group_id="grid_1",
            members=current_members,
            rows=2,
            columns=2,
            style="水墨",
            grid_aspect_ratio="1:1",
            references=references,
        )

    def member_basis(current_members: tuple[GridStoryboardVisual, ...], cell_index: int):
        return build_grid_member_storyboard_visual_basis(
            group_id="grid_1",
            members=current_members,
            cell_index=cell_index,
            composite_image=composite,
            rows=2,
            columns=2,
            style="水墨",
            member_aspect_ratio="16:9",
            references=references,
        )

    group = composite_basis(members)
    first = member_basis(members, 0)
    second = member_basis(members, 1)

    assert composite_basis(changed_second).digest != group.digest
    assert member_basis(changed_second, 0).digest == first.digest
    assert member_basis(changed_second, 1).digest != second.digest


def test_grid_member_tracks_transition_source_and_replaced_composite(tmp_path: Path) -> None:
    composite = tmp_path / "grid.png"
    composite.write_bytes(b"grid-v1")
    members = _grid_members()

    def build(current_members: tuple[GridStoryboardVisual, ...], cell_index: int):
        return build_grid_member_storyboard_visual_basis(
            group_id="grid_1",
            members=current_members,
            cell_index=cell_index,
            composite_image=composite,
            rows=2,
            columns=2,
            style="水墨",
            member_aspect_ratio="16:9",
        )

    first = build(members, 0)
    second = build(members, 1)
    changed_transition = _grid_members(first_action="阿黎跃下屋顶")

    assert build(changed_transition, 0).digest == first.digest
    assert build(changed_transition, 1).digest != second.digest

    composite.write_bytes(b"grid-v2")

    assert build(members, 0).digest != first.digest
    assert build(members, 1).digest != second.digest


def test_grid_composite_ignores_last_action_that_is_not_rendered(tmp_path: Path) -> None:
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"visual")
    members = _grid_members()
    changed_last = (*members[:2], replace(members[2], video_prompt={"action": "另一动作"}))

    def build(current_members: tuple[GridStoryboardVisual, ...]):
        return build_grid_composite_visual_basis(
            group_id="grid_1",
            members=current_members,
            rows=2,
            columns=2,
            style="水墨",
            grid_aspect_ratio="1:1",
            references=(VisualReference(path=reference, role="asset_sheet"),),
        )

    assert build(changed_last).digest == build(members).digest


def test_grid_composite_tracks_only_the_aspect_ratio_sent_for_the_composite(tmp_path: Path) -> None:
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"visual")
    common = {
        "group_id": "grid_1",
        "members": _grid_members(),
        "rows": 2,
        "columns": 2,
        "style": "水墨",
        "references": (VisualReference(path=reference, role="asset_sheet"),),
    }

    landscape = build_grid_composite_visual_basis(grid_aspect_ratio="16:9", **common)
    portrait = build_grid_composite_visual_basis(grid_aspect_ratio="9:16", **common)

    assert portrait.digest != landscape.digest


def test_storyboard_video_visual_basis_excludes_sound_execution_and_duration(tmp_path: Path) -> None:
    start = tmp_path / "start.png"
    end = tmp_path / "end.png"
    start.write_bytes(b"start-frame")
    end.write_bytes(b"end-frame")
    prompt = {
        "action": "阿黎推开门",
        "camera_motion": "Push In",
        "ambiance_audio": "雨声",
        "dialogue": [{"speaker": "阿黎", "line": "有人吗"}],
        "voice_profiles": [{"Speaker": "阿黎", "Voice_Style": "低沉"}],
        "provider": "ignored-provider",
        "resolution": "1080p",
        "seed": 7,
        "duration_seconds": 8,
    }

    def build(*, current_prompt: object = prompt, current_start: Path = start, aspect_ratio: str = "16:9"):
        return build_storyboard_video_artifact_visual_basis(
            resource_id="E1S01",
            visual_prompt=current_prompt,
            storyboard_image=current_start,
            end_frame_image=end,
            aspect_ratio=aspect_ratio,
        )

    baseline = build()
    changed_sound_and_execution = build(
        current_prompt={
            **prompt,
            "ambiance_audio": "钟声",
            "dialogue": [{"speaker": "阿黎", "line": "快走"}],
            "voice_profiles": [{"Speaker": "阿黎", "Voice_Style": "尖细"}],
            "provider": "other-provider",
            "resolution": "4k",
            "seed": 99,
            "duration_seconds": 12,
        }
    )

    assert changed_sound_and_execution.digest == baseline.digest
    assert build(current_prompt={**prompt, "action": "阿黎关上门"}).digest != baseline.digest
    assert build(current_prompt={**prompt, "camera_motion": "Pan Left"}).digest != baseline.digest
    assert build(aspect_ratio="9:16").digest != baseline.digest

    start.write_bytes(b"changed-start-frame")

    assert build().digest != baseline.digest


def test_storyboard_video_visual_basis_tracks_end_frame_presence_and_bytes(tmp_path: Path) -> None:
    start = tmp_path / "start.png"
    end = tmp_path / "end.png"
    start.write_bytes(b"start")
    end.write_bytes(b"end-v1")

    def build(current_end: Path | None):
        return build_storyboard_video_artifact_visual_basis(
            resource_id="E1S01",
            visual_prompt={"action": "起身", "camera_motion": "Static"},
            storyboard_image=start,
            end_frame_image=current_end,
            aspect_ratio="16:9",
        )

    without_end = build(None)
    with_end = build(end)

    assert with_end.digest != without_end.digest

    end.write_bytes(b"end-v2")

    assert build(end).digest != with_end.digest


def test_storyboard_video_visual_basis_accepts_legacy_visual_text(tmp_path: Path) -> None:
    start = tmp_path / "start.png"
    start.write_bytes(b"start")

    first = build_storyboard_video_artifact_visual_basis(
        resource_id="E1S01",
        visual_prompt="阿黎沿雨巷奔跑，镜头向前跟随",
        storyboard_image=start,
        end_frame_image=None,
        aspect_ratio="16:9",
    )
    changed = build_storyboard_video_artifact_visual_basis(
        resource_id="E1S01",
        visual_prompt="阿黎停在雨巷尽头，镜头缓慢推近",
        storyboard_image=start,
        end_frame_image=None,
        aspect_ratio="16:9",
    )

    assert changed.digest != first.digest


def _request_asset(
    path: Path,
    *,
    asset_type: Literal["product", "character", "scene", "prop"],
    name: str,
    kind: str = "asset",
) -> ResolvedReferenceAsset:
    return ResolvedReferenceAsset(
        path=path,
        reference=ReferenceResource(type=asset_type, name=name),
        kind=kind,
    )


def test_reference_video_visual_basis_uses_unit_visual_text_and_actual_request_assets(tmp_path: Path) -> None:
    character = tmp_path / "character.png"
    scene = tmp_path / "scene.png"
    clamped = tmp_path / "clamped.png"
    character.write_bytes(b"character")
    scene.write_bytes(b"scene")
    clamped.write_bytes(b"unused")
    unit = {
        "unit_id": "E1U01",
        "shots": [
            {"text": "镜头1：中景，@[阿黎]站在屋檐下\n@[阿黎]：{快走。}"},
            {"text": "特写，雨滴划过窗面\n{夜色吞没街道。}"},
        ],
        "references": [
            {"type": "character", "name": "阿黎"},
            {"type": "scene", "name": "雨巷"},
            {"type": "prop", "name": "被裁掉的伞"},
        ],
        "shot_ids": ["legacy-shot"],
        "source_signature": "legacy-source",
        "derived_group": "legacy-group",
        "duration_seconds": 8,
        "utterances": [{"kind": "voiceover", "text": "声音事实"}],
    }
    request_assets = (
        _request_asset(character, asset_type="character", name="阿黎", kind="sheet"),
        _request_asset(scene, asset_type="scene", name="雨巷", kind="sheet"),
    )

    def build(
        *,
        current_unit: dict[str, object] = unit,
        current_assets: tuple[ResolvedReferenceAsset, ...] = request_assets,
        style: str = "画风：水墨",
        aspect_ratio: str = "9:16",
    ):
        return build_reference_video_artifact_visual_basis(
            unit=current_unit,
            request_assets=current_assets,
            style=style,
            aspect_ratio=aspect_ratio,
        )

    baseline = build()
    changed_speech_and_legacy = build(
        current_unit={
            **unit,
            "shots": [
                {"text": "镜头1：中景，@[阿黎]站在屋檐下\n@[阿黎]：{别走。}"},
                {"text": "特写，雨滴划过窗面\n{另一句画外音。}"},
            ],
            "shot_ids": ["other-legacy-shot"],
            "source_signature": "other-source",
            "derived_group": "other-group",
            "duration_seconds": 12,
            "utterances": [{"kind": "dialogue", "text": "另一声音事实"}],
        }
    )

    assert changed_speech_and_legacy.digest == baseline.digest
    assert (
        build(
            current_unit={
                **unit,
                "shots": [
                    {"text": "镜头1：近景，@[阿黎]跑出屋檐\n@[阿黎]：{快走。}"},
                    unit["shots"][1],
                ],
            }
        ).digest
        != baseline.digest
    )
    assert build(current_unit={**unit, "unit_id": "E1U02"}).digest != baseline.digest
    assert build(style="写实").digest != baseline.digest
    assert build(aspect_ratio="16:9").digest != baseline.digest

    clamped.write_bytes(b"changed-but-still-clamped")

    assert build().digest == baseline.digest


def test_reference_video_visual_basis_tracks_request_asset_identity_order_and_bytes(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    unit = {"unit_id": "E1U01", "shots": [{"text": "阿黎走入雨巷"}]}
    assets = (
        _request_asset(first, asset_type="character", name="阿黎"),
        _request_asset(second, asset_type="scene", name="雨巷"),
    )

    def build(current_assets: tuple[ResolvedReferenceAsset, ...]):
        return build_reference_video_artifact_visual_basis(
            unit=unit,
            request_assets=current_assets,
            style="水墨",
            aspect_ratio="9:16",
        )

    baseline = build(assets)

    assert build(tuple(reversed(assets))).digest != baseline.digest
    assert (
        build((_request_asset(first, asset_type="character", name="另一个角色"), assets[1])).digest != baseline.digest
    )

    first.write_bytes(b"changed-first")

    assert build(assets).digest != baseline.digest


@pytest.mark.parametrize(
    "unit",
    [
        {"unit_id": "", "shots": [{"text": "visual"}]},
        {"shots": [{"text": "visual"}]},
        {"unit_id": "E1U01", "shots": "not-an-array"},
    ],
)
def test_reference_video_visual_basis_requires_canonical_video_unit(unit: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        build_reference_video_artifact_visual_basis(
            unit=unit,
            request_assets=(),
            style="",
            aspect_ratio="9:16",
        )


def test_video_components_compare_through_one_manifest_entry(tmp_path: Path) -> None:
    project_path = tmp_path / "project"
    video_path = project_path / "videos" / "scene_E1S01.mp4"
    paid_version = project_path / "versions" / "videos" / "E1S01" / "1.mp4"
    video_path.parent.mkdir(parents=True)
    paid_version.parent.mkdir(parents=True)
    video_path.write_bytes(b"selected-video")
    paid_version.write_bytes(b"paid-version")
    generated_assets = {
        "video_clip": "videos/scene_E1S01.mp4",
        "video_uri": "provider://paid-job",
        "versions": ["versions/videos/E1S01/1.mp4"],
    }
    untouched_assets = deepcopy(generated_assets)
    key = ArtifactKey.episode_video(1, "E1S01")
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(project_path))
    visual = ArtifactBasis.build("test/video-visual", kind_version=1, inputs={"frame": "v1"})
    speech = ArtifactBasis.build("test/video-speech", kind_version=1, inputs={"line": "v1"})
    duration = ArtifactBasis.build("test/video-duration", kind_version=1, inputs={"seconds": 8})
    baseline = compose_video_artifact_basis(visual=visual, speech=speech, duration=duration)
    manifest.register(key, artifact_path="videos/scene_E1S01.mp4", basis=baseline)

    variants = (
        compose_video_artifact_basis(
            visual=ArtifactBasis.build("test/video-visual", kind_version=1, inputs={"frame": "v2"}),
            speech=speech,
            duration=duration,
        ),
        compose_video_artifact_basis(
            visual=visual,
            speech=ArtifactBasis.build("test/video-speech", kind_version=1, inputs={"line": "v2"}),
            duration=duration,
        ),
        compose_video_artifact_basis(
            visual=visual,
            speech=speech,
            duration=ArtifactBasis.build("test/video-duration", kind_version=1, inputs={"seconds": 12}),
        ),
    )

    assert (
        manifest.compare(key, artifact_path="videos/scene_E1S01.mp4", basis=baseline).status is ArtifactStatus.CURRENT
    )
    for changed in variants:
        comparison = manifest.compare(key, artifact_path="videos/scene_E1S01.mp4", basis=changed)
        assert comparison.status is ArtifactStatus.STALE
        assert comparison.usable
    assert generated_assets == untouched_assets
    assert video_path.read_bytes() == b"selected-video"
    assert paid_version.read_bytes() == b"paid-version"
    assert ProjectArtifactManifestAdapter(project_path).get_entry(ArtifactKey.episode_audio(1, "E1S01")) is None


def test_video_component_composition_names_absent_future_components() -> None:
    visual = ArtifactBasis.build("test/video-visual", kind_version=1, inputs={"frame": "v1"})

    visual_only = compose_video_artifact_basis(visual=visual)
    explicit_none = compose_video_artifact_basis(visual=visual, speech=None, duration=None)
    with_speech = compose_video_artifact_basis(
        visual=visual,
        speech=ArtifactBasis.build("test/video-speech", kind_version=1, inputs={"line": "v1"}),
    )

    assert explicit_none.digest == visual_only.digest
    assert with_speech.digest != visual_only.digest
