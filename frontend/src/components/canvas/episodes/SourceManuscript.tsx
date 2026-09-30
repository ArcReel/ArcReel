import { memo } from "react";
import { useTranslation } from "react-i18next";
import { FileText, TriangleAlert } from "lucide-react";

import type { EpisodeMeta, EpisodesView, EpisodesViewEpisode, EpisodesViewFile, EpisodesViewSegment } from "@/types";
import { episodePosition } from "@/utils/episode-display";

import { ReplannedBadge } from "./ReplannedBadge";
import { episodeColor, formatSpoken, formatVolume, paragraphs } from "./episodes-view-model";

/** 原文阅读列：衬线正文，行长控制在约 40 个汉字。 */
const MANUSCRIPT_TEXT_CLS = "text-[14.5px] leading-[1.95] [font-family:var(--font-editorial)]";

interface SourceManuscriptProps {
  view: EpisodesView;
  episodes: EpisodeMeta[];
  selected: number | null;
  onSelect: (episode: number) => void;
  registerEpisodeHeader: (episode: number, el: HTMLElement | null) => void;
  registerFileBar: (sourceFile: string, el: HTMLElement | null) => void;
}

/**
 * 「分集」视图左栏：整本源文全文，按集分段。
 *
 * 每个文件开头是文件条；每集顶部是集标题条，段落左侧有集色竖条；夹在切出集之间的未切分原文显示为虚线卡片，
 * 最后一个切出集之后是「以下内容尚未分集」分隔线。
 */
export function SourceManuscript({
  view,
  episodes,
  selected,
  onSelect,
  registerEpisodeHeader,
  registerFileBar,
}: SourceManuscriptProps) {
  const info = new Map(view.episodes.map((episode) => [episode.episode, episode]));
  const meta = new Map(episodes.map((episode) => [episode.episode, episode]));
  // 第一个尚未分集的段之前挂分隔线，之后的尾段只淡化显示
  const firstTail = view.files
    .flatMap((file) => file.segments.map((segment) => ({ file, segment })))
    .find(({ segment }) => segment.kind === "unsplit" && !segment.gap);

  return (
    <div className="pb-24">
      {view.files.map((file, index) => (
        <section key={file.source_file} aria-label={file.name}>
          <FileBar file={file} index={index} total={view.files.length} unit={view.unit} register={registerFileBar} />
          {file.segments.map((segment) => {
            if (segment.kind === "episode" && segment.episode !== null) {
              const episodeInfo = info.get(segment.episode);
              return (
                <EpisodeBlock
                  key={`${segment.episode}`}
                  segment={segment}
                  episode={meta.get(segment.episode)}
                  info={episodeInfo}
                  position={episodePosition(episodes, segment.episode)}
                  unit={view.unit}
                  selected={selected === segment.episode}
                  onSelect={onSelect}
                  register={registerEpisodeHeader}
                />
              );
            }
            return (
              <UnsplitBlock
                key={`${file.source_file}:${segment.start}`}
                segment={segment}
                unit={view.unit}
                divider={firstTail?.segment === segment}
              />
            );
          })}
        </section>
      ))}
    </div>
  );
}

function FileBar({
  file,
  index,
  total,
  unit,
  register,
}: {
  file: EpisodesViewFile;
  index: number;
  total: number;
  unit: EpisodesView["unit"];
  register: (sourceFile: string, el: HTMLElement | null) => void;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div
      ref={(el) => register(file.source_file, el)}
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
    </div>
  );
}

const EpisodeBlock = memo(function EpisodeBlock({
  segment,
  episode,
  info,
  position,
  unit,
  selected,
  onSelect,
  register,
}: {
  segment: EpisodesViewSegment;
  episode: EpisodeMeta | undefined;
  info: EpisodesViewEpisode | undefined;
  position: number | null;
  unit: EpisodesView["unit"];
  selected: boolean;
  onSelect: (episode: number) => void;
  register: (episode: number, el: HTMLElement | null) => void;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const id = segment.episode ?? 0;
  const color = episodeColor(id);
  const title = episode?.title?.trim();
  return (
    <article className="mb-6" aria-labelledby={`episode-${id}-title`}>
      <button
        type="button"
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
            {position === null ? t("common:episode_unlisted_name") : t("common:episode_position_name", { position })}
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
      <div className="flex gap-4">
        <span aria-hidden className="w-[3px] shrink-0 rounded-full" style={{ background: episodeColor(id, 0.7) }} />
        <div
          className={`min-w-0 flex-1 space-y-3 text-text-2 ${MANUSCRIPT_TEXT_CLS}`}
          style={{ contentVisibility: "auto", containIntrinsicSize: "auto 600px" }}
        >
          {paragraphs(segment.text).map((line, index) => (
            <p key={index}>{line}</p>
          ))}
        </div>
      </div>
    </article>
  );
});

function UnsplitBlock({
  segment,
  unit,
  divider,
}: {
  segment: EpisodesViewSegment;
  unit: EpisodesView["unit"];
  divider: boolean;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div className="mb-6">
      {segment.gap ? (
        <div
          className="mb-3 flex flex-wrap items-center gap-x-2 gap-y-1 rounded-md px-3 py-2 text-[12px] text-text-3"
          style={{ border: "1px dashed var(--color-accent-soft)" }}
        >
          <span className="font-medium text-text-2">{t("episodes_view_gap_title")}</span>
          <span className="num text-[11px] text-text-4">{formatVolume(t, segment.units, unit)}</span>
          <span className="basis-full text-[11.5px] text-text-4">{t("episodes_view_gap_hint")}</span>
        </div>
      ) : divider ? (
        <div role="separator" className="mb-4 mt-2 flex items-center gap-3 text-[12px] text-accent-2">
          <span aria-hidden className="h-px flex-1" style={{ background: "linear-gradient(90deg, transparent, var(--color-accent))" }} />
          {t("episodes_view_unsplit_divider")}
          <span aria-hidden className="h-px flex-1" style={{ background: "linear-gradient(270deg, transparent, var(--color-accent))" }} />
        </div>
      ) : null}
      <div
        className={`space-y-3 pl-[19px] text-text-4 ${MANUSCRIPT_TEXT_CLS}`}
        style={{ contentVisibility: "auto", containIntrinsicSize: "auto 600px" }}
      >
        {paragraphs(segment.text).map((line, index) => (
          <p key={index}>{line}</p>
        ))}
      </div>
    </div>
  );
}
