import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";
import { ArrowUpRight, FileText, Upload } from "lucide-react";

import { WORKSPACE_ROUTE_EPISODES } from "@/app-routes";
import { ProgressBar } from "@/components/ui/ProgressBar";
import { PrimaryButton } from "@/components/ui/PrimaryButton";
import { GHOST_BTN_CLS } from "@/components/ui/darkroom-tokens";
import { useAppStore } from "@/stores/app-store";
import type { EpisodeMeta, EpisodesView, EpisodesViewEpisode } from "@/types";
import { episodePosition } from "@/utils/episode-display";

import { ReplannedBadge } from "./ReplannedBadge";
import { UnregisteredFilesPanel } from "./UnregisteredFilesPanel";
import {
  episodeColor,
  formatSpoken,
  formatVolume,
  otherEpisodes,
  railFileGroups,
  type RailRow,
} from "./episodes-view-model";

interface EpisodesRailProps {
  projectName: string;
  view: EpisodesView;
  episodes: EpisodeMeta[];
  selected: number | null;
  onSelect: (episode: number) => void;
  onScrollToFile: (sourceFile: string) => void;
  onUpload: () => void;
  onChanged: () => void;
}

/**
 * 「分集」视图右栏：上传、源文进度、按文件分组的切出集清单与其他集。
 * 选中某一集时左栏滚动到这一集（由调用方处理）。
 */
export function EpisodesRail({
  projectName,
  view,
  episodes,
  selected,
  onSelect,
  onScrollToFile,
  onUpload,
  onChanged,
}: EpisodesRailProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const groups = railFileGroups(view, episodes);
  const others = otherEpisodes(view, episodes);
  const percent = view.units === 0 ? 0 : Math.round((view.cut_units / view.units) * 100);
  // 助手面板收起时右上角浮着 Agent 球，标题行右端的上传按钮要给它让出位置。
  const assistantFloating = !useAppStore((s) => s.assistantPanelOpen);

  return (
    <div className="space-y-5 px-4 py-5 pb-24">
      <header className={`flex items-center gap-2 ${assistantFloating ? "pr-12" : ""}`}>
        <h2 className="display-serif text-[16px] font-semibold tracking-tight text-text">
          {t("dashboard:workspace_nav_episodes")}
        </h2>
        <span className="num rounded-md border border-accent-soft bg-accent-dim px-1.5 py-px text-[10.5px] text-text-3">
          {t("dashboard:episodes_view_episode_count", { count: episodes.length })}
        </span>
        <span className="flex-1" />
        <PrimaryButton size="sm" onClick={onUpload} leadingIcon={<Upload className="h-3.5 w-3.5" aria-hidden />}>
          {t("dashboard:source_upload_title")}
        </PrimaryButton>
      </header>

      {view.unregistered.length > 0 ? (
        <UnregisteredFilesPanel
          projectName={projectName}
          files={view.unregistered}
          episodes={episodes}
          onChanged={onChanged}
        />
      ) : null}

      {view.files.length > 0 ? (
        <section aria-labelledby="episodes-rail-progress" className="space-y-2">
          <div className="flex items-baseline justify-between gap-3">
            <h3 id="episodes-rail-progress" className="text-[12.5px] font-medium text-text-2">
              {t("dashboard:episodes_view_whole_source", { count: view.files.length })}
            </h3>
            <span className="num text-[11px] text-text-3">
              {t("dashboard:episodes_view_progress", {
                cut: view.cut_units.toLocaleString(),
                total: formatVolume(t, view.units, view.unit),
              })}
            </span>
          </div>
          <ProgressBar
            value={percent}
            label={t("dashboard:episodes_view_progress_label")}
            className="h-1 overflow-hidden rounded-full bg-[oklch(0.26_0.012_265)]"
            barClassName="h-full rounded-full bg-accent"
          />
        </section>
      ) : null}

      {groups.length > 0 ? (
        <RailSection title={t("dashboard:episodes_view_cut_section")}>
          {groups.map((group) => (
            <div key={group.file.source_file} className="space-y-1.5">
              <button
                type="button"
                onClick={() => onScrollToFile(group.file.source_file)}
                className="focus-ring flex w-full items-center gap-1.5 rounded-md px-1 py-0.5 text-left text-[11.5px] text-text-3 hover:text-text"
                title={group.file.name}
              >
                <FileText className="h-3.5 w-3.5 shrink-0" aria-hidden />
                <span className="truncate">{group.file.name}</span>
              </button>
              <ul className="space-y-1.5">
                {group.rows.map((row) => (
                  <li key={row.kind === "episode" ? row.episode.episode : row.key}>
                    <RailRowView
                      row={row}
                      view={view}
                      episodes={episodes}
                      selected={row.kind === "episode" && selected === row.episode.episode}
                      onSelect={onSelect}
                    />
                  </li>
                ))}
              </ul>
              {group.tailUnits > 0 ? (
                <p className="px-1 text-[11px] text-text-4">
                  {t("dashboard:episodes_view_tail_after", { volume: formatVolume(t, group.tailUnits, view.unit) })}
                </p>
              ) : null}
            </div>
          ))}
        </RailSection>
      ) : null}

      {others.length > 0 ? (
        <RailSection title={t("dashboard:episodes_view_other_section")}>
          <ul className="space-y-1.5">
            {others.map(({ episode, info }) => (
              <li key={episode.episode}>
                <EpisodeCard
                  episode={episode}
                  info={info}
                  view={view}
                  episodes={episodes}
                  selected={selected === episode.episode}
                  onSelect={onSelect}
                  origin
                />
              </li>
            ))}
          </ul>
        </RailSection>
      ) : null}
    </div>
  );
}

