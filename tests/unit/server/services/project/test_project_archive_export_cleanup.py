"""导出准备失败时，临时快照在线程返回之前释放。"""

import asyncio
import tempfile
from pathlib import Path

import pytest

from lib.artifacts.artifact_manifest import ArtifactManifestError
from lib.project.project_manager import ProjectManager
from server.services.project import project_archive
from server.services.project.project_archive import ProjectArchiveService
from tests.fakes import use_private_tempdir


@pytest.mark.parametrize("operation", ["export_project", "get_export_diagnostics"])
@pytest.mark.parametrize("failure_point", ["copy", "validation", "manifest"])
@pytest.mark.parametrize("error_type", [RuntimeError, ArtifactManifestError, asyncio.CancelledError])
async def test_export_preparation_failure_cleans_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    operation: str,
    failure_point: str,
    error_type: type[BaseException],
) -> None:
    def run() -> None:
        pm = ProjectManager(tmp_path / "projects")
        pm.create_project("demo")
        pm.create_project_metadata("demo", "Demo", "ink", "narration")
        service = ProjectArchiveService(pm)
        temporary_directory = tempfile.TemporaryDirectory
        snapshots: list[tempfile.TemporaryDirectory[str]] = []

        def track_snapshot(*args, **kwargs):
            directory = temporary_directory(*args, **kwargs)
            snapshots.append(directory)
            return directory

        error = error_type("snapshot preparation failed")

        def fail(*args, **kwargs):
            raise error

        use_private_tempdir(monkeypatch, tmp_path)
        monkeypatch.setattr(project_archive.tempfile, "TemporaryDirectory", track_snapshot)
        if failure_point == "copy":
            monkeypatch.setattr(project_archive.shutil, "copystat", fail)
        elif failure_point == "validation":
            monkeypatch.setattr(service.validator, "validate_project_tree", fail)
        else:
            monkeypatch.setattr(project_archive, "snapshot_preserved_artifact_manifest", fail)

        try:
            with pytest.raises(error_type) as caught:
                getattr(service, operation)("demo")
            assert caught.value is error
            assert len(snapshots) == 1
            assert not Path(snapshots[0].name).exists()
            assert not list(tmp_path.glob("demo-*.zip"))
        finally:
            for directory in snapshots:
                directory.cleanup()

    await asyncio.to_thread(run)
