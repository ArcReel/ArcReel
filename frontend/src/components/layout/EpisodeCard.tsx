import { useTranslation } from "react-i18next";
import type { EpisodeMeta } from "@/types";
import { useCostStore } from "@/stores/cost-store";
import { totalBreakdown } from "@/utils/cost-format";

interface EpisodeCardProps {
  ep: EpisodeMeta;
  active: boolean;
  onClick: () => void;
}

const STATUS_COLOR: Record<string, string> = {
  completed: "oklch(0.74 0.08 155)",
  in_production: "var(--color-accent)",
  scripted: "oklch(0.60 0.02 250)",
  draft: "oklch(0.46 0.01 250)",
  missing: "oklch(0.46 0.01 250)",
};

const STATUS_LABEL_KEY: Record<string, string> = {
  completed: "dashboard:episode_status_done",
  in_production: "dashboard:episode_status_active",
  scripted: "dashboard:episode_status_draft",
  draft: "dashboard:episode_status_draft",
  missing: "dashboard:episode_status_idea",
};

/**
 * 侧栏分集卡片：左缩略 (E1 字符) + 中标题/状态/进度 + 右费用。
 * Active 态有 accent 紫边框 + 玻璃面板背景。
 */
export function EpisodeCard({ ep, active, onClick }: EpisodeCardProps) {
  const { t } = useTranslation(["dashboard"]);
  const status = ep.status ?? "draft";
  const statusColor = STATUS_COLOR[status] ?? STATUS_COLOR.draft;
  const statusLabel = t(STATUS_LABEL_KEY[status] ?? STATUS_LABEL_KEY.draft);
  const isActive = status === "in_production";

  // 进度：优先用 storyboards/videos completed/total
  const totalShots = ep.scenes_count ?? ep.storyboards?.total ?? ep.units_count ?? 0;
  const completedShots = ep.videos?.completed ?? 0;
  const progress =
    totalShots > 0 ? Math.round((completedShots / totalShots) * 100) : 0;
  const showProgress = totalShots > 0 && (active || progress > 0);

  // 实际费用
  const episodeCost = useCostStore((s) => s.getEpisodeCost(ep.episode));
  const spentBreakdown = episodeCost ? totalBreakdown(episodeCost.totals.actual) : null;
  // spentBreakdown 是 Record<currency, number>，取主要币种
  const spentEntries = spentBreakdown ? Object.entries(spentBreakdown).filter(([, v]) => v > 0) : [];
  const primaryCost = spentEntries.find(([c]) => c === "USD") ?? spentEntries[0];
  const costText = primaryCost
    ? `${primaryCost[0] === "CNY" ? "¥" : "$"}${primaryCost[1].toFixed(2)}`
    : null;

  // 时长格式化
  const dur = ep.duration_seconds ?? 0;
  const durLabel = dur > 0 ? `${Math.floor(dur / 60)}:${String(dur % 60).padStart(2, "0")}` : null;

  return (
    <button
      type="button"
      onClick={onClick}
      className="relative grid w-full items-center gap-2.5 rounded-lg p-2 text-left transition-colors focus-ring"
      style={{
        gridTemplateColumns: "auto 1fr auto",
        marginBottom: 3,
        background: active
          ? "linear-gradient(180deg, oklch(0.26 0.018 290 / 0.55), oklch(0.22 0.015 280 / 0.4))"
          : "transparent",
        border: active ? "1px solid var(--color-accent-soft)" : "1px solid transparent",
        boxShadow: active
          ? "0 0 0 1px var(--color-accent-soft), 0 4px 12px -6px oklch(0 0 0 / 0.5), inset 0 1px 0 oklch(1 0 0 / 0.04)"
          : "none",
      }}
      onMouseEnter={(e) => {
        if (!active) e.currentTarget.style.background = "oklch(0.24 0.012 265 / 0.4)";
      }}
      onMouseLeave={(e) => {
        if (!active) e.currentTarget.style.background = "transparent";
      }}
    >
      <div
        className="num grid h-[34px] w-[34px] shrink-0 place-items-center rounded-md text-[11px] font-bold leading-none"
        style={{
          background: active
            ? "linear-gradient(135deg, var(--color-accent) 0%, oklch(0.45 0.12 285) 100%)"
            : "linear-gradient(180deg, oklch(0.28 0.013 265), oklch(0.24 0.012 265))",
          color: active ? "oklch(0.14 0 0)" : "var(--color-text-3)",
          boxShadow: active
            ? "inset 0 1px 0 oklch(1 0 0 / 0.25), 0 0 0 1px oklch(1 0 0 / 0.12), 0 2px 6px -2px var(--color-accent-glow)"
            : "inset 0 1px 0 oklch(1 0 0 / 0.04), inset 0 0 0 1px var(--color-hairline-soft)",
        }}
      >
        {`E${ep.episode}`}
      </div>

      <div className="min-w-0">
        <div
          className="truncate text-[13px]"
          style={{
            color: active ? "var(--color-text)" : "var(--color-text-2)",
            fontWeight: active ? 600 : 500,
          }}
        >
          {ep.title}
        </div>
        <div className="mt-[3px] flex items-center gap-1.5">
          <span
            className="inline-flex items-center gap-1 text-[10.5px]"
            style={{ color: "var(--color-text-4)" }}
          >
            <span
              className={`h-[5px] w-[5px] rounded-full ${
                isActive ? "animate-shot-pulse" : ""
              }`}
              style={{ background: statusColor }}
            />
            {statusLabel}
          </span>
          {totalShots > 0 && (
            <>
              <span
                aria-hidden="true"
                className="h-px w-px rounded"
                style={{ background: "var(--color-hairline)", width: 2, height: 2 }}
              />
              <span className="num text-[10.5px]" style={{ color: "var(--color-text-4)" }}>
                {totalShots}
                {durLabel ? ` · ${durLabel}` : ""}
              </span>
            </>
          )}
        </div>
        {showProgress && (
          <div
            className="mt-[5px] h-[2px] overflow-hidden rounded-[1px]"
            style={{ background: "oklch(0.22 0.010 265)" }}
          >
            <div
              className="h-full"
              style={{
                width: `${progress}%`,
                background: "linear-gradient(90deg, var(--color-accent), var(--color-accent-2))",
                boxShadow: "0 0 6px var(--color-accent-glow)",
              }}
            />
          </div>
        )}
      </div>

      {costText && (
        <span
          className="num self-start pt-0.5 text-[10.5px]"
          style={{ color: active ? "var(--color-accent-2)" : "var(--color-text-4)" }}
        >
          {costText}
        </span>
      )}
    </button>
  );
}
