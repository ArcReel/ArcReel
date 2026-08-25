"""Dependency direction for host-neutral media handlers."""

import ast
from pathlib import Path

from server import remote_mcp
from server.media_tools import definition as media_definition


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return {
        imported
        for node in ast.walk(tree)
        for imported in (
            [node.module]
            if isinstance(node, ast.ImportFrom) and node.module
            else [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else []
        )
    }


def test_host_adapters_depend_on_shared_media_handlers() -> None:
    media_root = Path(media_definition.__file__).parent
    neutral_imports = {name for path in media_root.glob("*.py") for name in _imports(path)}
    remote_imports = _imports(Path(remote_mcp.__file__))

    assert "claude_agent_sdk" not in neutral_imports
    assert not any(name.startswith("server.agent_runtime") for name in neutral_imports)
    assert not any(name.startswith("server.agent_runtime.sdk_tools") for name in remote_imports)
