import { Fragment, memo, useMemo, type MouseEvent, type ReactNode, type CSSProperties } from "react";
import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { FileText, MoveHorizontal, TriangleAlert } from "lucide-react";

import type { EpisodeMeta, EpisodesView, EpisodesViewEpisode, EpisodesViewFile, EpisodesViewSegment } from "@/types";
import { Button } from "@/components/ui/button";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { episodeDisplayName, episodePosition } from "@/utils/episode-display";

import { PlanGapButton } from "./PlanGapButton";
import { BoundaryRule, BoundaryTick, LaneBar, WaitingBadge } from "./ReplanCompareMarks";
import { ReplannedBadge } from "./ReplannedBadge";
import { SourceFileActions } from "./SourceFileActions";
import { SourceFileKindControl } from "./SourceFileKindControl";
import { episodeHue, formatSpoken, formatVolume } from "./episodes-view-model";
import { adjacentBoundaries, pointInRun, textRuns, type ManuscriptPoint, type TextRun } from "./manual-split-model";
import { lanesContinue, type ReplanCompare } from "./replan-compare-model";

/** 原文阅读列：衬线正文，行距加大；行长由视图的 40em 限宽控制在约 40 个汉字。 */
const MANUSCRIPT_TEXT_CLS = "flex cursor-text flex-col gap-3 font-editorial text-base leading-loose";

interface SourceManuscriptProps {
  projectName: string;
  view: EpisodesView;
  episodes: EpisodeMeta[];
  registerEpisodeHeader: (episode: number, el: HTMLElement | null) => void;
  registerFileBar: (sourceFile: string, el: HTMLElement | null) => void;
  /** 手工切分的插入光标与它下方的操作条；`episode` 是光标取色的集，null 取品牌色。没有落点时为 null。 */
  caret: { point: ManuscriptPoint; episode: number | null; toolbar: ReactNode } | null;
  /** 正在移动的分界（左侧一集的集 ID）。 */
  moving: number | null;
  onPlace: (point: ManuscriptPoint) => void;
  onToggleMoving: (left: number) => void;
  /** 有新的分集方案时新旧两种分法的对照；没有方案或方案已过时为 null。 */
  compare: ReplanCompare | null;
}

type InlineMark = { offset: number; node: ReactNode };
type CaretMark = InlineMark | null;

/** 一段原文所在文件的对照与该文件里新旧分界不同的位置；没有对照时为 null。 */
type SegmentCompare = { compare: ReplanCompare; diffs: number[] } | null;

/**
 * 正文容器跳过屏幕外段落的渲染（`offscreen-skip`）。插入光标所在的段落照常绘制：
 * `content-visibility: auto` 的绘制裁剪会把伸出正文的操作条裁掉。
 */
function manuscriptTextClass(hostsCaret: boolean) {
  return cn(!hostsCaret && "offscreen-skip");
}

/** 光标画在哪一行：文件内第一个结尾不早于落点的行；落在行与行之间的空白里时画在下一行开头。 */
function caretRunStart(file: EpisodesViewFile, offset: number): number | null {
  let last: number | null = null;
  for (const segment of file.segments) {
    for (const run of textRuns(segment.text, segment.start)) {
      if (run.start + [...run.text].length >= offset) return run.start;
      last = run.start;
    }
  }
  return last;
}

/** 点击处的落点：文本节点的父元素带 `data-file` 与 `data-run`（这段文字在文件内的码位起点）。 */
function pointFromMouse(event: MouseEvent): ManuscriptPoint | null {
  const doc = document as Document & {
    caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
  };
  let node: Node | null = null;
  let index = 0;
  if (typeof doc.caretPositionFromPoint === "function") {
    const position = doc.caretPositionFromPoint(event.clientX, event.clientY);
    node = position?.offsetNode ?? null;
    index = position?.offset ?? 0;
  } else if (typeof document.caretRangeFromPoint === "function") {
    const range = document.caretRangeFromPoint(event.clientX, event.clientY);
    node = range?.startContainer ?? null;
    index = range?.startOffset ?? 0;
  }
  if (!node || node.nodeType !== Node.TEXT_NODE) return null;
  const host = node.parentElement;
  const file = host?.getAttribute("data-file");
  const run = host?.getAttribute("data-run");
  if (file == null || run == null) return null;
  return pointInRun(Number(file), Number(run), node.textContent ?? "", index);
}

/**
 * 「分集」视图左栏：整本源文全文，按集分段。
 *
 * 每个文件开头是文件条；每集顶部是集标题条，段落左侧有集色竖条；夹在切出集之间的未切分原文显示为虚线卡片，
 * 最后一个切出集之后是「以下内容尚未分集」分隔线。
 *
 * 有新的分集方案时，正文右侧再加一条新方案的色条：左侧色条是现有分集，右侧是新方案，分界不同处画琥珀色虚线；
 * 方案还没生成到的原文是虚线色条，原文全在那里的现有集标「等待规划」。
 */
export function SourceManuscript({
  projectName,
  view,
  episodes,
  registerEpisodeHeader,
  registerFileBar,
  caret,
  moving,
  onPlace,
  onToggleMoving,
  compare,
}: SourceManuscriptProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const info = new Map(view.episodes.map((episode) => [episode.episode, episode]));
  const meta = new Map(episodes.map((episode) => [episode.episode, episode]));
  // 第一个尚未分集的段之前挂分隔线，之后的尾段只淡化显示
  const firstTail = view.files
    .flatMap((file) => file.segments.map((segment) => ({ file, segment })))
    .find(({ segment }) => segment.kind === "unsplit" && !segment.gap);

  const allBoundaries = adjacentBoundaries(view);
  const movingPair = moving === null ? null : allBoundaries.find((b) => b.left === moving);
  const fileCompares = useMemo<SegmentCompare[]>(
    () => view.files.map((_, index) => (compare === null ? null : { compare, diffs: compare.diffs(index) })),
    [view.files, compare],
  );
  const caretMark = (fileIndex: number): CaretMark =>
    caret !== null && caret.point.file === fileIndex
      ? { offset: caret.point.offset, node: <CaretMarker episode={caret.episode}>{caret.toolbar}</CaretMarker> }
      : null;

  const onMouseUp = (event: MouseEvent) => {
    if (event.button !== 0 || (event.target as HTMLElement).closest("[data-no-caret]")) return;
    const selection = window.getSelection();
    if (selection && !selection.isCollapsed) return;
    const point = pointFromMouse(event);
    if (point !== null) onPlace(point);
  };

  return (
    // eslint-disable-next-line jsx-a11y/no-static-element-interactions -- 原文区的点击用来放置分集点，键盘操作（←/→ 微调、Enter 确认、Esc 取消）由视图在 window 上统一接管
    <div data-manuscript className="pb-24" onMouseUp={onMouseUp}>
      {view.files.map((file, index) => {
        const mark = caretMark(index);
        const hostRun = mark === null ? null : caretRunStart(file, mark.offset);
        const boundaries = new Map(allBoundaries.filter((b) => b.file === index).map((b) => [b.right, b]));
        return (
          <section key={file.source_file} aria-label={file.name}>
            <FileBar
              projectName={projectName}
              file={file}
              episodes={episodes}
              index={index}
              total={view.files.length}
              unit={view.unit}
              register={registerFileBar}
            />
            {file.segments.map((segment) => {
              const hosted = hostRun !== null && segment.start <= hostRun && hostRun < segment.end ? mark : null;
              const dimmed =
                movingPair != null &&
                !(segment.kind === "episode" && (segment.episode === movingPair.left || segment.episode === movingPair.right));
              if (segment.kind === "episode" && segment.episode !== null) {
                const boundary = segment.continued ? undefined : boundaries.get(segment.episode);
                const episodeInfo = info.get(segment.episode);
                return (
                  <Fragment key={`${segment.episode}`}>
                    {boundary ? (
                      <BoundaryButton
                        active={moving === boundary.left}
                        label={t("dashboard:manual_split_boundary", {
                          left: episodeDisplayName(episodes, boundary.left, t),
                          right: episodeDisplayName(episodes, boundary.right, t),
                        })}
                        activeLabel={t("dashboard:manual_split_boundary_moving")}
                        onClick={() => onToggleMoving(boundary.left)}
                      />
                    ) : null}
                    <EpisodeBlock
                      fileIndex={index}
                      segment={segment}
                      episode={meta.get(segment.episode)}
                      info={episodeInfo}
                      position={episodePosition(episodes, segment.episode)}
                      unit={view.unit}
                      dimmed={dimmed}
                      caret={hosted}
                      hostRun={hosted ? hostRun : null}
                      compare={fileCompares[index]}
                      waiting={compare?.waiting.has(segment.episode) ?? false}
                      register={registerEpisodeHeader}
                    />
                  </Fragment>
                );
              }
              return (
                <UnsplitBlock
                  key={`${file.source_file}:${segment.start}`}
                  fileIndex={index}
                  sourceFile={file.source_file}
                  segment={segment}
                  unit={view.unit}
                  divider={firstTail?.segment === segment}
                  dimmed={dimmed}
                  caret={hosted}
                  hostRun={hosted ? hostRun : null}
                  compare={fileCompares[index]}
                  planBlocked={view.replan !== null ? t("dashboard:replan_pending_hint") : null}
                />
              );
            })}
          </section>
        );
      })}
    </div>
  );
}

function CaretMarker({ episode, children }: { episode: number | null; children: ReactNode }) {
  return (
    <span
      data-no-caret
      className="relative inline-block h-[1.35em] w-0 align-text-bottom"
      style={{ "--episode-hue": episodeHue(episode ?? 0) } as CSSProperties}
    >
      <span
        aria-hidden
        className={cn(
          "absolute top-0 -left-px h-full w-0.5 animate-caret-blink rounded-sm",
          episode === null ? "bg-primary" : "bg-episode",
        )}
      />
      {children}
    </span>
  );
}

/** 一行原文：文字带上 `data-file` / `data-run` 供点击换算偏移，在各标记的位置把文字断开插入标记。 */
function RunText({ fileIndex, run, marks }: { fileIndex: number; run: TextRun; marks: InlineMark[] }) {
  if (marks.length === 0) {
    return (
      <span data-file={fileIndex} data-run={run.start}>
        {run.text}
      </span>
    );
  }
  const chars = [...run.text];
  const parts: ReactNode[] = [];
  let from = 0;
  for (const [index, mark] of marks.entries()) {
    const split = Math.min(Math.max(mark.offset - run.start, from), chars.length);
    parts.push(
      <span key={`t${index}`} data-file={fileIndex} data-run={run.start + from}>
        {chars.slice(from, split).join("")}
      </span>,
      <Fragment key={`m${index}`}>{mark.node}</Fragment>,
    );
    from = split;
  }
  parts.push(
    <span key="tail" data-file={fileIndex} data-run={run.start + from}>
      {chars.slice(from).join("")}
    </span>,
  );
  return parts;
}

function SegmentText({
  fileIndex,
  segment,
  caret,
  hostRun,
  compare,
}: {
  fileIndex: number;
  segment: EpisodesViewSegment;
  caret: CaretMark;
  hostRun: number | null;
  compare: SegmentCompare;
}) {
  const runs = textRuns(segment.text, segment.start).map((run) => ({ run, end: run.start + [...run.text].length }));
  const lanes = compare ? runs.map(({ run, end }) => compare.compare.lanes(fileIndex, run.start, end)) : null;
  return runs.map(({ run, end }, index) => {
    const marks: InlineMark[] = (compare?.diffs ?? [])
      .filter((offset) => run.start < offset && offset < end)
      .map((offset) => ({ offset, node: <BoundaryTick /> }));
    if (caret !== null && run.start === hostRun) marks.push(caret);
    marks.sort((a, b) => a.offset - b.offset);
    const pieces = lanes?.[index] ?? [];
    const next = lanes?.[index + 1];
    return (
      <p key={run.start} className={lanes ? "relative" : undefined}>
        {compare?.diffs.includes(run.start) ? <BoundaryRule /> : null}
        <RunText fileIndex={fileIndex} run={run} marks={marks} />
        {pieces.length > 0 ? (
          <LaneBar
            pieces={pieces}
            start={run.start}
            end={end}
            continues={next !== undefined && lanesContinue(pieces, next)}
          />
        ) : null}
      </p>
    );
  });
}

function BoundaryButton({
  active,
  label,
  activeLabel,
  onClick,
}: {
  active: boolean;
  label: string;
  activeLabel: string;
  onClick: () => void;
}) {
  return (
    <div data-no-caret className="-mt-3 mb-3 flex items-center gap-2">
      <span aria-hidden className={cn("h-px flex-1", active ? "bg-primary" : "bg-input")} />
      {/* 两集名称都很长时按钮随原文列收窄，文字截断，全文在悬停提示与可访问名称里 */}
      <Button
        variant={active ? "secondary" : "outline"}
        size="xs"
        className="max-w-full min-w-0 shrink"
        onClick={onClick}
        aria-pressed={active}
      >
        <MoveHorizontal aria-hidden data-icon="inline-start" />
        <TruncatedText text={active ? activeLabel : label} focusable={false} />
      </Button>
      <span aria-hidden className={cn("h-px flex-1", active ? "bg-primary" : "bg-input")} />
    </div>
  );
}

