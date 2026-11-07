"""Combinatorial regression matrix for Wan / happyhorse family classification.

`lib.backends.video_backends.dashscope.classify_wan_model` is the sole classifier for family membership, separator normalization, identifier boundaries,
image-to-video continuation syntax, and videoedit modality exclusion. Endpoint routing
(`lib.custom_provider.endpoints.infer_endpoint`), request profiles
(`lib.backends.video_backends.dashscope._profile_key_for_model`), and duration tiers
(`lib.custom_provider.duration_presets.infer_supported_durations`) only consume its conclusions.
Cover combinations of separators × versions × modalities × case × decorative prefixes, comparing expected results at all three decision points,
and assert general consistency for family members with resolvable t2v/i2v/r2v modality: native routing must imply a resolvable
request profile, and vice versa. Separate regexes at the three points can drift in breadth and yield mutually inconsistent combinations;
individual point tests cannot detect this, whereas cross-point combinatorial checks can.
"""

from __future__ import annotations

import itertools

import pytest

from lib.backends.video_backends.dashscope import DashScopeVideoBackend, _profile_key_for_model
from lib.custom_provider.duration_presets import infer_supported_durations
from lib.custom_provider.endpoints import infer_endpoint

_PREFIX_SEPS = ["-", "_", ""]  # wan[-_]?<version>
_MODALITY_SEPS = ["-", "_"]  # <version>[-_]<modality>
_CASE_FNS = [str.lower, str.upper, str.title]


def _build(prefix_sep: str, version: str, modality_sep: str, modality: str) -> str:
    return f"wan{prefix_sep}{version}{modality_sep}{modality}"


def _wan27_ids() -> list[str]:
    return [id_ for id_, _modality in _wan27_ids_with_modality()]


def _wan27_ids_with_modality() -> list[tuple[str, str]]:
    return [
        (case_fn(_build(prefix_sep, "2.7", modality_sep, modality)), modality)
        for prefix_sep, modality_sep, modality, case_fn in itertools.product(
            _PREFIX_SEPS, _MODALITY_SEPS, ["t2v", "i2v", "r2v"], _CASE_FNS
        )
    ]


@pytest.mark.parametrize(("model_id", "modality"), _wan27_ids_with_modality())
def test_wan27_alias_routes_and_profiles_consistently(model_id: str, modality: str) -> None:
    """wan2.7 t2v/i2v/r2v: all hyphen/underscore prefixes × hyphen/underscore modality separators × case combinations."""
    assert infer_endpoint(model_id, "openai") == (
        "dashscope-reference-voice-video" if modality == "r2v" else "dashscope-always-on-video"
    )
    assert _profile_key_for_model(model_id) == f"wan2.7-{modality}"
    assert DashScopeVideoBackend.video_capabilities_for_model(model_id).max_prompt_chars == 5000
    assert infer_supported_durations(model_id) == list(range(2, 16))


@pytest.mark.parametrize(
    "modality_word",
    ["image-to-video", "image2video", "image_to_video"],
)
@pytest.mark.parametrize("prefix_sep", _PREFIX_SEPS)
@pytest.mark.parametrize("case_fn", _CASE_FNS)
def test_wan27_image_to_video_alias_normalizes_to_i2v(prefix_sep: str, modality_word: str, case_fn: type[str]) -> None:
    model_id = case_fn(f"wan{prefix_sep}2.7-{modality_word}")
    assert infer_endpoint(model_id, "openai") == "dashscope-always-on-video"
    assert _profile_key_for_model(model_id) == "wan2.7-i2v"  # Distinguish from the r2v profile.


@pytest.mark.parametrize("prefix_sep", _PREFIX_SEPS)
@pytest.mark.parametrize("case_fn", _CASE_FNS)
def test_wan27_pure_image_variant_is_not_native_video_route(prefix_sep: str, case_fn: type[str]) -> None:
    """wan2.7-image without image-to-video continuation syntax is an image variant, not a native video endpoint."""
    model_id = case_fn(f"wan{prefix_sep}2.7-image")
    assert infer_endpoint(model_id, "openai") == "openai-images"


