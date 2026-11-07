import json
import os

from lib.infra import json_io
from lib.infra.json_io import atomic_write_json


def test_atomic_write_json_fsyncs_temp_file_before_replace(tmp_path, monkeypatch):
    """Flush temporary files before replacement so power failure never installs empty/partial targets."""
    target = tmp_path / "project.json"
    target.write_text('{"old": true}', encoding="utf-8")
    events: list[str] = []
    real_fsync, real_replace = os.fsync, os.replace

    def recording_fsync(fd):
        events.append("fsync")
        real_fsync(fd)

    def recording_replace(src, dst):
        events.append("replace")
        real_replace(src, dst)

    monkeypatch.setattr(json_io.os, "fsync", recording_fsync)
    monkeypatch.setattr(json_io.os, "replace", recording_replace)

    atomic_write_json(target, {"title": "新内容"})

    assert events == ["fsync", "replace"]
    assert json.loads(target.read_text(encoding="utf-8")) == {"title": "新内容"}
    assert list(tmp_path.iterdir()) == [target]
