import pytest

from lib.backends.backend_assembly.specs import PROVIDER_ENDPOINT_SPECS
from lib.backends.generation_type_buckets import builtin_model_bucket_fit, custom_model_bucket_fit
from lib.backends.image_backends.base import ImageCapability
from lib.backends.image_capabilities import builtin_image_snapshot, image_endpoint, resolve_image_model
from lib.capabilities.kernel import InvalidOverrides, bucket_fit
from lib.capabilities.limits import InputLimit
from lib.catalog import query as catalog
from lib.custom_provider.capabilities import custom_image_snapshot
from lib.custom_provider.capability_view import builtin_capability_view, custom_capability_view
from lib.custom_provider.endpoints import (
    ENDPOINT_REGISTRY,
    comfyui_endpoint_spec,
    declarative_endpoint_spec,
    get_endpoint_spec,
)
from tests.factories import comfyui_endpoint_definition


def test_builtin_image_snapshot_and_settings_view():
    info = catalog.get_model("ark", "doubao-seedream-5-0-lite-260128", media_type="image")
    assert info is not None
    snapshot = builtin_image_snapshot("ark", "doubao-seedream-5-0-lite-260128", official=True)
    fit = builtin_model_bucket_fit("ark", "doubao-seedream-5-0-lite-260128", info)
    view = builtin_capability_view(
        "ark", "doubao-seedream-5-0-lite-260128", base_model=info, official=True, overrides={}
    )

    assert sorted(c.value for c in ImageCapability if snapshot.effective[c.value]) == [
        "image_to_image",
        "text_to_image",
    ]
    assert sorted(fit.buckets) == ["i2i", "t2i"]
    assert view.editable == ()
    assert view.effective == view.base


def test_image_reference_limit_soft_override_vs_openrouter_hard_cap():
    facts = {"text_to_image": True, "image_to_image": True, "max_reference_images": 14}

    soft_endpoint = image_endpoint(
        [ImageCapability.TEXT_TO_IMAGE, ImageCapability.IMAGE_TO_IMAGE],
        reference_limit=16,
    )
    soft = resolve_image_model(soft_endpoint, facts, {"max_reference_images": 20})
    assert soft.reference_images == InputLimit.at_most(20)

    openrouter = get_endpoint_spec("openrouter-images").image_description()
    with pytest.raises(InvalidOverrides) as exc:
        openrouter.validate({"max_reference_images": 20}, facts=facts)
    assert exc.value.keys == {"max_reference_images"}
    hard = resolve_image_model(openrouter, facts, {"max_reference_images": 20})
    assert hard.overrides == {}
    assert hard.reference_images == InputLimit.at_most(14)


@pytest.mark.parametrize(
    ("endpoint_key", "hard_limit"),
    [
        ("openrouter-images", 16),
        ("kling-image-single", 1),
        ("minimax-image", 1),
        ("minimax-h3-image", 16),
    ],
)
def test_hard_limit_image_endpoints_reject_exceeding_override(endpoint_key: str, hard_limit: int):
    spec = get_endpoint_spec(endpoint_key)
    snapshot = custom_image_snapshot(endpoint=endpoint_key, model_id="unrecorded-image", endpoint_spec=spec)
    assert snapshot.reference_images == InputLimit.at_most(hard_limit)

    assert snapshot.validate_overrides({"max_reference_images": hard_limit}) == {"max_reference_images": hard_limit}
    with pytest.raises(InvalidOverrides) as exc:
        snapshot.validate_overrides({"max_reference_images": hard_limit + 1})
    assert exc.value.keys == {"max_reference_images"}

    filtered = custom_image_snapshot(
        endpoint=endpoint_key,
        model_id="unrecorded-image",
        endpoint_spec=spec,
        overrides={"max_reference_images": hard_limit + 5},
    )
    assert filtered.overrides == {}
    assert filtered.reference_images == InputLimit.at_most(hard_limit)


@pytest.mark.parametrize(
    ("endpoint_key", "default_limit", "relaxed"),
    [
        ("openai-images", 16, 20),
        ("openai-images-edits", 16, 20),
    ],
)
def test_soft_default_image_endpoints_allow_relaxing_override(endpoint_key: str, default_limit: int, relaxed: int):
    spec = get_endpoint_spec(endpoint_key)
    base_snapshot = custom_image_snapshot(endpoint=endpoint_key, model_id="unrecorded-image", endpoint_spec=spec)
    assert base_snapshot.reference_images == InputLimit.at_most(default_limit)

    assert base_snapshot.validate_overrides({"max_reference_images": relaxed}) == {"max_reference_images": relaxed}
    overridden = custom_image_snapshot(
        endpoint=endpoint_key,
        model_id="unrecorded-image",
        endpoint_spec=spec,
        overrides={"max_reference_images": relaxed},
    )
    assert overridden.reference_images == InputLimit.at_most(relaxed)