def _wan3_ids() -> list[str]:
    return [
        case_fn(_build(prefix_sep, "3", modality_sep, modality))
        for prefix_sep, modality_sep, modality, case_fn in itertools.product(
            _PREFIX_SEPS, _MODALITY_SEPS, ["turbo", "video"], _CASE_FNS
        )
    ]


@pytest.mark.parametrize("model_id", _wan3_ids())
def test_wan3_alias_routes_and_durations_consistently(model_id: str) -> None:
    assert infer_endpoint(model_id, "openai") == "dashscope-wan3-video"
    assert infer_supported_durations(model_id) == list(range(2, 31))


@pytest.mark.parametrize("version", ["2.1", "2.2"])
@pytest.mark.parametrize("modality", ["t2v", "i2v", "r2v"])
@pytest.mark.parametrize("prefix_sep", _PREFIX_SEPS)
@pytest.mark.parametrize("case_fn", _CASE_FNS)
def test_wan2x_non_27_dash_form_stays_generic_dot_form_stays_native(
    prefix_sep: str, modality: str, version: str, case_fn: type[str]
) -> None:
    """Wan 2.1/2.2: hyphen/underscore prefixes use generic video; dot form uses native (this backend always requests
    video-generation/video-synthesis; narrowing this classification requires provider API facts and product judgment,
    as explained by classify_wan_model)."""
    model_id = case_fn(_build(prefix_sep, version, "-", modality))
    expected_endpoint = "dashscope-video" if prefix_sep == "" else "openai-video"
    assert infer_endpoint(model_id, "openai") == expected_endpoint
    assert infer_supported_durations(model_id) == [4, 5]


@pytest.mark.parametrize(
    ("model_id", "expected_endpoint", "expected_profile"),
    [
        ("proxy/wan-2.7-r2v", "dashscope-reference-voice-video", "wan2.7-r2v"),
        ("proxy/wan_2.7_t2v", "dashscope-always-on-video", "wan2.7-t2v"),
        ("vendor-wan2.7-i2v-0715", "dashscope-always-on-video", "wan2.7-i2v"),
    ],
)
def test_decorated_prefix_suffix_still_resolves(model_id: str, expected_endpoint: str, expected_profile: str) -> None:
    """Common proxy prefix/suffix decorations do not affect classification."""
    assert infer_endpoint(model_id, "openai") == expected_endpoint
    if expected_profile and expected_profile.startswith("wan"):
        assert _profile_key_for_model(model_id) == expected_profile


@pytest.mark.parametrize(
    "model_id",
    ["swan2.7-r2v", "vendorwan2.7-t2v", "wan20-i2v", "wan27-r2v", "swan2.1-kf2v"],
)
def test_boundary_false_positives_are_rejected(model_id: str) -> None:
    """Do not misclassify nonfamily names containing wan2/wan3 substrings; enforce both identifier boundaries."""
    assert infer_endpoint(model_id, "openai") == "openai-video"
    assert _profile_key_for_model(model_id) is None
    assert DashScopeVideoBackend.video_capabilities_for_model(model_id).max_prompt_chars is None


@pytest.mark.parametrize(
    ("model_id", "expected_endpoint"),
    [
        # WAN_DOT_FORM_PATTERN right boundary: an adjacent letter excludes 2.x dot form, selecting generic video (bare "wan" still
        # matches _VIDEO_PATTERN).
        ("wan2.7foo-r2v", "openai-video"),
        # HAPPYHORSE_PATTERN right boundary: an adjacent letter excludes the happyhorse family; without video/image keywords, select
        # the default text endpoint.
        ("happyhorsefoo-t2v", "openai-chat"),
        # WAN_IMAGE_TO_VIDEO_PATTERN left boundary: an adjacent letter excludes continuation syntax; treat as an image variant containing image semantics.
        ("wan-2.7-fooimage-to-video", "openai-images"),
        # WAN_IMAGE_TO_VIDEO_PATTERN right boundary: likewise.
        ("wan-2.7-image-to-videofoo", "openai-images"),
    ],
)
def test_adjacent_letters_do_not_qualify_as_classification_tokens(model_id: str, expected_endpoint: str) -> None:
    """Require full identifier boundaries on both sides of classification tokens; adjacent letters/digits must not produce false substring matches."""
    assert infer_endpoint(model_id, "openai") == expected_endpoint
    assert _profile_key_for_model(model_id) is None


