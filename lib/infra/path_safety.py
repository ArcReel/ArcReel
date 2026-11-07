"""Path safety utilities: the project's sole validation entry point for paths that must stay within a base directory.

Deliberately use ``os.path.realpath`` plus a ``startswith`` prefix comparison rather than
``Path.resolve()`` with ``relative_to()`` / ``is_relative_to()``. Both provide equivalent
protection (``realpath`` also expands symlinks), but the prefix comparison is a sanitizer
convergence pattern recognized by CodeQL ``py/path-injection``. The alternative is not
recognized and continues to produce existing alerts.

Always construct return values from ``realpath`` output, never the caller's original path,
to prevent tainted values from flowing onward.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = [
    "PathTraversalError",
    "safe_exists",
    "safe_join",
    "safe_resolve",
    "try_safe_join",
]


class PathTraversalError(ValueError):
    """The resolved path escapes the base directory.

    Inherits ``ValueError`` so existing ``except ValueError`` callers still catch it,
    while callers distinguishing traversal from other invalid arguments can catch it precisely.
    """


def _realpath(value: str | os.PathLike[str]) -> str:
    return os.path.realpath(os.fspath(value))


def safe_join(
    base: str | os.PathLike[str],
    *parts: str | os.PathLike[str],
    allow_base: bool = False,
    must_exist: bool = False,
    require_file: bool = False,
) -> Path:
    """Join untrusted ``parts`` under ``base`` and return a normalized absolute path after containment validation.

    Absolute paths in ``parts`` discard preceding components per ``os.path.join`` semantics,
    but must still pass containment validation. Passing an absolute path therefore checks
    whether that absolute path is within base.

    Args:
        base: Base directory that must contain the result.
        parts: Path components, usually from user input or untrusted on-disk data.
        allow_base: Whether a result exactly equal to ``base`` passes. Defaults to False.
        must_exist: If True, the result must exist (file or directory), otherwise raise ``FileNotFoundError``.
        require_file: If True, the result must be an existing **file**, otherwise raise ``FileNotFoundError``.

    Raises:
        PathTraversalError: The result is outside ``base``, or ``parts`` contains invalid
            filesystem characters causing ``os.path.realpath`` to fail (such as embedded NUL bytes).
        FileNotFoundError: ``must_exist`` / ``require_file`` validation failed.
        TypeError: ``parts`` contains non-path types (such as malformed project.json data).
    """
    base_real = _realpath(base)
    # Ensure a trailing separator: at the filesystem root or Windows drive root, base_real already
    # ends with one (for example, "/" or "C:\\"). Adding another produces "//" or "C:\\\\",
    # causing valid child paths to fail the prefix check and be misclassified as escapes.
    base_prefix = base_real if base_real.endswith(os.sep) else base_real + os.sep
    # os.path.join accepts PathLike arguments itself (calling os.fspath internally), so no prior conversion
    # is needed; an extra generator expression would only insert unnecessary nodes in CodeQL dataflow.
    #
    # Untrusted parts may contain invalid filesystem characters (such as embedded NUL bytes).
    # JSON can represent these, but os.path.realpath raises native ValueError/OSError. Most callers
    # catch only PathTraversalError. Convert here rather than patching individual callers, where
    # missed cases would degrade to HTTP 500.
    try:
        candidate_real = os.path.realpath(os.path.join(base_real, *parts))
    except (OSError, ValueError) as exc:
        raise PathTraversalError(f"路径解析失败：{parts!r}") from exc

    # Handle candidate_real == base_real separately: at the filesystem or drive root,
    # base_prefix == base_real and an equal candidate also passes the startswith branch below.
    # Combining allow_base into that branch would bypass allow_base=False at the root.
    if candidate_real == base_real:
        if not allow_base:
            raise PathTraversalError(f"路径越界：{candidate_real!r} 不在 {base_real!r} 内")
    elif not candidate_real.startswith(base_prefix):
        raise PathTraversalError(f"路径越界：{candidate_real!r} 不在 {base_real!r} 内")

    # is_file/exists check candidate_real directly (the string carrying the barrier), rather than
    # wrapping it in Path first: Path() would also interrupt the recognition described above.
    #
    # CodeQL still classifies the following two sinks as unvalidated. They were dismissed as
    # false positives through GitHub code scanning (see each alert's dismissal comment and PR link):
    # containment logic matches the implementation recognized as safe before this repository's
    # migration (the spa_deep_link containment check in server/app.py), with unchanged protection.
    # tests/unit/lib/infra/test_path_safety.py also locks this down with dedicated traversal
    # cases, including symlink escapes. As the repository's sole containment validator, this
    # function necessarily operates on the unsanitized candidate internally. This is considered
    # a CodeQL recognition gap in attributing sinks after branch convergence within a sanitizer.
    if require_file and not os.path.isfile(candidate_real):
        raise FileNotFoundError(candidate_real)
    if must_exist and not os.path.exists(candidate_real):
        raise FileNotFoundError(candidate_real)
    return Path(candidate_real)


def try_safe_join(
    base: str | os.PathLike[str],
    *parts: str | os.PathLike[str],
    allow_base: bool = False,
    must_exist: bool = False,
    require_file: bool = False,
) -> Path | None:
    """Quiet version of ``safe_join``: return None for traversal, missing paths or malformed data.

    For callers that skip unavailable entries instead of rejecting the request (validation summaries, candidate-path traversal, etc.).
    """
    try:
        return safe_join(
            base,
            *parts,
            allow_base=allow_base,
            must_exist=must_exist,
            require_file=require_file,
        )
    except (OSError, ValueError, TypeError):
        # TypeError: components come from raw project.json fields; treat malformed data (dict/int) as absent
        return None


def safe_resolve(base: Path, rel_path: str | None) -> Path | None:
    """Resolve a relative path within base to an absolute path; return None for traversal, malformed data or a missing/non-file result."""
    if not rel_path:
        return None
    return try_safe_join(base, rel_path, require_file=True)


def safe_exists(base: Path, rel_path: str) -> bool:
    """Whether rel_path is a valid relative path within base and the file exists (preventing traversal)."""
    return safe_resolve(base, rel_path) is not None
