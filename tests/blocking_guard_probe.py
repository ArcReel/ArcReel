"""启动临时 pytest 会话，验证事件循环阻塞守卫。"""

import os
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
EXEMPTIONS = Path("tests/blocking_guard/exemptions.toml")


def write_probe(root: Path, *, samples: str, tests: str, exemptions: dict[str, str]) -> None:
    (root / "lib").mkdir(parents=True)
    (root / "lib" / "blocking_samples.py").write_text(samples, encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests" / "test_probe.py").write_text(tests, encoding="utf-8")
    (root / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\nasyncio_mode = "auto"\npythonpath = ["lib", "server", "vendor/site-packages"]\n',
        encoding="utf-8",
    )
    exemptions_file = root / EXEMPTIONS
    exemptions_file.parent.mkdir(parents=True)
    exemptions_file.write_text(
        "".join(f'[[exemption]]\nkey = "{key}"\nreason = "{reason}"\n\n' for key, reason in exemptions.items()),
        encoding="utf-8",
    )


def run_probe(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(_REPO_ROOT), env.get("PYTHONPATH")]))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "pytest_asyncio.plugin",
            "-p",
            "tests.blocking_guard",
            "-p",
            "no:cacheprovider",
            "-rA",
            *args,
        ],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )
