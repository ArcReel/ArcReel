"""Backend property and state machine budgets, loaded before test collection."""

import os

from hypothesis import settings

settings.register_profile(
    "ci",
    parent=settings.get_profile("default"),
    derandomize=True,
    database=None,
    deadline=None,
    print_blob=True,
    max_examples=40,
    stateful_step_count=8,
)
settings.register_profile(
    "nightly",
    parent=settings.get_profile("default"),
    derandomize=False,
    database=None,
    deadline=None,
    print_blob=True,
    max_examples=500,
    stateful_step_count=32,
)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "ci"))


def state_machine_settings() -> settings:
    return settings(max_examples=8 if settings.default.derandomize else 40)
