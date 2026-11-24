from itertools import product

import pytest

from lib.capabilities.kernel import AudioProtocol, Endpoint, PathRestriction, RequestConditions, bucket_fit, resolve


def test_legacy_audio_keys_are_sparse_idempotent_model_facts():
    for protocol, legacy, requested in product(
        AudioProtocol, ["always_on", "always_off", "controllable"], [False, True]
    ):
        endpoint = Endpoint("video", audio=protocol)
        raw = {"audio_track": legacy}
        view = resolve(endpoint, overrides=raw)
        if protocol == AudioProtocol.SILENT and legacy != "always_off":
            with pytest.raises(ValueError, match="has_audio_track"):
                endpoint.validate(raw)
            assert not view.overrides
        elif protocol == AudioProtocol.NO_SWITCH and legacy == "controllable":
            with pytest.raises(ValueError, match="has_audio_track"):
                endpoint.validate(raw)
            assert view.overrides == {"has_audio_track": True}
        else:
            normalized = endpoint.validate(raw)
            assert normalized == endpoint.validate(normalized) == view.overrides
            assert normalized == {"has_audio_track": legacy != "always_off"}
        audio = view.audio("r2v", requested=requested)
        assert audio.switch == (requested if protocol == AudioProtocol.SWITCH else None)
        assert audio.output == (
            protocol != AudioProtocol.SILENT
            and legacy != "always_off"
            and (requested or protocol != AudioProtocol.SWITCH)
        )
        assert audio.billed == audio.output
        assert resolve(endpoint, overrides=raw, always_bill_audio=True).audio("r2v", requested=requested).billed


def test_write_read_and_hard_bounds_agree_for_dirty_and_valid_inputs():
    for supported, hard, count, frame in product(
        [True, False], [None, 0, 3], [None, -1, 0, 2, 3, 4, True, "4"], [True, False]
    ):
        endpoint = Endpoint(
            "video",
            slots=frozenset({"reference_images", "last_frame"} if supported else ()),
            limits={} if hard is None else {"max_reference_images": hard},
            defaults={"max_reference_images": 1},
        )
        raw = {"max_reference_images": count, "last_frame": frame}
        result = resolve(endpoint, overrides=raw)
        if supported and type(count) is int and count >= 0 and (hard is None or count <= hard):
            assert endpoint.validate(raw) == result.overrides == raw
        else:
            with pytest.raises(ValueError, match="max_reference_images"):
                endpoint.validate(raw)
        assert result.effective["last_frame"] == (supported and frame)
        if not supported:
            assert result.reference_images.state == "unsupported"
        elif hard is not None:
            assert result.reference_images.maximum is not None
            assert result.reference_images.maximum <= hard


def test_request_restriction_and_inheritance_use_the_same_snapshot_as_the_view():
    endpoint = Endpoint(
        "video",
        frozenset({"first_frame", "last_frame", "reference_images"}),
        audio=AudioProtocol.SWITCH,
        reference_audio=AudioProtocol.SILENT,
        exclusive_inputs=(frozenset({"reference_images", "last_frame"}),),
        paths=(
            PathRestriction(
                bucket="i2v",
                resolutions=frozenset({"4k"}),
                tiers=frozenset({"pro"}),
                disabled_slots=frozenset({"last_frame"}),
                audio=AudioProtocol.SILENT,
            ),
        ),
    )
    raw = endpoint.validate({"last_frame": True, "has_audio_track": True})
    with pytest.raises(ValueError, match="reference_route_has_audio_track"):
        endpoint.validate({"reference_route_has_audio_track": True})
    with pytest.raises(ValueError, match="has_audio_track"):
        endpoint.validate({"has_audio_track": False, "audio_track": "controllable"})
    view = resolve(endpoint, overrides=raw)
    assert view.reference_images.state == "unrecorded"
    assert view.effective["last_frame"]
    assert not view.audio("r2v", requested=True).output
    request = view.for_request(RequestConditions("i2v", "4k", "pro"))
    assert not request.effective["last_frame"]
    assert not request.audio("i2v", requested=True).output
    with pytest.raises(ValueError, match="incompatible input combination"):
        resolve(endpoint, request=RequestConditions("r2v", inputs=frozenset({"reference_images", "last_frame"})))
    assert bucket_fit("video", view) == {}
    assert bucket_fit("video", None) == {"i2v": (), "r2v": ()}


def test_snapshot_detaches_nested_model_data_and_keeps_zero_distinct_from_missing_slot():
    constraints = {"by_resolution": {"1080p": [8]}}
    endpoint = Endpoint("video", frozenset({"reference_images"}))
    snapshot = resolve(
        endpoint, facts={"duration_constraints": constraints, "max_reference_images": 0}, default_resolution="1080p"
    )
    constraints["by_resolution"]["1080p"].append(4)
    assert snapshot.effective["duration_constraints"]["by_resolution"]["1080p"] == (8,)
    with pytest.raises(TypeError):
        snapshot.effective["duration_constraints"]["by_resolution"]["4k"] = (4,)
    assert snapshot.reference_images.state == "bounded"
    assert bucket_fit("video", snapshot) == {"i2v": ("first_frame",), "r2v": ("max_reference_images",)}
    assert snapshot.default_resolution == "1080p"


def test_image_view_distinguishes_zero_from_missing_slot_and_permits_expanding_soft_limit():
    endpoint = Endpoint(
        "image",
        frozenset({"image_to_image"}),
        limits={"max_reference_images": 16},
        defaults={"max_reference_images": 4},
    )
    assert resolve(endpoint, facts={"max_reference_images": 8}).reference_images.maximum == 4
    assert resolve(endpoint, overrides=endpoint.validate({"max_reference_images": 12})).reference_images.maximum == 12
    zero = resolve(
        endpoint, facts={"resolutions": ["1K"], "qualities": ["high"]}, overrides={"max_reference_images": 0}
    )
    assert zero.effective["resolutions"] == ("1K",)
    assert zero.effective["qualities"] == ()
    assert "resolutions" not in zero.editable
    assert zero.effective["image_to_image"] is False
    assert bucket_fit("image", zero)["i2i"] == ("max_reference_images",)
    with pytest.raises(ValueError, match="max_reference_images"):
        endpoint.validate({"max_reference_images": 17})


def test_incoherent_reference_audio_overrides_fall_back_together():
    endpoint = Endpoint("video", frozenset({"reference_audio"}), defaults={"max_reference_audio_count": 2})
    raw = {"reference_audio_mode": "direct", "max_reference_audio_count": 0, "has_audio_track": False}
    with pytest.raises(ValueError, match="reference_audio_mode"):
        endpoint.validate(raw)
    view = resolve(endpoint, overrides=raw)
    assert view.overrides == {"has_audio_track": False}
    assert view.effective["reference_audio_mode"] == "none"
    assert view.audio_track("r2v") == "always_off"
    assert resolve(endpoint, facts={"reference_audio": True}).effective["max_reference_audio_count"] == 2