function FileBar({
  projectName,
  file,
  episodes,
  index,
  total,
  unit,
  register,
}: {
  projectName: string;
  file: EpisodesViewFile;
  episodes: EpisodeMeta[];
  index: number;
  total: number;
  unit: EpisodesView["unit"];
  register: (sourceFile: string, el: HTMLElement | null) => void;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div
      ref={(el) => register(file.source_file, el)}
      data-no-caret
      className="mt-10 mb-4 flex scroll-mt-4 flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-input bg-card px-3 py-2 text-xs first:mt-4"
    >
      <FileText className="size-3.5 shrink-0 text-muted-foreground" aria-hidden />
      <span className="num text-muted-foreground">
        {index + 1} / {total}
      </span>
      <span className="flex min-w-0 flex-1 flex-col">
        <TruncatedText text={file.name} className="text-sm font-medium text-foreground" />
        {file.original_filename && file.original_filename !== file.name ? (
          <TruncatedText
            text={t("episodes_view_file_original", { name: file.original_filename })}
            className="text-muted-foreground"
          />
        ) : null}
      </span>
      <SourceFileKindControl projectName={projectName} file={file} episodes={episodes} />
      {file.changed_outside ? (
        <span className="inline-flex items-center gap-1 text-warn">
          <TriangleAlert className="size-3.5" aria-hidden />
          {t("episodes_view_file_changed_outside")}
        </span>
      ) : null}
      {file.missing ? (
        <span className="inline-flex items-center gap-1 text-warn">
          <TriangleAlert className="size-3.5" aria-hidden />
          {t("episodes_view_file_missing")}
        </span>
      ) : (
        <span className="num text-muted-foreground">
          {t("episodes_view_file_coverage", {
            cut: file.cut_units.toLocaleString(),
            total: formatVolume(t, file.units, unit),
          })}
        </span>
      )}
      <SourceFileActions projectName={projectName} file={file} index={index} total={total} />
    </div>
  );
}