def test_unimplemented_image_slots_are_rejected_and_excluded_from_editable():
    gen_only = get_endpoint_spec("openai-images-generations")
    snapshot = custom_image_snapshot(endpoint=gen_only.key, model_id="unrecorded-image", endpoint_spec=gen_only)
    assert snapshot.editable == frozenset({"text_to_image"})
    assert bucket_fit("image", snapshot) == {"i2i": ("image_to_image",)}
    with pytest.raises(InvalidOverrides) as exc:
        snapshot.validate_overrides({"image_to_image": True})
    assert exc.value.reasons["image_to_image"] == "image_unsupported"
    with pytest.raises(InvalidOverrides) as limit_exc:
        snapshot.validate_overrides({"max_reference_images": 2})
    assert limit_exc.value.keys == {"max_reference_images"}

    edit_only = get_endpoint_spec("openai-images-edits")
    edit_snapshot = custom_image_snapshot(endpoint=edit_only.key, model_id="unrecorded-image", endpoint_spec=edit_only)
    assert edit_snapshot.editable == frozenset({"image_to_image", "max_reference_images"})
    assert bucket_fit("image", edit_snapshot) == {"t2i": ("text_to_image",)}
    with pytest.raises(InvalidOverrides) as edit_exc:
        edit_snapshot.validate_overrides({"text_to_image": True})
    assert edit_exc.value.reasons["text_to_image"] == "image_unsupported"


def test_comfyui_and_declarative_image_endpoints_use_single_description():
    comfy_def = comfyui_endpoint_definition(media_type="image")
    comfy_def["bindings"]["reference_images"] = [
        {"node": "10", "input": "image", "class_type": "LoadImage"},
        {"node": "11", "input": "image", "class_type": "LoadImage"},
    ]
    comfy_spec = comfyui_endpoint_spec("ce-img", comfy_def)
    comfy_snap = custom_image_snapshot(
        endpoint="ce-img",
        model_id="qwen-image-edit-plus",
        endpoint_spec=comfy_spec,
        overrides={"text_to_image": True, "max_reference_images": 5},
    )
    assert comfy_snap.editable == frozenset()
    assert comfy_snap.overrides == {}
    assert comfy_snap.reference_images == InputLimit.at_most(2)
    assert custom_model_bucket_fit(
        endpoint="ce-img", model_id="qwen-image-edit-plus", endpoint_spec=comfy_spec
    ).unmet == {"t2i": ("text_to_image",)}
    comfy_view = custom_capability_view(
        endpoint="ce-img",
        model_id="qwen-image-edit-plus",
        spec=comfy_spec,
        raw_overrides={"max_reference_images": 5},
        applied_overrides={},
        entry_binding=None,
    )
    assert comfy_view.editable == ()
    assert comfy_view.effective == {"text_to_image": False, "image_to_image": True, "max_reference_images": 2}

    decl_def = {
        "kind": "declarative",
        "media_type": "image",
        "submit": {"method": "POST", "url": "{{ base_url }}/v1/gen", "body": {}},
        "capabilities": {"text_to_image": True, "image_to_image": True, "max_reference_images": 3},
    }
    decl_spec = declarative_endpoint_spec("ce-decl-img", decl_def, source="custom")
    decl_snap = custom_image_snapshot(
        endpoint="ce-decl-img",
        model_id="unrecorded-image",
        endpoint_spec=decl_spec,
        overrides={"max_reference_images": 6},
    )
    assert decl_snap.base["max_reference_images"] == 3
    assert decl_snap.effective["max_reference_images"] == 6


def test_all_registered_image_endpoints_have_valid_descriptions():
    for key, spec in ENDPOINT_REGISTRY.items():
        if spec.media_type == "image":
            desc = spec.image_description()
            assert desc.media == "image", key
            assert desc.slots & {"text_to_image", "image_to_image"}, key

    for (provider_id, endpoint), pspec in PROVIDER_ENDPOINT_SPECS.items():
        if pspec.media_type == "image":
            snap = builtin_image_snapshot(provider_id, "unrecorded-model", endpoint=endpoint)
            assert snap.media == "image"
            assert snap.endpoint.slots & {"text_to_image", "image_to_image"}
