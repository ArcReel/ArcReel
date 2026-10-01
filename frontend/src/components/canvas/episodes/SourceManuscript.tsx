import { Fragment, memo, type MouseEvent, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { FileText, MoveHorizontal, TriangleAlert } from "lucide-react";

import type { EpisodeMeta, EpisodesView, EpisodesViewEpisode, EpisodesViewFile, EpisodesViewSegment } from "@/types";
import { episodeDisplayName, episodePosition } from "@/utils/episode-display";

import { PlanGapButton } from "./PlanGapButton";
import { ReplannedBadge } from "./ReplannedBadge";
import { SourceFileActions } from "./SourceFileActions";
import { SourceFileKindControl } from "./SourceFileKindControl";
import { episodeColor, formatSpoken, formatVolume } from "./episodes-view-model";
import { adjacentBoundaries, pointInRun, textRuns, type ManuscriptPoint, type TextRun } from "./manual-split-model";

/** 原文阅读列：衬线正文，行长控制在约 40 个汉字。 */
const MANUSCRIPT_TEXT_CLS = "cursor-text text-[14.5px] leading-[1.95] [font-family:var(--font-editorial)]";

interface SourceManuscriptProps {
  projectName: string;
  view: EpisodesView;
  episodes: EpisodeMeta[];
  selected: number | null;
  onSelect: (episode: number) => void;
  registerEpisodeHeader: (episode: number, el: HTMLElement | null) => void;
  registerFileBar: (sourceFile: string, el: HTMLElement | null) => void;
  /** 手工切分的插入光标与它下方的操作条；没有落点时为 null。 */
  caret: { point: ManuscriptPoint; color: string; toolbar: ReactNode } | null;
  /** 正在移动的分界（左侧一集的集 ID）。 */
  moving: number | null;
  onPlace: (point: ManuscriptPoint) => void;
  onToggleMoving: (left: number) => void;
}

type CaretMark = { offset: number; node: ReactNode } | null;

/**
 * 正文容器的样式。屏幕外的段落跳过渲染；插入光标所在的段落照常绘制，
 * `content-visibility: auto` 的绘制裁剪会把伸出正文的操作条裁掉。
 */
function manuscriptTextStyle(hostsCaret: boolean) {
  return hostsCaret ? undefined : { contentVisibility: "auto" as const, containIntrinsicSize: "auto 600px" };
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
 */
export function SourceManuscript({
  projectName,
  view,
  episodes,
  selected,
  onSelect,
  registerEpisodeHeader,
  registerFileBar,
  caret,
  moving,
  onPlace,
  onToggleMoving,
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
  const caretMark = (fileIndex: number): CaretMark =>
    caret !== null && caret.point.file === fileIndex
      ? { offset: caret.point.offset, node: <CaretMarker color={caret.color}>{caret.toolbar}</CaretMarker> }
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
                      selected={selected === segment.episode}
                      dimmed={dimmed}
                      caret={hosted}
                      hostRun={hosted ? hostRun : null}
                      onSelect={onSelect}
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

function CaretMarker({ color, children }: { color: string; children: ReactNode }) {
  return (
    <span data-no-caret className="relative inline-block h-[1.35em] w-0 align-text-bottom">
      <span
        aria-hidden
        className="absolute -left-px top-0 h-full w-[2px] rounded motion-safe:animate-pulse"
        style={{ background: color, boxShadow: `0 0 8px ${color}` }}
      />
      {children}
    </span>
  );
}

/** 一行原文：文字带上 `data-file` / `data-run` 供点击换算偏移，落点在这一行时插入光标。 */
function RunText({ fileIndex, run, caret }: { fileIndex: number; run: TextRun; caret: CaretMark }) {
  if (caret === null) {
    return (
      <span data-file={fileIndex} data-run={run.start}>
        {run.text}
      </span>
    );
  }
  const chars = [...run.text];
  const split = Math.min(Math.max(caret.offset - run.start, 0), chars.length);
  return (
    <>
      <span data-file={fileIndex} data-run={run.start}>
        {chars.slice(0, split).join("")}
      </span>
      {caret.node}
      <span data-file={fileIndex} data-run={run.start + split}>
        {chars.slice(split).join("")}
      </span>
    </>
  );
}

function SegmentText({
  fileIndex,
  segment,
  caret,
  hostRun,
}: {
  fileIndex: number;
  segment: EpisodesViewSegment;
  caret: CaretMark;
  hostRun: number | null;
}) {
  return textRuns(segment.text, segment.start).map((run) => (
    <p key={run.start}>
      <RunText fileIndex={fileIndex} run={run} caret={run.start === hostRun ? caret : null} />
    </p>
  ));
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
  const line = active ? "var(--color-accent)" : "var(--color-hairline-strong)";
  return (
    <div data-no-caret className="-mt-3 mb-3 flex items-center gap-2">
      <span aria-hidden className="h-px flex-1" style={{ background: line }} />
      <button
        type="button"
        onClick={onClick}
        aria-pressed={active}
        className="focus-ring inline-flex items-center gap-1 rounded-full border px-2 py-px text-[11px] transition-colors hover:border-accent hover:text-text"
        style={{
          borderColor: line,
          color: active ? "var(--color-accent-2)" : "var(--color-text-3)",
          background: active ? "var(--color-accent-dim)" : "oklch(0.2 0.01 265)",
        }}
      >
        <MoveHorizontal className="h-3 w-3" aria-hidden />
        {active ? activeLabel : label}
      </button>
      <span aria-hidden className="h-px flex-1" style={{ background: line }} />
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
      className="mb-4 mt-10 flex scroll-mt-4 flex-wrap items-center gap-x-3 gap-y-1 rounded-md border border-hairline-strong px-3 py-2 first:mt-2"
      style={{ background: "oklch(0.225 0.012 265 / 0.9)" }}
      title={t("episodes_view_file_original", { name: file.original_filename ?? file.name })}
    >
      <FileText className="h-3.5 w-3.5 shrink-0 text-text-3" aria-hidden />
      <span className="num text-[11px] text-text-4">
        {index + 1} / {total}
      </span>
      <span className="min-w-0 truncate text-[12.5px] font-medium text-text">{file.name}</span>
      <span className="flex-1" />
      <SourceFileKindControl projectName={projectName} file={file} episodes={episodes} />
      {file.missing ? (
        <span className="inline-flex items-center gap-1 text-[11.5px] text-[var(--color-warm)]">
          <TriangleAlert className="h-3.5 w-3.5" aria-hidden />
          {t("episodes_view_file_missing")}
        </span>
      ) : (
        <span className="num text-[11px] text-text-3">
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
  selected,
  dimmed,
  caret,
  hostRun,
  onSelect,
  register,
}: {
  fileIndex: number;
  segment: EpisodesViewSegment;
  episode: EpisodeMeta | undefined;
  info: EpisodesViewEpisode | undefined;
  position: number | null;
  unit: EpisodesView["unit"];
  selected: boolean;
  dimmed: boolean;
  caret: CaretMark;
  hostRun: number | null;
  onSelect: (episode: number) => void;
  register: (episode: number, el: HTMLElement | null) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const id = segment.episode ?? 0;
  const color = episodeColor(id);
  const title = episode?.title?.trim();
  const name =
    position === null ? t("common:episode_unlisted_name") : t("common:episode_position_name", { position });
  // 跨文件的一集只在起点所在文件里显示完整集头（也只有它登记为滚动目标）；后续文件里的部分只标明接续
  if (segment.continued) {
    return (
      <article className="mb-6 transition-opacity" style={{ opacity: dimmed ? 0.45 : 1 }} aria-label={name}>
        <button
          type="button"
          data-no-caret
          onClick={() => onSelect(id)}
          aria-pressed={selected}
          className="focus-ring mb-2 block w-full rounded-md px-3 py-1 text-left text-[12px] text-text-3 transition-colors"
          style={{
            borderLeft: `3px solid ${color}`,
            background: selected ? "var(--color-accent-dim)" : "oklch(0.21 0.01 265 / 0.4)",
          }}
        >
          <span style={{ color }}>{t("dashboard:episodes_view_episode_continued", { name })}</span>
          <span className="num ml-2.5 text-[11px] text-text-4">{formatVolume(t, segment.units, unit)}</span>
        </button>
        <EpisodeBody id={id} fileIndex={fileIndex} segment={segment} caret={caret} hostRun={hostRun} />
      </article>
    );
  }
  return (
    <article
      className="mb-6 transition-opacity"
      style={{ opacity: dimmed ? 0.45 : 1 }}
      aria-labelledby={`episode-${id}-title`}
    >
      <button
        type="button"
        data-no-caret
        ref={(el) => register(id, el)}
        onClick={() => onSelect(id)}
        aria-pressed={selected}
        className="focus-ring mb-2 block w-full scroll-mt-4 rounded-md px-3 py-2 text-left transition-colors"
        style={{
          borderLeft: `3px solid ${color}`,
          background: selected ? "var(--color-accent-dim)" : "oklch(0.21 0.01 265 / 0.6)",
        }}
      >
        <span className="flex flex-wrap items-baseline gap-x-2.5 gap-y-1">
          <span id={`episode-${id}-title`} className="text-[13px] font-semibold" style={{ color }}>
            {name}
          </span>
          <span className="min-w-0 text-[13px] text-text">{title || t("dashboard:episodes_view_untitled")}</span>
          <span className="num text-[11px] text-text-4">
            {formatVolume(t, segment.units, unit)}
            {info?.spoken_seconds != null ? ` · ${formatSpoken(t, info.spoken_seconds)}` : ""}
          </span>
          {episode?.ledger_status === "stale" ? <ReplannedBadge /> : null}
        </span>
        {episode?.hook?.trim() ? (
          <span className="mt-1 block text-[12px] leading-[1.6] text-text-3">
            {t("dashboard:episodes_view_hook", { hook: episode.hook.trim() })}
          </span>
        ) : null}
      </button>
      <EpisodeBody id={id} fileIndex={fileIndex} segment={segment} caret={caret} hostRun={hostRun} />
    </article>
  );
});

function EpisodeBody({
  id,
  fileIndex,
  segment,
  caret,
  hostRun,
}: {
  id: number;
  fileIndex: number;
  segment: EpisodesViewSegment;
  caret: CaretMark;
  hostRun: number | null;
}) {
  return (
    <div className="flex gap-4">
      <span aria-hidden className="w-[3px] shrink-0 rounded-full" style={{ background: episodeColor(id, 0.7) }} />
      <div
        className={`min-w-0 flex-1 space-y-3 text-text-2 ${MANUSCRIPT_TEXT_CLS}`}
        style={manuscriptTextStyle(caret !== null)}
      >
        <SegmentText fileIndex={fileIndex} segment={segment} caret={caret} hostRun={hostRun} />
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
  /** 不能规划这段原文的原因（有等待处理的新的分集方案）；可以时为 null。 */
  planBlocked: string | null;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="mb-6 transition-opacity" style={{ opacity: dimmed ? 0.45 : 1 }}>
      {segment.gap ? (
        <div
          data-no-caret
          className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md px-3 py-2 text-[12px] text-text-3"
          style={{ border: "1px dashed var(--color-accent-soft)" }}
        >
          <span className="font-medium text-text-2">{t("episodes_view_gap_title")}</span>
          <span className="num text-[11px] text-text-4">{formatVolume(t, segment.units, unit)}</span>
          <span className="basis-full text-[11.5px] text-text-4">{t("episodes_view_gap_hint")}</span>
          <span className="mt-1 basis-full">
            <PlanGapButton sourceFile={sourceFile} end={segment.end} blocked={planBlocked} />
          </span>
        </div>
      ) : divider ? (
        <div role="separator" data-no-caret className="mb-4 mt-2 flex items-center gap-3 text-[12px] text-accent-2">
          <span aria-hidden className="h-px flex-1" style={{ background: "linear-gradient(90deg, transparent, var(--color-accent))" }} />
          <span className="flex flex-col items-center gap-0.5 text-center">
            <span>{t("episodes_view_unsplit_divider")}</span>
            <span className="text-[11px] text-text-4">{t("manual_split_divider_hint")}</span>
          </span>
          <span aria-hidden className="h-px flex-1" style={{ background: "linear-gradient(270deg, transparent, var(--color-accent))" }} />
        </div>
      ) : null}
      <div
        className={`space-y-3 pl-[19px] text-text-4 ${MANUSCRIPT_TEXT_CLS}`}
        style={manuscriptTextStyle(caret !== null)}
      >
        <SegmentText fileIndex={fileIndex} segment={segment} caret={caret} hostRun={hostRun} />
      </div>
    </div>
  );
}
