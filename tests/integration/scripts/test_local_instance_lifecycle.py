import json
import socket
from pathlib import Path

import psutil
import pytest

from scripts.local_instance import (
    instance_status,
    is_port_free,
    pick_port,
    start_instance,
    stop_instance,
)


@pytest.mark.local_port
def test_instance_owns_data_directory_and_process(tmp_path: Path):
    (tmp_path / "frontend/dist").mkdir(parents=True)
    (tmp_path / "frontend/dist/index.html").touch()
    data = tmp_path / "data"
    external = tmp_path / "status.json"
    adjacent = tmp_path / "vertex_keys" / "unrelated.json"
    adjacent.parent.mkdir()
    adjacent.write_text("{}")
    state = start_instance(root=tmp_path, data_dir=data, state_file=external, skip_build=True)
    try:
        with pytest.raises(RuntimeError):
            start_instance(root=tmp_path, data_dir=data, skip_build=True)
        assert instance_status(state_file=external)["healthy"]
        stale = dict(state, created_at=state["created_at"] - 1)
        external.write_text(json.dumps(stale))
        assert not instance_status(state_file=external)["process_alive"]
        with pytest.raises(RuntimeError, match="不一致"):
            stop_instance(state_file=external)
        assert psutil.pid_exists(state["pid"])
    finally:
        external.write_text(json.dumps(state))
        stop_instance(data_dir=data)
    assert not external.exists()
    assert not data.exists()
    assert adjacent.read_text() == "{}"


@pytest.mark.local_port
def test_pick_port_and_is_port_free_detect_active_listener() -> None:
    port = pick_port()
    assert is_port_free(port) is True
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", port))
        listener.listen(1)
        assert is_port_free(port) is False
        with pytest.raises(RuntimeError, match="已被占用"):
            pick_port(port)
    assert is_port_free(port) is True


@pytest.mark.local_port
def test_backend_exit_releases_port_and_state(tmp_path: Path):
    (tmp_path / "frontend/dist").mkdir(parents=True)
    (tmp_path / "frontend/dist/index.html").touch()
    port = pick_port()
    data = tmp_path / "instance"
    external = tmp_path / "state.json"
    (tmp_path / "uvicorn.py").write_text("raise SystemExit(42)\n")
    with pytest.raises(RuntimeError, match="退出码 42"):
        start_instance(root=tmp_path, port=port, data_dir=data, state_file=external, skip_build=True)
    assert is_port_free(port)
    assert not data.exists()
    assert not external.exists()
