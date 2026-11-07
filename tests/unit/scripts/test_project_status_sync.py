import pytest

from scripts.project_status_sync import derive_status


@pytest.mark.parametrize(
    ("state", "labels", "has_assignee", "has_open_closing_pr", "expected"),
    [
        # Spec / parked clears Status, ahead of all other signals.
        ("OPEN", {"Spec", "ready-for-agent"}, True, True, None),
        ("CLOSED", {"parked"}, False, False, None),
        # closed → Done, ahead of open PRs and assignees.
        ("CLOSED", {"needs-triage"}, True, True, "Done"),
        # Open closing PR → In review, ahead of assignees and ready labels.
        ("OPEN", {"ready-for-agent"}, True, True, "In review"),
        # assignee → In progress, ahead of ready / needs-info labels.
        ("OPEN", {"ready-for-human", "needs-info"}, True, False, "In progress"),
        # ready-for-agent precedes ready-for-human, which precedes needs-info.
        ("OPEN", {"ready-for-agent", "ready-for-human"}, False, False, "Ready · agent"),
        ("OPEN", {"ready-for-human", "needs-info"}, False, False, "Ready · human"),
        ("OPEN", {"needs-info", "needs-triage"}, False, False, "Needs info"),
        # needs-triage and unlabeled issues both fall back to Inbox.
        ("OPEN", {"needs-triage"}, False, False, "Inbox"),
        ("OPEN", set(), False, False, "Inbox"),
    ],
)
def test_derive_status_priority(state, labels, has_assignee, has_open_closing_pr, expected):
    assert derive_status(state, labels, has_assignee, has_open_closing_pr) == expected
