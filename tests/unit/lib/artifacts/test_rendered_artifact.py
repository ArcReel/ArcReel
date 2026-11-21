"""渲染产物验收与登记失败时的可观察状态。"""

import asyncio
import re
from pathlib import Path

import pytest

from lib.artifacts.artifact_manifest import (
    ArtifactBasis,
    ArtifactKey,
    ArtifactManifest,
    ArtifactStatus,
    ProjectArtifactManifestAdapter,
)
from lib.artifacts.rendered_artifact import (
    RenderedArtifactPathTooLong,
    check_render_path_lengths,
    cleanup_interrupted_render_staging,
    commit_rendered_artifact,
    read_render_record,
    staging_paths,
)
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


async def test_staging_names_exclude_the_artifact_stem(tmp_path: Path) -> None:
    """隐藏临时文件与工作目录的名字只带随机 token：正式文件名很长，拼进去会顶出路径长度上限。"""
    key = ArtifactKey.episode_final_cut(1, "tl-0000abcd", "without_narration", "no_subtitles")
    path = "renders/episode_1/tl-0000abcd/final_cut.with_narration.burned_subtitles.mp4"
    basis = ArtifactBasis.build("test/render", kind_version=1, inputs={"source": "old"})
    seen: list[tuple[Path, Path]] = []

    async def render(output: Path, workspace: Path) -> None:
        seen.append((output, workspace))
        await asyncio.to_thread(output.write_bytes, b"payload")

    async def accept(output: Path) -> int:
        return len(await asyncio.to_thread(output.read_bytes))

    await commit_rendered_artifact(tmp_path, key=key, artifact_path=path, basis=basis, render=render, accept=accept)

    output, workspace = seen[0]
    assert re.fullmatch(r"\.[0-9a-f]{32}\.partial\.mp4", output.name)
    assert re.fullmatch(r"\.[0-9a-f]{32}\.work", workspace.name)
    assert "burned_subtitles" not in output.name
    assert "burned_subtitles" not in workspace.name


def test_cleanup_removes_new_and_legacy_staging_names(tmp_path: Path) -> None:
    """新版隐藏名与旧版（名字里带正式文件名）隐藏名都要清掉，正式文件与其它隐藏文件不动。"""
    render_dir = tmp_path / "renders" / "episode_1" / "tl-0000abcd"
    render_dir.mkdir(parents=True)
    token = "a" * 32
    new_partial = render_dir / f".{token}.partial.mp4"
    new_work = render_dir / f".{token}.work"
    legacy_partial = render_dir / f".final_cut.{token}.partial.mp4"
    legacy_work = render_dir / f".final_cut.{token}.work"
    keep = render_dir / ".keep-me"
    for item in (new_partial, legacy_partial, keep):
        item.write_bytes(b"x")
    for item in (new_work, legacy_work):
        item.mkdir()

    cleanup_interrupted_render_staging(tmp_path)

    assert not new_partial.exists()
    assert not new_work.exists()
    assert not legacy_partial.exists()
    assert not legacy_work.exists()
    assert keep.exists()


def test_check_render_path_lengths_rejects_the_deepest_staged_file(tmp_path: Path, monkeypatch) -> None:
    """工作目录里的文件比工作目录本身更深，超限时给出明确的异常而不是让文件系统报「找不到」。"""
    from lib.artifacts import rendered_artifact

    formal = tmp_path / "renders" / "episode_1" / "tl-0000abcd" / "final_cut.mp4"
    output, workspace = staging_paths(formal)
    limit = len(str(output))
    monkeypatch.setattr(rendered_artifact, "path_limit", lambda: limit)

    with pytest.raises(RenderedArtifactPathTooLong) as caught:
        check_render_path_lengths(formal, deepest_relative="fonts/SourceHanSansCN-Bold.otf")

    assert caught.value.path.parent.name == "fonts"
    assert caught.value.path.name == "SourceHanSansCN-Bold.otf"
    assert caught.value.length == len(str(workspace / "fonts" / "SourceHanSansCN-Bold.otf"))
    assert caught.value.limit == limit