@pytest.mark.parametrize("model_id", ["swan2.7-image", "vendorwan2.7-image"])
def test_wan_substring_image_variant_routes_to_image_endpoint_despite_rejected_family(
    model_id: str,
) -> None:
    """IDs failing family boundaries (such as "swan2.7-image") still contain "wan" and match generic _VIDEO_PATTERN.
    Image-endpoint exclusion must not require strict family boundaries, or these would incorrectly select openai-video."""
    assert infer_endpoint(model_id, "openai") == "openai-images"


@pytest.mark.parametrize("model_id", ["image-proxy/wan-2.7-i2v", "proxy-image/wan_2.7-r2v"])
def test_wan27_known_modality_not_downgraded_by_unrelated_image_decoration(model_id: str) -> None:
    """When wan2.7 resolves a known t2v/i2v/r2v profile, unrelated "image" elsewhere in the ID (such as a proxy namespace)
    must not imply an image variant: the known profile already establishes video semantics and takes priority over broad image substring detection."""
    assert infer_endpoint(model_id, "openai") == (
        "dashscope-reference-voice-video" if "r2v" in model_id else "dashscope-always-on-video"
    )


@pytest.mark.parametrize("model_id", ["wan3.0-image-edit", "wan-3-turbo-image-preview"])
def test_wan3_image_variant_still_routes_to_image_endpoint(model_id: str) -> None:
    """wan3 has only one profile key and always has_known_modality=True, without distinguishing t2v/i2v/r2v from true
    image aliases such as image-edit. Do not apply the prior known-modality exemption to wan3; true image aliases must still select image endpoints."""
    assert infer_endpoint(model_id, "openai") == "openai-images"


@pytest.mark.parametrize("model_id", ["image-proxy/wan-2.7-s2v", "image-proxy/wan-2.7-v2v", "wan2.7-videoedit"])
def test_wan27_known_unsupported_video_modality_not_downgraded_by_unrelated_image_decoration(
    model_id: str,
) -> None:
    """s2v/v2v/videoedit match the family regex but lack backend request construction (has_known_modality=False); they are still
    established video modalities. Unrelated "image" elsewhere in the ID must not classify them as image variants; select generic video
    instead of image endpoints."""
    assert infer_endpoint(model_id, "openai") == "openai-video"


@pytest.mark.parametrize("model_id", ["wan-2.7-fooimage-to-video", "wan-2.7-image-to-videofoo"])
def test_wan27_unrecognized_modality_with_image_substring_stays_image_variant(model_id: str) -> None:
    """Names with none of the known modality tokens (t2v/i2v/r2v/s2v/v2v/videoedit) remain conservatively classified as
    image variants even if the family regex matches; do not swallow them with a broad "family==wan2.7 means video" rule."""
    assert infer_endpoint(model_id, "openai") == "openai-images"


@pytest.mark.parametrize("model_id", ["wan2.7-i2v-s2v", "wan-2.7-r2v-v2v"])
def test_wan27_known_token_does_not_mask_coexisting_s2v_v2v(model_id: str) -> None:
    """When IDs contain both a known profile token (i2v/r2v) and s2v/v2v, lack of s2v/v2v request construction takes priority
    over the known-token match; that match must not mask the limitation and admit native routing (the same exclusion priority
    applies when videoedit coexists with a known token)."""
    assert infer_endpoint(model_id, "openai") == "openai-video"
    assert infer_supported_durations(model_id) != list(range(2, 16))


