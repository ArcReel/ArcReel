"""视频预检报价单：已定价的单元合计，未定价的单元如实列出。"""

from __future__ import annotations

import pytest

from lib.generation.batch_admission import BatchAdmission, UnitAdmissionTicket
from lib.generation.generation_result import GenerationSelectionMode
from server.services.admission.video_quote import VideoQuoteOutcome, VideoUnitQuote, quote_sheet


def _priced(unit_id: str, amount: float, currency: str = "USD") -> VideoUnitQuote:
    return VideoUnitQuote(
        unit_id,
        VideoQuoteOutcome.GENERATE,
        request_duration_seconds=8,
        cost={"amount": amount, "currency": currency, "unpriced_reason": None},
    )


def _unpriced(unit_id: str, reason: str = "missing_rate") -> VideoUnitQuote:
    return VideoUnitQuote(
        unit_id,
        VideoQuoteOutcome.GENERATE,
        request_duration_seconds=8,
        cost={"amount": None, "currency": None, "unpriced_reason": reason},
    )


def _sheet(*units: VideoUnitQuote):
    from server.services.admission.video_quote import VideoQuoteSheet

    return VideoQuoteSheet(units=units)


def test_total_covers_the_priced_units_and_the_unpriced_ones_are_counted() -> None:
    sheet = _sheet(_priced("E1U1", 0.5), _unpriced("E1U2"), _priced("E1U3", 0.25), _unpriced("E1U4"))

    assert sheet.estimated_total == {"USD": pytest.approx(0.75)}
    assert sheet.unpriced_count == 2
    payload = sheet.to_payload()
    assert payload["unpriced_count"] == 2
    assert [unit.unpriced for unit in sheet.units] == [False, True, False, True]
    summary = sheet.summary()
    assert "预计合计：0.75 USD（仅含已定价的单元）" in summary
    assert "另有 2 个单元未定价" in summary
    assert "未定价（原因：missing_rate）" in summary


def test_no_total_is_given_when_no_unit_is_priced() -> None:
    sheet = _sheet(_unpriced("E1U1", "no_usage_estimate"))

    assert sheet.estimated_total is None
    assert sheet.unpriced_count == 1
    assert "1 个单元未定价，无法给出合计" in sheet.summary()


def test_a_unit_that_cannot_be_quoted_at_all_is_unpriced_too() -> None:
    unquoted = VideoUnitQuote("E1U1", VideoQuoteOutcome.GENERATE, request_duration_seconds=8, cost=None)
    sheet = _sheet(_priced("E1U2", 1.0), unquoted)

    assert unquoted.unpriced is True
    assert sheet.unpriced_count == 1
    assert sheet.estimated_total == {"USD": pytest.approx(1.0)}
    assert "未定价（无法为这次请求报价）" in sheet.summary()


def test_reused_and_blocked_units_are_neither_priced_nor_unpriced() -> None:
    sheet = _sheet(
        VideoUnitQuote("E1U1", VideoQuoteOutcome.REUSE),
        VideoUnitQuote("E1U2", VideoQuoteOutcome.BLOCKED),
        _priced("E1U3", 2.0),
    )

    assert sheet.unpriced_count == 0
    assert sheet.estimated_total == {"USD": pytest.approx(2.0)}
    assert "未定价" not in sheet.summary()


def test_a_sheet_with_nothing_to_charge_has_an_empty_total() -> None:
    sheet = _sheet(VideoUnitQuote("E1U1", VideoQuoteOutcome.REUSE))

    assert sheet.estimated_total == {}
    assert sheet.unpriced_count == 0


def test_quote_sheet_carries_the_ticket_quote_through_as_unpriced() -> None:
    ticket = UnitAdmissionTicket(
        unit_id="E1U1",
        request_duration_seconds=8,
        request_cost={"amount": None, "currency": None, "unpriced_reason": "no_entry"},
    )
    admission = BatchAdmission("generate_videos", GenerationSelectionMode.MISSING_ONLY, (ticket,))

    sheet = quote_sheet(admission)

    assert sheet.units[0].unpriced is True
    assert sheet.estimated_total is None


def test_confirmation_text_tells_the_agent_which_units_in_a_tier_are_unpriced() -> None:
    from lib.generation.generation_result import GenerationAction, GenerationProblem
    from server.media_tools.videos import _confirmation_lines

    def ticket(unit_id: str, cost: dict[str, object] | None) -> UnitAdmissionTicket:
        return UnitAdmissionTicket(
            unit_id=unit_id,
            problems=(
                GenerationProblem(
                    code="reference_duration_confirmation_required",
                    detail="档位与剧本时长不一致",
                    action=GenerationAction.CONFIRM_REQUEST_DURATION,
                    params={"script_duration": 5, "request_duration": 8},
                ),
            ),
            request_duration_seconds=8,
            request_cost=cost,
        )

    admission = BatchAdmission(
        "generate_videos",
        GenerationSelectionMode.MISSING_ONLY,
        (
            ticket("E1U1", {"amount": 0.8, "currency": "USD"}),
            ticket("E1U2", {"amount": None, "currency": None, "unpriced_reason": "no_entry"}),
        ),
    )

    text = "\n".join(_confirmation_lines(admission))

    assert "8s 档位 × 2：E1U1、E1U2；合计 0.8 USD（仅含已定价的 unit）；其中 1 个 unit 未定价，费用未知" in text