function RailSection({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="space-y-2.5">
      <h3 className="text-[12.5px] font-medium text-text-2">{title}</h3>
      {children}
    </section>
  );
}

function RailRowView({
  row,
  view,
  episodes,
  selected,
  onSelect,
}: {
  row: RailRow;
  view: EpisodesView;
  episodes: EpisodeMeta[];
  selected: boolean;
  onSelect: (episode: number) => void;
}) {
  const { t } = useTranslation("dashboard");
  if (row.kind === "gap") {
    return (
      <div
        className="rounded-md px-2.5 py-1.5 text-[11.5px] text-text-4"
        style={{ border: "1px dashed var(--color-accent-soft)" }}
      >
        {t("episodes_view_gap_row", { volume: formatVolume(t, row.units, view.unit) })}
      </div>
    );
  }
  return (
    <EpisodeCard
      episode={row.episode}
      info={row.info}
      view={view}
      episodes={episodes}
      selected={selected}
      onSelect={onSelect}
    />
  );
}

function EpisodeCard({
  episode,
  info,
  view,
  episodes,
  selected,
  onSelect,
  origin = false,
}: {
  episode: EpisodeMeta;
  info: EpisodesViewEpisode | null;
  view: EpisodesView;
  episodes: EpisodeMeta[];
  selected: boolean;
  onSelect: (episode: number) => void;
  origin?: boolean;
}) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [, setLocation] = useLocation();
  const id = episode.episode;
  const color = episodeColor(id);
  const position = episodePosition(episodes, id);
  const title = episode.title?.trim();
  const hook = episode.hook?.trim();
  return (
    <div
      className="rounded-md transition-colors"
      style={{
        borderLeft: `3px solid ${color}`,
        background: selected ? "var(--color-accent-dim)" : "oklch(0.2 0.011 265 / 0.55)",
      }}
    >
      <button
        type="button"
        onClick={() => onSelect(id)}
        aria-pressed={selected}
        className="focus-ring block w-full rounded-md px-2.5 py-2 text-left hover:bg-[oklch(0.26_0.012_265/0.45)]"
      >
        <span className="flex flex-wrap items-center gap-x-1.5 gap-y-1 text-[12.5px]">
          <span className="font-semibold" style={{ color }}>
            {position === null ? t("common:episode_unlisted_name") : t("common:episode_position_name", { position })}
          </span>
          <span className="min-w-0 text-text">{title || t("dashboard:episodes_view_untitled")}</span>
          {origin && info ? (
            <span className="rounded border border-hairline px-1 py-px text-[10.5px] text-text-3">
              {t(`dashboard:episodes_view_origin_${info.origin}`)}
            </span>
          ) : null}
          {episode.ledger_status === "stale" ? <ReplannedBadge /> : null}
        </span>
        {info?.units != null ? (
          <span className="num mt-0.5 block text-[10.5px] text-text-4">
            {formatVolume(t, info.units, view.unit)}
            {info.spoken_seconds != null ? ` · ${formatSpoken(t, info.spoken_seconds)}` : ""}
          </span>
        ) : null}
        {hook ? (
          <span className="mt-1 block text-[11.5px] leading-[1.55] text-text-3">
            {t("dashboard:episodes_view_hook", { hook })}
          </span>
        ) : null}
        {info?.first_sentence ? (
          <span className="mt-1 block truncate text-[11.5px] text-text-3" title={info.first_sentence}>
            {t("dashboard:episodes_view_first_sentence", { sentence: info.first_sentence })}
          </span>
        ) : null}
        {info?.last_sentence ? (
          <span className="block truncate text-[11.5px] text-text-3" title={info.last_sentence}>
            {t("dashboard:episodes_view_last_sentence", { sentence: info.last_sentence })}
          </span>
        ) : null}
      </button>
      {selected ? (
        <div className="px-2.5 pb-2">
          <button
            type="button"
            className={GHOST_BTN_CLS}
            onClick={() => setLocation(`/${WORKSPACE_ROUTE_EPISODES}/${id}`)}
          >
            <ArrowUpRight className="h-3.5 w-3.5" aria-hidden />
            {t("dashboard:episodes_view_open_episode")}
          </button>
        </div>
      ) : null}
    </div>
  );
}
