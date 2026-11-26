"""录制前端页面级套件接口替身用的后端，由 ``frontend/e2e/record-fixtures.ts`` 启动，不单独运行。

替身只应随代码变化。生产后端有三项输入随录制当天的环境而变，这里在导入应用之前固定：

- 目录数据：读测试目录数据 ``tests/fixtures/catalog.json``（与后端测试共用），不读出厂快照。出厂快照随发版刷新成
  目录数据仓库的最新数据。
- 生命周期判定的「今天」：固定为这份数据的数据集日期，与根 conftest 的 ``use_test_catalog_data`` 一致。
- 应用版本：固定为 :data:`RECORDED_APP_VERSION`。

系统时钟从测试数据集日期起走，避免真实日期越过全部关停日期后，日期漂移检查失效。
``--perturb`` 在固定之前换掉这三项之下的真实来源：系统时钟前移、出厂快照目录置空、pyproject 版本号改写。
``record-fixtures.ts --check`` 用它和不加 ``--perturb`` 的后端各录一次，比对两份替身。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import time_machine

from arcreel_catalog_core import CatalogData, parse_catalog

ROOT = Path(__file__).resolve().parent.parent
TEST_CATALOG_FILE = ROOT / "tests" / "fixtures" / "catalog.json"

#: 录制时的应用版本。取得足够大，目录与端点定义里的最低应用版本门槛都判为满足；版本门槛的其他状态由场景替换。
RECORDED_APP_VERSION = "999.0.0"

#: ``--perturb`` 把系统时钟前移的天数：跨过测试目录数据里登记的关停日期。
PERTURB_CLOCK_SHIFT = timedelta(days=1000)
#: ``--perturb`` 改写成的 pyproject 版本号，低于任何已发布的版本。
PERTURB_APP_VERSION = "0.0.1"


def perturb_sources(workdir: Path) -> None:
    """换掉出厂快照与应用版本的真实来源。须在 :func:`pin_inputs` 与导入 ``server`` 之前调用。"""
    from lib import app_version
    from lib.catalog import query

    empty_snapshot = workdir / "snapshot"
    empty_snapshot.mkdir()
    query.SNAPSHOT_DIR = empty_snapshot

    pyproject = workdir / "pyproject.toml"
    pyproject.write_text(f'[project]\nname = "arcreel"\nversion = "{PERTURB_APP_VERSION}"\n', encoding="utf-8")
    app_version.PYPROJECT_PATH = pyproject


def pin_inputs(data: CatalogData) -> None:
    """固定目录数据、生命周期的「今天」与应用版本。须在导入 ``server`` 之前调用：``server`` 下的模块按名字导入
    ``load_app_version``，导入之后再替换就换不到它们。
    """
    from lib import app_version
    from lib.catalog import lifecycle, query

    today = datetime.fromisoformat(data.dataset_version).date()

    def recorded_catalog() -> CatalogData:
        return data

    def recorded_today() -> date:
        return today

    def recorded_app_version(_pyproject_path: Path) -> str:
        return RECORDED_APP_VERSION

    query.factory_snapshot = recorded_catalog
    lifecycle.utc_today = recorded_today
    app_version.load_app_version = recorded_app_version


def install_progress_recording() -> None:
    from lib.backends.text_backends.base import TextProgress
    from lib.backends.text_backends.streaming import current_text_task
    from lib.generation.generation_worker import GenerationWorker
    from server import app as backend

    ready = asyncio.Event()
    release = asyncio.Event()

    async def execute(task: dict[str, Any], *, claimed_provider_id: str | None) -> dict[str, Any]:
        context = current_text_task()
        if context is None or context.on_progress is None or task["project_name"] != "progress-demo":
            raise RuntimeError("Unexpected task in fixture recorder")
        context.on_progress(TextProgress("waiting", "claude-sonnet-4"))
        ready.set()
        await asyncio.wait_for(release.wait(), timeout=15)
        return {}

    backend.create_generation_worker = lambda: GenerationWorker(
        executor=execute, resume_executor=backend.execute_resume_video_task
    )

    @backend.app.post("/__record/start-progress", include_in_schema=False)
    async def start_progress() -> None:
        worker = backend.app.state.generation_worker
        await worker.queue.enqueue_task(
            project_name="progress-demo",
            task_type="text_episode_script",
            media_type="text",
            resource_id="episode-1",
            payload={"episode": 1},
        )
        worker.wake()
        await asyncio.wait_for(ready.wait(), timeout=15)

    @backend.app.post("/__record/finish-progress", include_in_schema=False)
    async def finish_progress() -> None:
        release.set()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--perturb", action="store_true", help="换掉系统时钟、出厂快照与版本号的真实来源")
    args = parser.parse_args()

    os.environ["ARCREEL_SKIP_LEGACY_LOG_MIGRATION"] = "1"

    # 后端启动时拒绝父进程环境里的供应商密钥；录制环境本就没有配置供应商，开发者 shell 里导出的密钥不带进来。
    from lib.config.env_keys import PROVIDER_SECRET_KEYS

    for key in PROVIDER_SECRET_KEYS:
        os.environ.pop(key, None)

    data = parse_catalog(json.loads(TEST_CATALOG_FILE.read_text(encoding="utf-8")))
    clock = datetime.fromisoformat(data.dataset_version)
    if args.perturb:
        clock += PERTURB_CLOCK_SHIFT
    with (
        tempfile.TemporaryDirectory(prefix="arcreel-e2e-record-") as workdir,
        time_machine.travel(clock, tick=True),
    ):
        if args.perturb:
            perturb_sources(Path(workdir))
        pin_inputs(data)

        import uvicorn

        from server.app import app

        install_progress_recording()
        uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
