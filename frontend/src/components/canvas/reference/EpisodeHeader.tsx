import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useCostStore } from "@/stores/cost-store";
import { formatCost } from "@/utils/cost-format";
import type { ReferenceVideoUnit } from "@/types";

export interface EpisodeHeaderProps {
  episode: number;
  title: string;
  units: ReferenceVideoUnit[];
}

export function EpisodeHeader({ episode, title, units }: EpisodeHeaderProps) {
  const { t } = useTranslation("dashboard");
  const epCost = useCostStore((s) => s._episodeIndex.get(episode));

  const stats = useMemo(() => {
    const total = units.length;
    const ready = units.filter((u) => !!u.generated_assets.video_clip).length;
    const totalDur = units.reduce((s, u) => s + u.duration_seconds, 0);
    const percent = total > 0 ? Math.round((ready / total) * 100) : 0;
    return {
      total,
      ready,
      totalDur,
      percent,
      estimated: formatCost(epCost?.totals.estimate.video),
      actual: formatCost(epCost?.totals.actual.video),
    };
  }, [units, epCost]);

  const epLabel = t("episode_header_episode_chip", {
    number: String(episode).padStart(2, "0"),
  });

  return (
    <div className="flex flex-wrap items-end justify-between gap-5 border-b border-[var(--color-hairline)] bg-[linear-gradient(180deg,oklch(0.22_0.014_290_/_0.4),oklch(0.20_0.012_250_/_0.15))] px-6 py-4">
      <div className="min-w-0 flex-1">
        <div className="mb-2 flex flex-wrap items-center gap-2.5">
          <span
            className="font-mono text-[10px] font-semibold uppercase tracking-wider text-[var(--color-accent-2)]"
            style={{ background: "var(--color-accent-dim)", padding: "2px 8px", borderRadius: 4 }}
            translate="no"
          >
            {epLabel}
          </span>
          <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-4)]">
            {t("reference_episode_header_units", { count: stats.total })} · ~{stats.totalDur}s
          </span>
          <span aria-hidden="true" className="h-[3px] w-[3px] rounded-full bg-[var(--color-hairline)]" />
          <span className="inline-flex items-center gap-1.5 text-[11px] text-[var(--color-text-3)]">
            <span
              aria-hidden="true"
              className="h-[5px] w-[5px] rounded-full bg-[var(--color-accent)] motion-safe:animate-pulse"
            />
            <span className="tabular-nums">
              {stats.ready}/{stats.total} · {stats.percent}%
            </span>
          </span>
        </div>
        <h1
          className="m-0 truncate text-[26px] font-medium leading-[1.15] tracking-tight"
          style={{ fontFamily: "var(--font-display)" }}
        >
          {title}
        </h1>
      </div>

      <div className="flex shrink-0 items-stretch gap-0">
        {(
          [
            {
              key: "ready",
              label: t("reference_episode_header_ready"),
              value: `${stats.ready}/${stats.total}`,
              accent: false,
            },
            {
              key: "estimated",
              label: t("reference_episode_header_estimated"),
              value: stats.estimated,
              accent: false,
            },
            {
              key: "actual",
              label: t("reference_episode_header_actual"),
              value: stats.actual,
              accent: true,
            },
          ] as const
        ).map((s, i) => (
          <div
            key={s.key}
            className={`min-w-[64px] px-3.5 ${
              i === 0 ? "" : "border-l border-[var(--color-hairline-soft)]"
            }`}
          >
            <div className="font-mono text-[10px] font-semibold uppercase tracking-wider text-[var(--color-text-4)]">
              {s.label}
            </div>
            <div
              className={`mt-0.5 font-mono text-sm font-semibold tabular-nums ${
                s.accent ? "text-[var(--color-accent-2)]" : "text-[var(--color-text)]"
              }`}
            >
              {s.value}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
