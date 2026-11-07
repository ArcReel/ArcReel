"""Tests for assert_duration_supported."""

import pytest

from arcreel_market_core.video_backend_contract import VideoCapabilityError
from server.services.tasks.generation_tasks import assert_duration_supported


class TestAssertDurationSupported:
    def test_supported_duration_passes(self):
        assert_duration_supported(8, [4, 6, 8])  # no raise

    def test_unsupported_duration_rejected(self):
        # Raise capability errors with stable codes (symmetric with ImageCapabilityError); details live in params.
        with pytest.raises(VideoCapabilityError) as exc:
            assert_duration_supported(5, [4, 6, 8])
        assert exc.value.code == "video_duration_not_supported"
        assert exc.value.params["duration"] == 5

    def test_empty_supported_list_passes(self):
        # Unresolvable capabilities with empty lists allow through, preserving tolerant behavior.
        assert_duration_supported(99, [])  # no raise

    def test_integer_like_string_and_float_accepted(self):
        # External string/float settings parseable as integer seconds normalize and pass without raw exceptions.
        assert_duration_supported("6", [4, 6, 8])  # no raise
        assert_duration_supported(6.0, [4, 6, 8])  # no raise

    def test_fractional_duration_rejected_not_truncated(self):
        # Reject every noninteger duration; never truncate into coincidentally valid four seconds.
        with pytest.raises(VideoCapabilityError) as exc:
            assert_duration_supported(4.5, [4, 6, 8])
        assert exc.value.code == "video_duration_invalid"
        with pytest.raises(VideoCapabilityError):
            assert_duration_supported("4.5", [4, 6, 8])

    def test_non_numeric_duration_rejected(self):
        with pytest.raises(VideoCapabilityError) as exc:
            assert_duration_supported("abc", [4, 6, 8])
        assert exc.value.code == "video_duration_invalid"
