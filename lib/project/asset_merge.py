"""资产合并的纯函数层：引用改指保留方、衍生去留规划与影响报告。

合并把同一身份被登记成的两个同类型资产并成一个（见 ``docs/adr/0057``、``docs/adr/0092``）：
被并方的全部引用改指保留方，保留方原样不动，只追加别名或衍生。它不是「重命名到已有名字」，
重命名遇到同名一律拒绝。

两种并法：

- **并为本体**：被并方的名字与别名追加进保留方的别名；``被并方/衍生`` 改指 ``保留方/衍生``。
- **并为衍生**（仅角色）：被并方成为保留方的一个衍生，衍生名就是被并方的名字，不记为别名。
  画面引用改指 ``保留方/被并方``，说话人只承载本体名（见 ``docs/adr/0072``），改指保留方。

两种并法里，衍生与保留方已有衍生同名（按 :func:`find_derivative_name`，只差大小写也算）时并入
已有的那个，引用改写到它的名字。

本模块只承载无副作用的部分（就地改写载荷、返回计数与规划），供
:meth:`lib.project.project_manager.ProjectManager.merge_asset` 在与重命名同一组锁内先扫描、再落盘。
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable, Collection, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from lib.artifacts.artifact_manifest import ArtifactKey, ArtifactKind, ArtifactManifestError, ArtifactStatus
from lib.project.asset_derivatives import derivative_sheet_relative_path, derivative_table, find_derivative_name
from lib.project.asset_rename import asset_files_named
from lib.project.asset_types import ALIASES_FIELD, ASSET_SPECS, asset_name_comparison_key, resolve_asset_key
from lib.prompts.reference_image_numbering import reference_image_label
from lib.references.reference_catalog import build_reference_catalog, derivative_reference, split_derivative_reference
from lib.script.reference_video.text_parser import extract_mentions, remap_mentions, render_matched_mentions
from lib.script.script_editor import ScriptEditError, resolve_items
from lib.script.script_references import iter_reference_lists, iter_reference_sites
from lib.speech.speech_artifact_provenance import build_video_speech_basis
from lib.speech.speech_composition import admit_script_unit

#: 可以合并的资产类型：带别名的类型（角色、场景、道具）。商品没有别名，合并后被并名无处可记。
MERGEABLE_ASSET_TYPES: tuple[str, ...] = tuple(
    asset_type for asset_type, spec in ASSET_SPECS.items() if ALIASES_FIELD in spec.extra_list_fields
)

MergeRejection = Literal["type_not_mergeable", "same_asset", "derivative_needs_character"]


class AssetMergeNotFoundError(KeyError):
    """被并方或保留方在资产表中不存在。"""

    def __init__(self, name: str):
        super().__init__(name)
        self.name = name


class AssetMergeRejectedError(ValueError):
    """这次合并不成立（类型不可合并、并入自己、非角色并为衍生），整体拒绝、不落盘。"""

    def __init__(self, reason: MergeRejection, detail: str):
        super().__init__(detail)
        self.reason: MergeRejection = reason


@dataclass(frozen=True, slots=True)
class ReferenceChanges:
    """一份载荷里改写的引用处数，按落点分：引用数组、说话人、正文与画面描述里的记号。"""

    names: int = 0
    speakers: int = 0
    mentions: int = 0

    @property
    def total(self) -> int:
        return self.names + self.speakers + self.mentions


@dataclass(frozen=True, slots=True)
class AssetMergeEpisodeImpact:
    """合并对一集的影响：各处改写的引用数，以及将从现行转为过期的分镜图与视频数。

    ``script_plan`` / ``script`` / ``draft`` 只数引用数组（按所在文件分），说话人与记号
    不分文件，分别计入 ``speaker`` 与 ``prompt_text``，各项之和即这一集的引用改写总数。
    """

    episode: int
    script_plan: int = 0
    script: int = 0
    draft: int = 0
    prompt_text: int = 0
    speaker: int = 0
    storyboards: int = 0
    videos: int = 0

    @property
    def references(self) -> int:
        return self.script_plan + self.script + self.draft + self.prompt_text + self.speaker


@dataclass(frozen=True, slots=True)
class AssetMergeReport:
    """资产合并的影响报告。dry-run 预览与实际执行共用同一次扫描，数字必然一致。"""

    table: str
    source: str
    target: str
    as_derivative: bool
    #: 追加进保留方别名的名字（并为衍生时为空）。
    aliases_added: tuple[str, ...]
    #: 并为衍生时新建的衍生名；保留方已有同名（只差大小写也算）衍生时并入那个，这里为 ``None``。
    derivative_created: str | None
    #: 被并方名下迁到保留方的衍生。
    derivatives_moved: tuple[str, ...]
    #: 被并方名下与保留方已有衍生同名（只差大小写也算）、并入已有那个的衍生（这些衍生的描述与资产图不保留）。
    derivatives_folded: tuple[str, ...]
    episodes: tuple[AssetMergeEpisodeImpact, ...]
    dry_run: bool

    @property
    def references(self) -> int:
        return sum(item.references for item in self.episodes)


@dataclass(frozen=True, slots=True)
class DerivativeMergePlan:
    """被并角色的衍生怎么处理：新建、迁移或并入保留方已有的同名衍生，以及引用随之改写成的衍生名。"""

    created: str | None = None
    moved: tuple[str, ...] = ()
    folded: tuple[str, ...] = ()
    #: 并为衍生时被并方本体在保留方名下的衍生名（新建的，或并入的已有衍生），比对坐标。
    source_derivative: str | None = None
    #: 被并方的衍生（比对坐标）→ 它在保留方名下的衍生名（比对坐标）；并入只差大小写的已有衍生时两者不同。
    derivative_names: Mapping[str, str] = field(default_factory=dict)

    def kept_derivative(self, derivative: str) -> str:
        """被并方名下的衍生（比对坐标）合并后在保留方名下叫什么；不是被并方的衍生时原样返回。"""
        return self.derivative_names.get(derivative, derivative)

    def carried_references(self, source: str, target: str) -> dict[str, str]:
        """迁到保留方名下的衍生：合并后的引用名 → 合并前的引用名（比对坐标）。

        迁移的衍生连图带过来，引用只是换了名字；并入已有衍生的换成了另一张图，不在其列。
        """
        return {
            derivative_reference(asset_name_comparison_key(target), asset_name_comparison_key(name)): (
                derivative_reference(asset_name_comparison_key(source), asset_name_comparison_key(name))
            )
            for name in self.moved
        }


def merged_reference(
    name: str,
    source: str,
    target: str,
    *,
    as_derivative: bool,
    speaker: bool,
    derivatives: DerivativeMergePlan,
) -> str | None:
    """把一个引用名按一次合并改写；与被并方无关时返回 ``None``。

    *source* / *target* 是资产表里的条目名。``被并方/衍生`` 一律改指 ``保留方/衍生``（衍生随之
    迁到保留方名下）；被并方本体在并为衍生时改指 ``保留方/被并方``，但说话人位只认本体，仍改指保留方。
    衍生名按 *derivatives* 规划改写：并入只差大小写的已有衍生时，改用已有衍生的名字。
    """
    key = asset_name_comparison_key(name)
    source_key = asset_name_comparison_key(source)
    base, derivative = split_derivative_reference(key)
    if derivative:
        return derivative_reference(target, derivatives.kept_derivative(derivative)) if base == source_key else None
    if key != source_key:
        return None
    if as_derivative and not speaker:
        return derivative_reference(target, derivatives.source_derivative or source_key)
    return target


def merge_payload_references(
    payload: dict[str, Any],
    asset_type: str,
    source: str,
    target: str,
    *,
    as_derivative: bool = False,
    derivatives: DerivativeMergePlan,
) -> ReferenceChanges:
    """就地把剧本 / 草稿载荷里指向被并方的引用改指保留方，返回各落点的改写数。

    落点与重命名同一份（:func:`lib.script.script_references.iter_reference_sites`）。与重命名不同的是
    保留方本来就可能写在同一个引用数组里，改写后按比对坐标去重，只留第一次出现的那个。旧式剧本内嵌
    的顶层 ``characters`` 镜像里被并方那条直接删除，计入引用数组的改写数。
    """
    spec = ASSET_SPECS[asset_type]
    names = speakers = mentions = 0
    written: set[str] = set()

    def remap_for(kind: str) -> tuple[Callable[[str, bool], str | None], list[int]]:
        hits = [0]

        def remap(name: str, is_speaker: bool) -> str | None:
            merged = merged_reference(
                name,
                source,
                target,
                as_derivative=as_derivative,
                speaker=is_speaker and kind == "text",
                derivatives=derivatives,
            )
            if merged is None:
                return None
            hits[0] += 1
            return f"@[{merged}]"

        return remap, hits

    for kind, value, write in iter_reference_sites(
        payload, frozenset(spec.reference_list_fields), with_speaker=asset_type == "character"
    ):
        if kind in ("text", "scene"):
            remap, hits = remap_for(kind)
            rewritten = remap_mentions(value, remap)
            if hits[0]:
                write(rewritten)
                mentions += hits[0]
            continue
        # 新增资产清单的 target 与说话人一样只认本体：并为衍生时改指保留方，不写成 ``保留方/被并方``。
        merged = merged_reference(
            value,
            source,
            target,
            as_derivative=as_derivative,
            speaker=kind in ("speaker", "target"),
            derivatives=derivatives,
        )
        if merged is None:
            continue
        write(merged)
        if kind == "speaker":
            speakers += 1
        else:
            names += 1
            written.add(asset_name_comparison_key(merged))

    for items in iter_reference_lists(payload, frozenset(spec.reference_list_fields)):
        seen: set[str] = set()
        kept: list[Any] = []
        for item in items:
            if isinstance(item, str):
                key = asset_name_comparison_key(item)
                if key in written and key in seen:
                    continue
                seen.add(key)
            kept.append(item)
        if len(kept) != len(items):
            items[:] = kept

    if asset_type == "character":
        embedded = payload.get("characters")
        embedded_key = resolve_asset_key(embedded, source) if isinstance(embedded, dict) else None
        if isinstance(embedded, dict) and embedded_key is not None:
            del embedded[embedded_key]
            names += 1

    return ReferenceChanges(names=names, speakers=speakers, mentions=mentions)


def visual_reference_signature(item: Mapping[str, Any], asset_type: str) -> tuple[object, ...]:
    """一个条目里决定其画面产物的引用：引用数组、画面描述与正文里的画面记号（不含说话人位）。

    合并前后签名不同的条目，其分镜图或参考生视频的登记依据随之对不上，由现行转为过期。
    说话人与台词记号的说话人位不进入画面依据，只改它们的条目不算。
    """
    spec = ASSET_SPECS[asset_type]
    lists = tuple(
        tuple(item.get(name) or ()) for name in spec.reference_list_fields if isinstance(item.get(name), list)
    )
    image_prompt = item.get("image_prompt")
    scene = image_prompt.get("scene") if isinstance(image_prompt, Mapping) else image_prompt
    text = item.get("text")
    text_mentions = tuple(extract_mentions(text)) if isinstance(text, str) else ()
    return (lists, scene if isinstance(scene, str) else None, text_mentions)


def storyboard_reference_signature(
    item: Mapping[str, Any], asset_type: str, identities: Mapping[str, str]
) -> tuple[object, ...]:
    """一个条目里决定其分镜图的引用，按引用到的图比较。

    分镜图依据不记资产名、只记参考图内容（``without_asset_names``）：可用参考图的名字换成
    *identities* 中的内容身份；缺图仍按名字区分缺口。正文只替换引用数组里实际选中的参考图。
    """
    remapped = dict(item)
    spec = ASSET_SPECS[asset_type]
    selected: dict[str, str] = {}
    for name in spec.reference_list_fields:
        names = item.get(name)
        if isinstance(names, list):
            selected.update(
                (key, identities[key])
                for value in names
                if isinstance(value, str) and (key := asset_name_comparison_key(value)) in identities
            )
            remapped[name] = [
                identities.get(asset_name_comparison_key(value), value) if isinstance(value, str) else value
                for value in names
            ]
    image_prompt = item.get("image_prompt")
    scene = image_prompt.get("scene") if isinstance(image_prompt, Mapping) else image_prompt
    if isinstance(scene, str):
        remapped["image_prompt"] = remap_mentions(
            scene, lambda name, _speaker: f"@[{selected[name]}]" if name in selected else None
        )
    return visual_reference_signature(remapped, asset_type)


def storyboard_reference_identities(project_dir: Path, project: dict[str, Any], asset_type: str) -> dict[str, str]:
    """已登记且可用的资产参考图：引用名 → 内容身份；没有图的引用不在映射里。"""
    # 生成输入经规划器依赖 ProjectManager，惰性导入避免环。
    from lib.artifacts.generation_input import project_input_observation
    from lib.artifacts.visual_artifact_provenance import visual_file_digest

    observation = project_input_observation(project_dir)
    catalog = build_reference_catalog(project)
    identities: dict[str, str] = {}
    for name in catalog.reference_names(asset_type):
        reference = catalog.lookup(asset_type, name)
        if reference is None or not isinstance(reference.asset, Mapping):
            continue
        sheet = reference.asset.get(reference.spec.sheet_field)
        registered = observation.registered(ArtifactKey.asset_sheet(asset_type, name))
        if not isinstance(sheet, str) or registered is None or registered.artifact_path != sheet:
            continue
        observed = observation.observe(sheet)
        if observed is not None:
            try:
                identities[name] = f"sha256:{visual_file_digest(observed.path)}"
            except OSError:
                continue
    return identities


#: 问「这件产物此刻是现行的、且依据会随剧本引用变化吗」：``(清单键, 产物路径字段值) → bool``。
CurrentArtifactProbe = Callable[[ArtifactKey, object], bool]


def current_artifact_probe(project_dir: Path) -> CurrentArtifactProbe:
    """按产物清单判定现行与否的探针；项目没有可用的清单（未升级、清单损坏）时一律答「否」。

    选中手动上传的分镜图按上传字节判定时效，剧本引用与宫格参考图都不是它的输入，合并不会让它过期，
    同样答「否」。上传依据由规划器判定（选中版本是上传且字节与正式文件一致），这里不另行推断。
    """
    # artifact_currency 经产物规划依赖 project_manager，模块级导入会成环。
    from lib.artifacts.artifact_currency import ArtifactCurrencyResolver
    from lib.artifacts.visual_artifact_provenance import UPLOADED_STORYBOARD_BASIS_KIND

    try:
        resolver = ArtifactCurrencyResolver(project_dir)
    except (ArtifactManifestError, OSError, ValueError):
        return lambda _key, _path: False

    def is_current(key: ArtifactKey, artifact_path: object) -> bool:
        if not isinstance(artifact_path, str) or not artifact_path:
            return False
        try:
            if resolver.compare(key, artifact_path=artifact_path).status is not ArtifactStatus.CURRENT:
                return False
            if key.kind is ArtifactKind.EPISODE_STORYBOARD:
                basis = resolver.resolve_basis(key)
                return basis is None or basis.kind != UPLOADED_STORYBOARD_BASIS_KIND
            return True
        except (ArtifactManifestError, OSError, RuntimeError, TypeError, ValueError):
            return False

    return is_current


def speech_signature(kind: str, item: Mapping[str, Any]) -> str | None:
    """一个条目进入视频声音依据的部分（角色台词的说话人与文本）；不能进入声音合成时为 ``None``。

    不含声音设置：合并只改说话人，说话人没变时双方的声音设置都不会进入这个条目的依据。
    """
    try:
        admission = admit_script_unit(kind, item)
        if not admission.allowed:
            return None
        return build_video_speech_basis(admission.preparation).digest
    except (TypeError, ValueError):
        return None


def grid_merge_references(
    project_dir: Path, asset_type: str, source: str, folded_derivatives: Sequence[str]
) -> tuple[set[str], dict[str, tuple[str, ...]]]:
    """会丢失共享参考图的宫格 ID，以及每张宫格当前可用的冻结参考图名字。"""
    from lib.artifacts.artifact_planner import TargetStatePlanner

    removed = [
        *asset_files_named(project_dir, ASSET_SPECS[asset_type], source),
        *(project_dir / derivative_sheet_relative_path(source, name) for name in folded_derivatives),
    ]
    removed_paths = {
        unicodedata.normalize("NFC", path.relative_to(project_dir).as_posix())
        for path in removed
        if path.is_file() and not path.is_symlink()
    }
    try:
        grids = TargetStatePlanner(project_dir).load_grid_records()
    except (ArtifactManifestError, OSError, ValueError):
        # 同 current_artifact_probe：无法读取依据的产物不计为从现行转成过期。
        return set(), {}
    outdated = {
        grid.id
        for grid in grids
        if any(
            unicodedata.normalize("NFC", reference.path) in removed_paths for reference in grid.reference_images or []
        )
    }
    names = {
        grid.id: tuple(
            reference.name for reference in grid.reference_images or [] if (project_dir / reference.path).is_file()
        )
        for grid in grids
    }
    return outdated, names


def grid_storyboard_reference_signature(item: Mapping[str, Any], reference_names: Sequence[str]) -> str | None:
    """合并只改写本格画面提示词中的 mention；它按整组冻结参考图编号，不按本格引用数组编号。"""
    image_prompt = item.get("image_prompt")
    scene = image_prompt.get("scene") if isinstance(image_prompt, Mapping) else image_prompt
    if not isinstance(scene, str):
        return None
    labels: dict[str, str] = {}
    for position, name in enumerate(reference_names):
        labels.setdefault(asset_name_comparison_key(name), reference_image_label(position))
    return render_matched_mentions(scene, labels.get)


def count_outdated_artifacts(
    before: dict[str, Any],
    after: dict[str, Any],
    asset_type: str,
    episode: int,
    is_current: CurrentArtifactProbe,
    *,
    storyboard_identities: Mapping[str, str],
    carried_references: Mapping[str, str],
    outdated_grid_ids: Collection[str],
    grid_reference_names: Mapping[str, Sequence[str]],
) -> tuple[int, int]:
    """一份正式剧本经合并改写后，将从现行转为过期的 ``(分镜图数, 视频数)``。

    只数此刻是现行、且依据随引用变化的产物（选中手动上传的分镜图不计，见 :func:`current_artifact_probe`），
    判据取各自登记依据里与引用有关的部分：

    - 分镜图：引用到的图变了（:func:`storyboard_reference_signature`）；随图迁来的衍生只换了名字，不算。
      切格分镜的依据取宫格记录冻结的参考图，合并只把随图迁来的衍生（*carried_references*，
      合并后的引用名 → 合并前的引用名）在记录里改名改路径；共享参考图丢失时整组切格分镜计入。
    - 参考生视频：画面引用变了，或角色台词的说话人变了（:func:`speech_signature`）。
    - 分镜图生视频：说话人变了。它的画面输入是分镜图本身，分镜图重新生成之前不受画面引用影响。
    """
    try:
        before_items, id_field, kind = resolve_items(before)
        after_items, _id_field, _kind = resolve_items(after)
    except ScriptEditError:
        return 0, 0
    grid_renames = {previous: current for current, previous in carried_references.items()}
    storyboards = videos = 0
    for old, new in zip(before_items, after_items, strict=True):
        if not isinstance(old, dict) or not isinstance(new, dict):
            continue
        resource_id = old.get(id_field)
        if not isinstance(resource_id, str):
            continue
        visual_changed = visual_reference_signature(old, asset_type) != visual_reference_signature(new, asset_type)
        speech_changed = speech_signature(kind, old) != speech_signature(kind, new)
        generated = old.get("generated_assets")
        generated = generated if isinstance(generated, Mapping) else {}
        if generated.get("grid_id") is not None or generated.get("grid_cell_index") is not None:
            grid_id = generated.get("grid_id")
            names = grid_reference_names.get(grid_id, ()) if isinstance(grid_id, str) else ()
            renamed = tuple(grid_renames.get(asset_name_comparison_key(name), name) for name in names)
            storyboard_changed = (
                isinstance(grid_id, str) and grid_id in outdated_grid_ids
            ) or grid_storyboard_reference_signature(old, names) != grid_storyboard_reference_signature(new, renamed)
        else:
            storyboard_changed = storyboard_reference_signature(
                old, asset_type, storyboard_identities
            ) != storyboard_reference_signature(new, asset_type, storyboard_identities)
        if kind != "video_units" and storyboard_changed:
            storyboard_key = ArtifactKey.episode_storyboard(episode, resource_id)
            storyboards += is_current(storyboard_key, generated.get("storyboard_image"))
        if speech_changed or (kind == "video_units" and visual_changed):
            videos += is_current(ArtifactKey.episode_video(episode, resource_id), generated.get("video_clip"))
    return storyboards, videos


def plan_derivative_merge(
    source_entry: Mapping[str, Any] | None,
    target_entry: Mapping[str, Any] | None,
    source: str,
    *,
    as_derivative: bool,
) -> DerivativeMergePlan:
    """规划被并角色的衍生去留：迁到保留方名下，与保留方已有衍生同名的并入那个。

    同名按 :func:`find_derivative_name` 判定，只差大小写也算（衍生图按名落盘，两者会落到同一个
    文件）；并入的衍生在引用里改用已有衍生的名字。并为衍生时先新建以被并方命名的衍生；保留方
    已有同名衍生时并入它，不新建。被并方名下与之同名的衍生同样并入。
    """
    # 保留方名下的衍生：已有的，加上本次新建与迁入的。
    kept: dict[str, None] = dict.fromkeys(derivative_table(target_entry))
    created: str | None = None
    source_derivative: str | None = None
    if as_derivative:
        source_key = asset_name_comparison_key(source)
        existing = find_derivative_name(kept, source_key)
        if existing is None:
            created = existing = source_key
            kept[source_key] = None
        source_derivative = asset_name_comparison_key(existing)
    moved: list[str] = []
    folded: list[str] = []
    names: dict[str, str] = {}
    for name in derivative_table(source_entry):
        existing = find_derivative_name(kept, name)
        if existing is None:
            moved.append(name)
            kept[name] = None
            existing = name
        else:
            folded.append(name)
        names[asset_name_comparison_key(name)] = asset_name_comparison_key(existing)
    return DerivativeMergePlan(
        created=created,
        moved=tuple(moved),
        folded=tuple(folded),
        source_derivative=source_derivative,
        derivative_names=names,
    )


__all__ = [
    "MERGEABLE_ASSET_TYPES",
    "AssetMergeEpisodeImpact",
    "AssetMergeNotFoundError",
    "AssetMergeRejectedError",
    "AssetMergeReport",
    "CurrentArtifactProbe",
    "DerivativeMergePlan",
    "MergeRejection",
    "ReferenceChanges",
    "count_outdated_artifacts",
    "current_artifact_probe",
    "grid_merge_references",
    "grid_storyboard_reference_signature",
    "merge_payload_references",
    "merged_reference",
    "plan_derivative_merge",
    "speech_signature",
    "storyboard_reference_identities",
    "storyboard_reference_signature",
    "visual_reference_signature",
]
