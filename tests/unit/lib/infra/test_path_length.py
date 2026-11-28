"""路径长度上限的判定：超限的路径要能提前认出来，别等文件系统报「找不到」。"""

import os

from lib.infra.path_length import (
    POSIX_PATH_MAX,
    WINDOWS_MAX_PATH,
    WINDOWS_MAX_PATH_LENGTH,
    path_length,
    path_limit,
)


def test_windows_limit_excludes_the_terminating_nul() -> None:
    assert WINDOWS_MAX_PATH == 260
    assert WINDOWS_MAX_PATH_LENGTH == 259


def test_path_limit_follows_the_platform() -> None:
    assert path_limit() == (WINDOWS_MAX_PATH_LENGTH if os.name == "nt" else POSIX_PATH_MAX)


def test_path_length_counts_characters() -> None:
    assert path_length("a/b/c") == 5
    assert path_length(os.fspath("a/b/c")) == 5