const EpisodeBlock = memo(function EpisodeBlock({
  fileIndex,
  segment,
  episode,
  info,
  position,
  unit,
  dimmed,
  caret,
  hostRun,
  compare,
  waiting,
  register,
}: {
  fileIndex: number;
  segment: EpisodesViewSegment;
  episode: EpisodeMeta | undefined;
  info: EpisodesViewEpisode | undefined;
  position: number | null;
  unit: EpisodesView["unit"];
  dimmed: boolean;
  caret: CaretMark;
  hostRun: number | null;
  compare: SegmentCompare;
  /** 新的分集方案还没生成到这一集。 */
  waiting: boolean;
  register: (episode: number, el: HTMLElement | null) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const id = segment.episode ?? 0;
  const title = episode?.title?.trim();
  const name =
    position === null ? t("common:episode_unlisted_name") : t("common:episode_position_name", { position });
  const blockCls = cn("mb-6 transition-opacity duration-fast", dimmed && "opacity-45");
  // 跨文件的一集只在起点所在文件里显示完整集头（也只有它登记为滚动目标）；后续文件里的部分只标明接续
  if (segment.continued) {
    return (
      <article
        data-episode-block={id}
        className={blockCls}
        style={{ "--episode-hue": episodeHue(id) } as CSSProperties}
        aria-label={name}
      >
        <p
          data-no-caret
          className="mb-2 flex items-baseline gap-2.5 rounded-md border-l-3 border-episode bg-muted/40 px-3 py-1 text-xs"
        >
          <span className="text-episode">{t("dashboard:episodes_view_episode_continued", { name })}</span>
          <span className="num text-muted-foreground">{formatVolume(t, segment.units, unit)}</span>
        </p>
        <EpisodeBody fileIndex={fileIndex} segment={segment} caret={caret} hostRun={hostRun} compare={compare} />
      </article>
    );
  }
  return (
    <article
      data-episode-block={id}
      className={blockCls}
      style={{ "--episode-hue": episodeHue(id) } as CSSProperties}
      aria-labelledby={`episode-${id}-title`}
    >
      <header
        data-no-caret
        ref={(el) => register(id, el)}
        className="mb-2 flex scroll-mt-4 flex-col gap-1 rounded-md border-l-3 border-episode bg-muted/40 px-3 py-2"
      >
        <span className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <h3 id={`episode-${id}-title`} className="flex items-baseline gap-2.5 text-sm">
            <span className="shrink-0 font-semibold whitespace-nowrap text-episode">{name}</span>
            <span className="text-foreground">{title || t("dashboard:episodes_view_untitled")}</span>
          </h3>
          <span className="num text-xs text-muted-foreground">
            {formatVolume(t, segment.units, unit)}
            {info?.spoken_seconds != null ? ` · ${formatSpoken(t, info.spoken_seconds)}` : ""}
          </span>
          {episode?.ledger_status === "stale" ? <ReplannedBadge /> : null}
          {waiting ? <WaitingBadge /> : null}
        </span>
        {episode?.hook?.trim() ? (
          <span className="text-xs text-muted-foreground">
            {t("dashboard:episodes_view_hook", { hook: episode.hook.trim() })}
          </span>
        ) : null}
      </header>
      <EpisodeBody fileIndex={fileIndex} segment={segment} caret={caret} hostRun={hostRun} compare={compare} />
    </article>
  );
});

function EpisodeBody({
  fileIndex,
  segment,
  caret,
  hostRun,
  compare,
}: {
  fileIndex: number;
  segment: EpisodesViewSegment;
  caret: CaretMark;
  hostRun: number | null;
  compare: SegmentCompare;
}) {
  return (
    <div className="flex gap-4">
      <span aria-hidden className="w-0.75 shrink-0 rounded-full bg-episode/70" />
      <div
        className={cn(
          "min-w-0 flex-1 text-subtle-foreground",
          MANUSCRIPT_TEXT_CLS,
          compare && "pr-6",
          manuscriptTextClass(caret !== null),
        )}
      >
        <SegmentText fileIndex={fileIndex} segment={segment} caret={caret} hostRun={hostRun} compare={compare} />
      </div>
    </div>
  );
}

function UnsplitBlock({
  fileIndex,
  sourceFile,
  segment,
  unit,
  divider,
  dimmed,
  caret,
  hostRun,
  compare,
  planBlocked,
}: {
  fileIndex: number;
  sourceFile: string;
  segment: EpisodesViewSegment;
  unit: EpisodesView["unit"];
  divider: boolean;
  dimmed: boolean;
  caret: CaretMark;
  hostRun: number | null;
  compare: SegmentCompare;
  /** 不能规划这段原文的原因（有等待处理的新的分集方案）；可以时为 null。 */
  planBlocked: string | null;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div className={cn("mb-6 transition-opacity duration-fast", dimmed && "opacity-45")}>
      {segment.gap ? (
        <div
          data-no-caret
          className="mb-3 flex flex-col items-start gap-1.5 rounded-md border border-dashed border-primary/30 px-3 py-2 text-xs text-muted-foreground"
        >
          <span className="flex items-baseline gap-2">
            <span className="font-medium text-subtle-foreground">{t("episodes_view_gap_title")}</span>
            <span className="num">{formatVolume(t, segment.units, unit)}</span>
          </span>
          <span>{t("episodes_view_gap_hint")}</span>
          <PlanGapButton sourceFile={sourceFile} end={segment.end} blocked={planBlocked} />
        </div>
      ) : divider ? (
        <div role="separator" data-no-caret className="mt-2 mb-4 flex items-center gap-3 text-xs">
          <span aria-hidden className="h-px flex-1 bg-linear-to-r from-transparent to-primary" />
          <span className="flex flex-col items-center gap-0.5 text-center">
            <span className="text-primary">{t("episodes_view_unsplit_divider")}</span>
            <span className="text-muted-foreground">{t("manual_split_divider_hint")}</span>
          </span>
          <span aria-hidden className="h-px flex-1 bg-linear-to-l from-transparent to-primary" />
        </div>
      ) : null}
      <div
        className={cn(
          "pl-4.75 text-muted-foreground",
          MANUSCRIPT_TEXT_CLS,
          compare && "pr-6",
          manuscriptTextClass(caret !== null),
        )}
      >
        <SegmentText fileIndex={fileIndex} segment={segment} caret={caret} hostRun={hostRun} compare={compare} />
      </div>
    </div>
  );
}
