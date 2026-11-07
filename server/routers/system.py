"""System-level endpoints: package diagnostic logs for download."""

from __future__ import annotations

import tempfile
import zipfile
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import IO

from fastapi import APIRouter
from fastapi.responses import StreamingResponse

from lib.infra.async_thread import run_sync_transaction
from lib.infra.data_root_layout import DataRootLayout
from server.i18n import Translator
from server.services.system.diagnostics import collect_diagnostics

router = APIRouter()

_MAX_FILE_BYTES = 100 * 1024 * 1024
_SPOOL_MAX = 50 * 1024 * 1024
_LOG_GLOB = "arcreel.log*"


def _write_diagnostics_zip(spooled: IO[bytes], log_dir: Path) -> None:
    """Write logs from logs/ and diagnostic information as a zip to ``spooled``, then rewind to the beginning."""
    diagnostics_lines: list[str] = []
    with zipfile.ZipFile(spooled, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        if log_dir.exists():
            for path in sorted(log_dir.glob(_LOG_GLOB)):
                # Skip symlinks to prevent links in logs/ pointing to sensitive files outside the directory
                # from leaking through diagnostic packages.
                if path.is_symlink() or not path.is_file():
                    continue
                size = path.stat().st_size
                if size > _MAX_FILE_BYTES:
                    diagnostics_lines.append(f"[skipped: too large: {path.name} ({size} bytes)]")
                    continue
                zf.write(path, arcname=f"logs/{path.name}")

        diagnostics_text = collect_diagnostics()
        if diagnostics_lines:
            diagnostics_text += "\n" + "\n".join(diagnostics_lines) + "\n"
        zf.writestr("diagnostics.txt", diagnostics_text)

    spooled.seek(0)


@router.get("/system/logs/download")
async def download_logs(_t: Translator) -> StreamingResponse:
    """Return a package containing all files in logs/ plus diagnostics.txt."""
    log_dir = DataRootLayout.current().log_dir

    spooled = tempfile.SpooledTemporaryFile(max_size=_SPOOL_MAX)  # noqa: SIM115 -- keep the handle alive until StreamingResponse finishes reading, beyond this function; no with block
    try:
        # Package and collect diagnostics (subprocesses, log reads) in a thread; on cancellation, await its write before closing the handle.
        await run_sync_transaction(_write_diagnostics_zip, spooled, log_dir)
    except BaseException:
        spooled.close()
        raise

    ts = datetime.now(UTC).strftime("%Y-%m-%d-%H%MZ")
    filename = f"arcreel-diagnostics-{ts}.zip"

    def _iter() -> Iterator[bytes]:
        try:
            while chunk := spooled.read(64 * 1024):
                yield chunk
        finally:
            spooled.close()

    return StreamingResponse(
        _iter(),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