@pytest.mark.parametrize("model_id", ["s2v-proxy/wan2.7-image", "v2v-service/wan-2.7-image"])
def test_wan27_image_variant_not_upgraded_by_s2v_v2v_decoration_before_marker(model_id: str) -> None:
    """Recognize s2v/v2v only within the modality segment after wan2.7, excluding preceding decorations. A true image variant
    (literal modality segment "image") must not become a known video modality because its decorative prefix happens to contain "s2v"/"v2v"."""
    assert infer_endpoint(model_id, "openai") == "openai-images"


@pytest.mark.parametrize("model_id", ["minimax-s2v-swan", "minimax-s2v-swan2"])
def test_minimax_s2v_routing_requires_the_exact_model_name(model_id: str) -> None:
    """MiniMax S2V recognizes only exact model names (S2V-01 / minimax-s2v-01 after stripping namespace prefixes). Other
    spellings containing s2v reveal no definite upstream protocol, so must not select the reference-image-required S2V protocol. These two IDs contain "wan",
    match generic video-family regexes, and select openai-video."""
    assert infer_endpoint(model_id, "openai") == "openai-video"


@pytest.mark.parametrize("model_id", ["wan2-s2v", "wan-2-s2v", "wan_2-s2v"])
def test_wan_glued_bare_digit_version_excluded_from_minimax_routing(model_id: str) -> None:
    """When "wan" directly adjoins an integer version without a decimal point, meeting the left identifier boundary ("wan2", not "swan2")
    must still identify a Wan version token and exclude MiniMax S2V routing, just like equivalent separated forms
    ("wan-2"/"wan_2")."""
    assert infer_endpoint(model_id, "openai") == "openai-video"


@pytest.mark.parametrize(
    "model_id",
    ["image-to-video-proxy/swan2.7-image", "image-to-video-proxy/vendorwan2.7-image"],
)
def test_fallback_image_to_video_syntax_scoped_to_wan_marker_not_decoration_prefix(model_id: str) -> None:
    """The family=None fallback must also search for image-to-video continuation syntax from the literal "wan" substring itself.
    "swan2.7-image"/"vendorwan2.7-image" fail strict family boundaries (family=None); continuation spelling in a decorative
    "image-to-video-proxy/" prefix is outside the ID's own modality segment and must not misclassify a true image variant as
    video continuation."""
    assert infer_endpoint(model_id, "openai") == "openai-images"


@pytest.mark.parametrize(
    "model_id",
    ["vendorwan2.7-videoedit-proxy/wan2.7-r2v", "wan2.7s2v-proxy/wan2.7-i2v"],
)
def test_wan27_suffix_not_polluted_by_literal_marker_text_in_decoration_prefix(model_id: str) -> None:
    """Obtain wan27_suffix from the family marker's actual match position (family_match), not by finding literal "wan2.7"
    in the complete concatenated profile_key. A preceding decorative prefix can itself contain "wan2.7" without meeting
    family boundaries or being classified as a marker, such as "wan2.7" in "vendorwan2.7-videoedit-proxy/".
    Text search cannot distinguish this prefix noise from the true family marker, and would incorrectly include prefix
    "videoedit"/"s2v" content in the modality segment."""
    assert infer_endpoint(model_id, "openai") == (
        "dashscope-reference-voice-video" if "r2v" in model_id else "dashscope-always-on-video"
    )
    assert _profile_key_for_model(model_id) is not None


def test_wan27_videoedit_excluded_from_family_duration_preset() -> None:
    """This backend lacks wan2.7-videoedit request construction, so duration does not use t2v/i2v/r2v family tiers
    (use generic presets, not the family-specific full 2-15s range)."""
    assert infer_supported_durations("wan2.7-videoedit") != list(range(2, 16))


def test_wan27_videoedit_excluded_even_alongside_recognized_modality_token() -> None:
    """ "wan2.7-i2v-videoedit" contains known profile token i2v and videoedit; missing videoedit request construction
    takes priority over the known-token match, which must not mask it and incorrectly admit native routing."""
    assert infer_endpoint("wan2.7-i2v-videoedit", "openai") == "openai-video"
    assert infer_supported_durations("wan2.7-i2v-videoedit") != list(range(2, 16))


