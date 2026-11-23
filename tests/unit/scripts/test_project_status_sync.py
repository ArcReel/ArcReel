import pytest

from scripts.project_status_sync import derive_status


@pytest.mark.parametrize(
    ("state", "labels", "has_assignee", "has_open_closing_pr", "expected"),
    [
        ("OPEN", {"Spec", "ready-for-agent"}, True, True, None),
        ("CLOSED", {"parked"}, False, False, None),
        ("CLOSED", {"needs-triage"}, True, True, "Done"),
        ("OPEN", {"ready-for-agent"}, True, True, "In review"),
        ("OPEN", {"ready-for-human", "needs-info"}, True, False, "In progress"),
        ("OPEN", {"ready-for-agent", "ready-for-human"}, False, False, "Ready · agent"),
        ("OPEN", {"ready-for-human", "needs-info"}, False, False, "Ready · human"),
        ("OPEN", {"needs-info", "needs-triage"}, False, False, "Needs info"),
        ("OPEN", {"needs-triage"}, False, False, "Inbox"),
        ("OPEN", set(), False, False, "Inbox"),
    ],
)
def test_derive_status_priority(state, labels, has_assignee, has_open_closing_pr, expected):
    assert derive_status(state, labels, has_assignee, has_open_closing_pr) == expected
