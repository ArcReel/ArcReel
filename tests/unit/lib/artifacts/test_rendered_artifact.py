"""渲染产物验收与登记失败时的可观察状态。"""

import asyncio
from pathlib import Path

import pytest

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifest,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.rendered_artifact import commit_rendered_artifact, read_render_record
from lib.project.project_activity import recorded_project_activity


@pytest.mark.parametrize("failure", ["acceptance", "replacement", "record"])
async def test_failed_replacement_never_claims_new_content_as_current(
    tmp_path: Path, monkeypatch, failure: str
) -> None:
    import os

    key = ArtifactKey.episode_final_cut(1, "tl-0000abcd", "without_narration", "no_subtitles")
    path = "renders/episode_1/tl-0000abcd/final_cut.without_narration.no_subtitles.mp4"
    original = ArtifactBasis.build("test/render", kind_version=1, inputs={"source": "old"})
    changed = ArtifactBasis.build("test/render", kind_version=1, inputs={"source": "new"})
    payload = b"old"

    async def render(output: Path, workspace: Path) -> None:
        await asyncio.to_thread(output.write_bytes, payload)
        await asyncio.to_thread((workspace / "segment.mp4").write_bytes, b"intermediate")

    async def accept(output: Path) -> int:
        return len(await asyncio.to_thread(output.read_bytes))

    first = await commit_rendered_artifact(
        tmp_path, key=key, artifact_path=path, basis=original, render=render, accept=accept
    )
    assert recorded_project_activity(tmp_path) is not None
    payload = b"new"
    replace = os.replace

    def replace_with_record_failure(source, target, **kwargs) -> None:
        if str(target).endswith(".render.json"):
            raise OSError("record write failed")
        replace(source, target, **kwargs)

    def replace_with_formal_failure(source, target, **kwargs) -> None:
        if str(target) == str(tmp_path / path):
            raise OSError("formal replacement failed")
        replace(source, target, **kwargs)

    async def refuse_acceptance(output: Path) -> int:
        raise ValueError("acceptance failed")

    with monkeypatch.context() as patch:
        if failure == "record":
            patch.setattr(os, "replace", replace_with_record_failure)
        if failure == "replacement":
            patch.setattr(os, "replace", replace_with_formal_failure)
        with pytest.raises((OSError, ValueError)):
            await commit_rendered_artifact(
                tmp_path,
                key=key,
                artifact_path=path,
                basis=changed,
                render=render,
                accept=refuse_acceptance if failure == "acceptance" else accept,
            )

    formal = tmp_path / path
    manifest = ArtifactManifest(ProjectArtifactManifestAdapter(tmp_path))
    # 正式文件没被替换时旧登记保留；替换后记录写入失败时撤下登记，不把新内容配旧依据。
    assert manifest.compare(key, artifact_path=path, basis=original).status is (
        ArtifactStatus.MISSING if failure == "record" else ArtifactStatus.CURRENT
    )
    assert formal.read_bytes() == (b"new" if failure == "record" else b"old")
    assert read_render_record(tmp_path, path) == first.record
    assert not [item for item in formal.parent.iterdir() if item.name.startswith(".")]
    retry = await commit_rendered_artifact(
        tmp_path, key=key, artifact_path=path, basis=changed, render=render, accept=accept
    )
    assert retry.record.version == 2
    assert manifest.compare(key, artifact_path=path, basis=changed).status is ArtifactStatus.CURRENT