@pytest.mark.parametrize("model_id", ["proxy-videoeditor/wan2.7-i2v", "proxy-videoedit-service/wan_2.7-r2v"])
def test_videoedit_detection_ignores_decoration_before_wan27_marker(model_id: str) -> None:
    """Recognize "videoedit" only in the modality segment after wan2.7, excluding preceding decorations: unrelated words
    such as "videoeditor" already fail identifier boundaries, and decorative prefixes such as "videoedit-service" are excluded
    even when bounded. True modalities independent of proxy namespaces (such as i2v/r2v) should route natively."""
    assert infer_endpoint(model_id, "openai") == (
        "dashscope-reference-voice-video" if "r2v" in model_id else "dashscope-always-on-video"
    )


@pytest.mark.parametrize("model_id", ["wan2.7-r2v-videoedit", "wan_2.7-videoedit-0715"])
def test_videoedit_detected_within_modality_segment_after_marker(model_id: str) -> None:
    """A genuine videoedit marker in the modality segment after wan2.7 must still match and exclude native routing,
    whether accompanied by known tokens or suffix decorations."""
    assert infer_endpoint(model_id, "openai") == "openai-video"


@pytest.mark.parametrize("model_id", ["proxy-videoedit/wan3-turbo", "wan-3-turbo-videoedit"])
def test_videoedit_exclusion_scoped_to_wan27_only(model_id: str) -> None:
    """videoedit exclusion applies only to wan2.7. A wan3 ID containing "videoedit" (in a decorative prefix or elsewhere)
    must not be excluded from native routing; the missing modality capability is recorded only for wan2.7."""
    assert infer_endpoint(model_id, "openai") == "dashscope-wan3-video"
    assert infer_supported_durations(model_id) == list(range(2, 31))


@pytest.mark.parametrize(
    ("model_id", "expected_endpoint"),
    [
        ("wan-2.7-v2v", "openai-video"),
        ("wan_2.7-foo", "openai-video"),
        ("wan-2.7-s2v-0715", "openai-video"),
    ],
)
def test_wan27_unrecognized_modality_excluded_from_native_route_and_family_duration(
    model_id: str, expected_endpoint: str
) -> None:
    """A wan2.7 family match without implemented modality request construction (non-t2v/i2v/r2v unknown suffixes other than videoedit)
    uses neither native endpoints nor family duration tiers, following the same exclusion path as videoedit."""
    assert infer_endpoint(model_id, "openai") == expected_endpoint
    assert infer_supported_durations(model_id) != list(range(2, 16))


@pytest.mark.parametrize("model_id", ["wan2.6-image-to-video", "wan2.1-image-to-video"])
def test_wan2x_dot_image_to_video_has_no_registered_capability_falls_back_to_generic(model_id: str) -> None:
    """wan2x_dot (dot forms other than 2.7) registers no VideoCapabilities. Admitting native routing on image-to-video
    continuation would silently obtain channel declarations (whose first_frame=True happens to hide the issue).
    Exclude IDs without verified capabilities/request schemas from native routing and select generic video."""
    assert infer_endpoint(model_id, "openai") == "openai-video"


@pytest.mark.parametrize("model_id", ["wan-2.2-image-to-video", "wan_2.6-image2video"])
def test_image_to_video_syntax_recognized_even_when_family_boundary_unmet(model_id: str) -> None:
    """Recognizing image-to-video continuation must not depend on strict family boundaries. When a separator between wan and
    version fails dot-form boundaries and classify_wan_model returns family=None, preserve continuation information
    instead of swallowing it as an image endpoint through broad image detection."""
    assert infer_endpoint(model_id, "openai") == "openai-video"


