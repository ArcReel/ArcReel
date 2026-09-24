"""生成输入 interface：资产图与衍生资产图的参考图、缺口定性、画布比例与冻结。

观测走执行器的 adapter，配内存清单 adapter；参考图是临时目录里的真实小图。
"""

from __future__ import annotations

import copy
import unicodedata
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest
from PIL import Image

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactInputClaim,
    ArtifactKey,
    ArtifactManifestEntry,
    InMemoryArtifactManifestAdapter,
)
from lib.artifacts.generation_input import (
    AssetSheetInput,
    DerivativeSheetInput,
    InputGap,
    InputRefused,
    ManifestInputObservation,
    asset_sheet_input,
    derivative_sheet_input,
)

_REGISTERED_BASIS = ArtifactBasis.build("test/registered", kind_version=1, inputs={}).digest
_OWNER_SHEET = "characters/张三.png"
_ORIGINALS = (
    "characters/refs/张三.png",
    "products/refs/保温杯_1.jpg",
    "products/refs/保温杯_2.jpg",
)


def _project() -> dict[str, Any]:
    return {
        "style": "Anime",
        "style_description": "cinematic",
        "characters": {
            "张三": {
                "description": "hero",
                "reference_image": "characters/refs/张三.png",
                "character_sheet": _OWNER_SHEET,
                "derivatives": {"劲装": {"description": " 换上劲装 ", "character_sheet": ""}},
            },
            "李四": {"description": "sidekick", "reference_image": ""},
        },
        "scenes": {"祠堂": {"description": "temple"}},
        "props": {"玉佩": {"description": "jade"}},
        "products": {
            "保温杯": {
                "description": "不锈钢保温杯",
                "reference_images": ["products/refs/保温杯_1.jpg", "products/refs/保温杯_2.jpg"],
            },
        },
    }


class _Fixture:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.project = _project()
        self.files: set[str] = set()
        self.registered: dict[ArtifactKey, str] = {ArtifactKey.asset_sheet("character", "张三"): _OWNER_SHEET}
        for index, path in enumerate([_OWNER_SHEET, *_ORIGINALS]):
            self.write_image(path, shade=index)

    def write_image(self, relative: str, *, shade: int = 0) -> None:
        target = self.root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        Image.new("RGB", (2, 2), (shade * 20 % 256, 64, 128)).save(target, format="PNG")
        self.files.add(relative)

    def remove_file(self, relative: str) -> None:
        (self.root / relative).unlink()
        self.files.discard(relative)

    def observation(self) -> ManifestInputObservation:
        adapter = InMemoryArtifactManifestAdapter(artifacts=set(self.files))
        for key, path in self.registered.items():
            adapter.put_entry(key, ArtifactManifestEntry(artifact_path=path, basis_digest=_REGISTERED_BASIS))
        return ManifestInputObservation(self.root, adapter)

    def sheet(self, asset_type: str, name: str) -> AssetSheetInput | InputRefused:
        return asset_sheet_input(self.project, asset_type=asset_type, name=name, observation=self.observation())

    def derivative(self, owner: str = "张三", derivative: str = "劲装") -> DerivativeSheetInput | InputRefused:
        return derivative_sheet_input(self.project, owner=owner, derivative=derivative, observation=self.observation())


def _references(generation_input: AssetSheetInput | DerivativeSheetInput) -> list[tuple[str, str, str | None]]:
    return [(ref.artifact_path, ref.visual.role, ref.visual.kind) for ref in generation_input.references]


def _identity_binder(
    claims: Sequence[ArtifactInputClaim], content_digests: Mapping[str, str]
) -> tuple[ArtifactInputClaim, ...]:
    return tuple(
        ArtifactInputClaim(
            key=claim.key, artifact_path=claim.artifact_path, content_digest=content_digests[claim.artifact_path]
        )
        for claim in claims
    )


def test_legacy_asset_name_with_surrounding_whitespace_resolves_for_sheet(tmp_path: Path) -> None:
    fixture = _Fixture(tmp_path)
    fixture.project["characters"][" 张三 "] = fixture.project["characters"].pop("张三")

    result = fixture.sheet("character", "张三")

    assert isinstance(result, AssetSheetInput), result
    assert result.semantics.name == "张三"


