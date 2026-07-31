import { Mic, Quote, VolumeX } from "lucide-react";
import { useTranslation } from "react-i18next";
import { formatDurationsLabel } from "@/utils/duration_format";
import type { VoiceConsistencyTier } from "@/hooks/useModelCapabilities";

// ---------------------------------------------------------------------------
// 声音一致性档位元数据（Spec #1486 第 37 条「规格条」）。
//
// 档位含义只在此处声明一次：颜色/图标/文案随档位联动，避免各调用点各拼一份判断。
// ---------------------------------------------------------------------------

const TIER_ICON: Record<VoiceConsistencyTier, typeof Mic> = {
  native: Mic,
  soft: Quote,
  none: VolumeX,
};

const TIER_COLOR: Record<VoiceConsistencyTier, { fg: string; bg: string; border: string }> = {
  native: {
    fg: "var(--color-accent-2)",
    bg: "var(--color-accent-dim)",
    border: "var(--color-accent-soft)",
  },
  soft: {
    fg: "var(--color-warn)",
    bg: "oklch(0.80 0.12 70 / 0.12)",
    border: "oklch(0.80 0.12 70 / 0.35)",
  },
  none: {
    fg: "var(--color-text-4)",
    bg: "oklch(1 0 0 / 0.04)",
    border: "var(--color-hairline)",
  },
};

/** 声音一致性档位徽章：图标 + 文案 + 悬停说明。 */
export function VoiceConsistencyBadge({ tier }: { tier: VoiceConsistencyTier }) {
  const { t } = useTranslation("dashboard");
  const Icon = TIER_ICON[tier];
  const color = TIER_COLOR[tier];
  return (
    <span
      title={t(`voice_consistency_${tier}_desc`)}
      className="inline-flex cursor-help items-center gap-1 rounded px-1.5 py-0.5 text-[10.5px] font-medium"
      style={{ color: color.fg, background: color.bg, border: `1px solid ${color.border}` }}
    >
      <Icon className="h-3 w-3" aria-hidden />
      {t(`voice_consistency_${tier}_label`)}
    </span>
  );
}

export interface VideoModelSpecBarProps {
  /** 未收窄的模型原生时长全集；未知为 null。 */
  durations: number[] | null;
  resolutions: string[];
  /** 声音一致性档位；未知（能力未查到）为 null。音轨格同源于此——tier !== "none" 即有声。 */
  tier: VoiceConsistencyTier | null;
}

/** 选择器下方的规格条：时长 / 分辨率 / 音轨 / 声音一致性，四格集中展示当前选中模型的能力。 */
export function VideoModelSpecBar({ durations, resolutions, tier }: VideoModelSpecBarProps) {
  const { t } = useTranslation(["templates", "dashboard"]);

  const cells: { label: string; content: React.ReactNode }[] = [
    {
      label: t("duration_label"),
      content:
        durations && durations.length > 0 ? (
          <span className="font-mono text-[11.5px] tabular-nums text-text-2">
            {formatDurationsLabel(durations)}
          </span>
        ) : (
          <span className="text-[11.5px] text-text-4">—</span>
        ),
    },
    {
      label: t("resolution_label"),
      content:
        resolutions.length > 0 ? (
          <span className="font-mono text-[11.5px] text-text-2">{resolutions.join(" / ")}</span>
        ) : (
          <span className="text-[11.5px] text-text-4">—</span>
        ),
    },
    {
      label: t("dashboard:video_spec_audio_label"),
      content:
        tier !== null ? (
          <span className="text-[11.5px] text-text-2">
            {t(tier === "none" ? "dashboard:video_spec_audio_none" : "dashboard:video_spec_audio_has")}
          </span>
        ) : (
          <span className="text-[11.5px] text-text-4">—</span>
        ),
    },
    {
      label: t("dashboard:voice_consistency_label"),
      content: tier !== null ? <VoiceConsistencyBadge tier={tier} /> : <span className="text-[11.5px] text-text-4">—</span>,
    },
  ];

  return (
    <div
      className="mt-3 grid grid-cols-2 gap-y-3 rounded-[8px] border border-hairline-soft px-3 py-2.5 sm:grid-cols-4 sm:gap-y-0"
      style={{ background: "oklch(0.18 0.010 265 / 0.35)" }}
    >
      {cells.map((c, i) => (
        <div
          key={c.label}
          className={`flex flex-col gap-1 pl-3 first:pl-0 ${
            i > 0 ? "sm:border-l sm:border-[var(--color-hairline-soft)]" : ""
          }`}
        >
          <span className="font-mono text-[9.5px] font-bold uppercase tracking-[0.12em] text-text-4">
            {c.label}
          </span>
          {c.content}
        </div>
      ))}
    </div>
  );
}
