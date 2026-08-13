from __future__ import annotations

import pytest

from lib import generation_admission

pytestmark = pytest.mark.unit


async def test_admission_guard_closes_its_file_when_unlock_fails(tmp_path, monkeypatch):
    captured_handle = None

    def _fail_unlock(handle):
        nonlocal captured_handle
        captured_handle = handle
        raise OSError("unlock failed")

    monkeypatch.setattr(generation_admission, "app_data_dir", lambda: tmp_path)
    monkeypatch.setattr(generation_admission.portalocker, "unlock", _fail_unlock)

    with pytest.raises(OSError, match="unlock failed"):
        async with generation_admission.generation_admission_lock(
            project_name="demo",
            script_file="episode_01.json",
            resource_id="E1S01",
        ):
            pass

    assert captured_handle is not None and captured_handle.closed
