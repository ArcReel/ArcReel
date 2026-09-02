"""mutmut 拉起的 pytest 会话各用一个私有 basetemp，不进共享的 ``pytest-of-<user>`` 根。

只由 ``pyproject.toml`` 的 ``[tool.mutmut] pytest_add_cli_args`` 以 ``-p tests.mutmut_plugin``
加载，本地与 CI 的 pytest 不加载它。

mutmut 按 mutant fork 出多个 pytest 会话并行跑，它们在会话收尾时同时清理共享根下的
``pytest-current`` 软链，竞争抛出的 ``FileNotFoundError`` 从 ``pytest.main`` 冒出，让子进程
走正常退出路径、退出码 1 记成 killed。显式给定的 basetemp 不登记任何清理，收尾时无事可做；
目录由本会话在 unconfigure 时自己回收。
"""

from __future__ import annotations

import shutil
import tempfile

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config: pytest.Config) -> None:
    """``tryfirst`` 使赋值先于 pytest 自带 tmpdir 插件读取 ``config.option.basetemp``。

    该选项经命令行解析后是 str，xdist 派给 worker 的也是 str，这里保持同一类型。
    """
    if config.option.basetemp is not None:
        return
    private_basetemp = tempfile.mkdtemp(prefix="arcreel-mutmut-basetemp-")
    config.option.basetemp = private_basetemp
    config.add_cleanup(lambda: shutil.rmtree(private_basetemp, ignore_errors=True))
