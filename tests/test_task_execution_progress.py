"""MiniMax H3 execution progress projection tests."""

import pytest

from lib.task_execution_progress import h3_execution_progress, h3_progress_from_provider

pytestmark = pytest.mark.unit


def test_h3_queue_projection_exposes_ahead_count_and_total():
    progress = h3_progress_from_provider(
        {"status": "queued", "stage": "waiting_for_route", "can_cancel": True, "progress": 0},
        {"position": 4, "queue_length": 9},
    )

    assert progress == {
        "kind": "minimax_h3",
        "phase": "queued",
        "provider_status": "queued",
        "stage": "waiting_for_route",
        "progress": 0,
        "can_cancel": True,
        "queue_position": 4,
        "queue_length": 9,
        "queue_ahead": 3,
    }


def test_h3_progress_clamps_provider_percentage():
    assert h3_execution_progress("running", progress=108.6)["progress"] == 100
    assert h3_execution_progress("running", progress=-2)["progress"] == 0