class TestAssetSheetReferences:
    @pytest.mark.parametrize(
        ("asset_type", "name", "expected"),
        [
            pytest.param(
                "character", "张三", [("characters/refs/张三.png", "source", "original")], id="character-one-original"
            ),
            pytest.param("character", "李四", [], id="character-no-original"),
            pytest.param(
                "product",
                "保温杯",
                [
                    ("products/refs/保温杯_1.jpg", "source", "original"),
                    ("products/refs/保温杯_2.jpg", "source", "original"),
                ],
                id="product-all-originals",
            ),
            pytest.param("scene", "祠堂", [], id="scene-text-only"),
            pytest.param("prop", "玉佩", [], id="prop-text-only"),
        ],
    )
    def test_originals_by_asset_type(self, tmp_path, asset_type, name, expected):
        result = _Fixture(tmp_path).sheet(asset_type, name)

        assert isinstance(result, AssetSheetInput), result
        assert _references(result) == expected
        assert all(ref.claim is None for ref in result.references)
        assert result.canvas_ratio == "16:9"

    def test_description_comes_from_the_stored_entry(self, tmp_path):
        fixture = _Fixture(tmp_path)
        fixture.project["scenes"]["祠堂"]["description"] = "  古旧祠堂  "

        result = fixture.sheet("scene", "祠堂")

        assert isinstance(result, AssetSheetInput)
        assert "古旧祠堂" in result.render(max_reference_images=0, model="m").prompt

    def test_nfd_name_resolves_to_the_stored_entry(self, tmp_path):
        fixture = _Fixture(tmp_path)
        fixture.project["props"] = {unicodedata.normalize("NFD", "Ngọc bội"): {"description": "jade"}}

        result = fixture.sheet("prop", "Ngọc bội")

        assert isinstance(result, AssetSheetInput)
        assert result.semantics.name == unicodedata.normalize("NFC", "Ngọc bội")


_ASSET_GAP_CASES: list[tuple[str, Callable[[_Fixture], object], str, str, list[InputGap]]] = [
    (
        "character-original-missing",
        lambda f: f.remove_file("characters/refs/张三.png"),
        "character",
        "张三",
        [InputGap("asset_original_missing", "character", "张三")],
    ),
    (
        "product-one-of-many-originals-missing",
        lambda f: f.remove_file("products/refs/保温杯_2.jpg"),
        "product",
        "保温杯",
        [InputGap("asset_original_missing", "product", "保温杯")],
    ),
    (
        "unsafe-original-path",
        lambda f: f.project["characters"]["张三"].__setitem__("reference_image", "../outside.png"),
        "character",
        "张三",
        [InputGap("asset_original_missing", "character", "张三")],
    ),
    (
        "empty-description",
        lambda f: f.project["scenes"]["祠堂"].__setitem__("description", "   "),
        "scene",
        "祠堂",
        [InputGap("asset_description_required", "scene", "祠堂")],
    ),
    (
        "description-and-original-reported-together",
        lambda f: (
            f.project["products"]["保温杯"].__setitem__("description", ""),
            f.remove_file("products/refs/保温杯_1.jpg"),
        ),
        "product",
        "保温杯",
        [
            InputGap("asset_description_required", "product", "保温杯"),
            InputGap("asset_original_missing", "product", "保温杯"),
        ],
    ),
]


class TestAssetSheetGaps:
    @pytest.mark.parametrize(
        ("mutate", "asset_type", "name", "expected"),
        [pytest.param(*case[1:], id=case[0]) for case in _ASSET_GAP_CASES],
    )
    def test_refusals(self, tmp_path, mutate, asset_type, name, expected):
        fixture = _Fixture(tmp_path)
        mutate(fixture)

        result = fixture.sheet(asset_type, name)

        assert isinstance(result, InputRefused), result
        assert list(result.reasons) == expected

    @pytest.mark.parametrize(
        ("asset_type", "name", "mutate", "match"),
        [
            pytest.param("scene", "不存在", lambda _p: None, "not found", id="missing-asset"),
            pytest.param("character", "张三/劲装", lambda _p: None, "not found", id="derivative-is-not-an-asset-sheet"),
            pytest.param(
                "product",
                "保温杯",
                lambda p: p["products"]["保温杯"].__setitem__("reference_images", "products/refs/保温杯_1.jpg"),
                "reference_images",
                id="malformed-originals",
            ),
            pytest.param(
                "scene",
                "祠堂",
                lambda p: p["scenes"]["祠堂"].__setitem__("description", 42),
                "description",
                id="malformed-description",
            ),
        ],
    )
    def test_missing_or_malformed_entry_raises(self, tmp_path, asset_type, name, mutate, match):
        fixture = _Fixture(tmp_path)
        mutate(fixture.project)

        with pytest.raises(ValueError, match=match):
            fixture.sheet(asset_type, name)