@pytest.mark.parametrize("model_id", ["wan-2.2-s2v", "wan_2.6-s2v", "vendorwan2.7-s2v"])
def test_wan_substring_s2v_excluded_from_minimax_routing_even_without_family_match(model_id: str) -> None:
    """Excluding MiniMax routing for bare "s2v" also must not require strict family boundaries. These IDs contain "wan"
    but fail family boundaries (family=None); select generic video instead of incorrectly selecting MiniMax S2V."""
    assert infer_endpoint(model_id, "openai") == "openai-video"


@pytest.mark.parametrize(
    ("model_id", "expected_endpoint"),
    [
        ("proxy/happyhorse-1.0-r2v", "dashscope-reference-video"),
        ("happyhorse-1.0-r2v-0715", "dashscope-reference-video"),
        ("myhappyhorse-1.0-r2v", "openai-chat"),  # Identifier boundary rejected: not the happyhorse family.
    ],
)
def test_happyhorse_boundary_and_decoration(model_id: str, expected_endpoint: str) -> None:
    assert infer_endpoint(model_id, "openai") == expected_endpoint


@pytest.mark.parametrize(
    "model_id",
    [
        *_wan27_ids(),
        *_wan3_ids(),
        "happyhorse-1.0-t2v",
        "happyhorse-1.1-i2v",
        "happyhorse-1.0-r2v",
    ],
)
def test_native_route_and_non_default_profile_agree(model_id: str) -> None:
    """Consistency: family members with resolvable t2v/i2v/r2v modality have native routing iff they have a resolvable request profile.

    This covers only members with resolvable modalities. Bare family names (such as "happyhorse"), wan2.7-videoedit, and non-2.7
    dotted 2.x ("wan2.1-*") are intentionally unrecognized without verified modality/protocol and exempt from this assertion
    (see classify_wan_model and _profile_for_model).
    """
    assert infer_endpoint(model_id, "openai").startswith("dashscope-")
    if "happyhorse" not in model_id:
        assert _profile_key_for_model(model_id) is not None


@pytest.mark.parametrize(
    "model_id",
    [
        "wan2.7-i2v-s2v",  # A coexisting unimplemented modality excludes the known token from has_known_modality.
        "wan-2.7-r2v-v2v",
        "wan2.7-r2v-videoedit",
        "image-proxy/wan-2.7-s2v",  # The unimplemented modality itself is excluded; decorative prefixes do not change that.
    ],
)
def test_excluded_from_native_route_agrees_with_default_profile(model_id: str) -> None:
    """Exclusion consistency: with has_known_modality=False, both decision points must use the default profile, without
    the inconsistent combination of no native routing but a resolved request profile. Without checking has_known_modality,
    substring-tolerant `_profile_for_model` could find a coexisting known token in decorative suffixes (such as "i2v" in
    "wan2.7-i2v-s2v") and incorrectly resolve that implemented modality's request profile."""
    assert not infer_endpoint(model_id, "openai").startswith("dashscope-")
    assert _profile_key_for_model(model_id) is None


@pytest.mark.parametrize(
    "model_id",
    [
        "image-to-video-proxy/wan2.7-image",  # Decorative prefix contains the complete "image-to-video" spelling.
        "video-continuation-proxy/wan-2.7-image",
    ],
)
def test_wan27_image_variant_not_upgraded_by_image_to_video_decoration_before_marker(model_id: str) -> None:
    """Like videoedit/s2v/v2v, recognize image-to-video continuation only in the modality segment after wan2.7:
    even a complete literal "image-to-video" in a preceding decorative prefix must not turn a true image variant into video continuation."""
    assert infer_endpoint(model_id, "openai") == "openai-images"


@pytest.mark.parametrize("model_id", ["wan2.7-image-to-video", "wan_2.7-image2video"])
def test_wan27_image_to_video_detected_within_modality_segment_after_marker(model_id: str) -> None:
    """Genuine image-to-video continuation within the modality segment after wan2.7 must still match, normalize to
    i2v, route natively, and resolve a request profile."""
    assert infer_endpoint(model_id, "openai") == (
        "dashscope-reference-voice-video" if "r2v" in model_id else "dashscope-always-on-video"
    )
    assert _profile_key_for_model(model_id) is not None
