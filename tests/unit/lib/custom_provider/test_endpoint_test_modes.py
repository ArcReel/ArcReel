"""Endpoint-test mode matrix: modes supported by each definition kind, and implementation coverage for supported modes."""

from __future__ import annotations

import pytest

from arcreel_market_core.endpoint_definition import COMFYUI_KIND, DECLARATIVE_KIND
from lib.custom_provider.endpoint_test import SUPPORTED_KINDS, TESTABLE_KINDS, EndpointTestMode, supports_test_mode


@pytest.mark.parametrize("mode", list(EndpointTestMode))
def test_declarative_supports_every_mode(mode: EndpointTestMode):
    assert supports_test_mode(DECLARATIVE_KIND, mode)


@pytest.mark.parametrize("mode", [EndpointTestMode.PREVIEW_REQUEST, EndpointTestMode.TRIAL_RUN])
def test_comfyui_supports_previewing_and_trial_running(mode: EndpointTestMode):
    assert supports_test_mode(COMFYUI_KIND, mode)


def test_comfyui_does_not_support_checking_a_response():
    """Artifact extraction reads three fixed keys; there are no user-configurable value paths to validate (ADR 0081)."""
    assert not supports_test_mode(COMFYUI_KIND, EndpointTestMode.CHECK_RESPONSE)


@pytest.mark.parametrize("mode", list(EndpointTestMode))
def test_a_kind_without_an_implementation_supports_nothing(mode: EndpointTestMode):
    """Unknown kinds support no modes; callers reject them structurally instead of discovering missing implementations midway."""
    assert not supports_test_mode("not-a-kind", mode)


def test_modes_are_the_three_documented_ones():
    assert {mode.value for mode in EndpointTestMode} == {"preview-request", "check-response", "trial-run"}


def test_the_support_matrix_and_the_implementations_cover_the_same_kinds():
    """The two kind-indexed tables must agree: every allowed kind has an implementation, and every implemented kind belongs in the matrix."""
    assert TESTABLE_KINDS == SUPPORTED_KINDS