class TestDerivativeSheet:
    def test_owner_sheet_is_the_only_reference_with_a_claim(self, tmp_path):
        result = _Fixture(tmp_path).derivative()

        assert isinstance(result, DerivativeSheetInput), result
        assert _references(result) == [(_OWNER_SHEET, "derivative_source", "sheet")]
        [reference] = result.references
        assert reference.claim == ArtifactInputClaim(
            key=ArtifactKey.asset_sheet("character", "张三"), artifact_path=_OWNER_SHEET
        )
        assert result.canvas_ratio == "16:9"
        assert "换上劲装" in result.render(max_reference_images=0, model="m").prompt

    @pytest.mark.parametrize(
        ("mutate", "expected"),
        [
            pytest.param(
                lambda f: f.project["characters"]["张三"].__setitem__("character_sheet", ""),
                [InputGap("derivative_owner_sheet_missing", "character", "张三")],
                id="owner-sheet-undeclared",
            ),
            pytest.param(
                lambda f: f.remove_file(_OWNER_SHEET),
                [InputGap("derivative_owner_sheet_missing", "character", "张三")],
                id="owner-sheet-file-gone",
            ),
            pytest.param(
                lambda f: f.registered.clear(),
                [InputGap("derivative_owner_sheet_missing", "character", "张三")],
                id="owner-sheet-unregistered",
            ),
            pytest.param(
                lambda f: f.project["characters"]["张三"]["derivatives"]["劲装"].__setitem__("description", ""),
                [InputGap("derivative_description_required", "character", "劲装")],
                id="change-description-missing",
            ),
            pytest.param(
                lambda f: (
                    f.project["characters"]["张三"]["derivatives"]["劲装"].__setitem__("description", None),
                    f.remove_file(_OWNER_SHEET),
                ),
                [
                    InputGap("derivative_description_required", "character", "劲装"),
                    InputGap("derivative_owner_sheet_missing", "character", "张三"),
                ],
                id="both-reported-together",
            ),
        ],
    )
    def test_refusals(self, tmp_path, mutate, expected):
        fixture = _Fixture(tmp_path)
        mutate(fixture)

        result = fixture.derivative()

        assert isinstance(result, InputRefused), result
        assert list(result.reasons) == expected

    @pytest.mark.parametrize(("owner", "derivative"), [("王五", "劲装"), ("张三", "便装")])
    def test_missing_owner_or_derivative_raises(self, tmp_path, owner, derivative):
        with pytest.raises(ValueError, match="not found"):
            _Fixture(tmp_path).derivative(owner, derivative)


class TestFreeze:
    def test_clamp_only_limits_what_is_sent(self, tmp_path):
        result = _Fixture(tmp_path).sheet("product", "保温杯")
        assert isinstance(result, AssetSheetInput)

        with result.freeze(max_reference_images=1, model="m", bind_claims=_identity_binder) as frozen:
            assert frozen.basis == result.expected_basis()
            assert len(frozen.references.reference_images or []) == 1
            assert [warning["key"] for warning in frozen.warnings] == ["ref_too_many_images"]
            assert frozen.claims == ()

    def test_derivative_claim_is_bound_to_frozen_bytes(self, tmp_path):
        result = _Fixture(tmp_path).derivative()
        assert isinstance(result, DerivativeSheetInput)

        with result.freeze(max_reference_images=0, model="m", bind_claims=_identity_binder) as frozen:
            assert frozen.basis == result.expected_basis()
            [claim] = frozen.claims
            assert claim.content_digest is not None

    def test_stored_description_change_changes_the_basis(self, tmp_path):
        fixture = _Fixture(tmp_path)
        before = fixture.sheet("character", "张三")
        changed = copy.deepcopy(fixture.project)
        changed["characters"]["张三"]["description"] = "older hero"
        fixture.project = changed
        after = fixture.sheet("character", "张三")

        assert isinstance(before, AssetSheetInput)
        assert isinstance(after, AssetSheetInput)
        assert before.expected_basis() != after.expected_basis()

    @pytest.mark.parametrize("limit", [0, 1])
    def test_preview_render_matches_the_frozen_prompt(self, tmp_path, limit):
        result = _Fixture(tmp_path).sheet("product", "保温杯")
        assert isinstance(result, AssetSheetInput)

        rendered = result.render(max_reference_images=limit, model="m")
        with result.freeze(max_reference_images=limit, model="m", bind_claims=_identity_binder) as frozen:
            assert rendered.prompt == frozen.prompt
            assert rendered.warnings == frozen.warnings
