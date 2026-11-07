import { Mic, Quote, VolumeX } from "lucide-react";
import { cn } from "cn";
import { useTranslation } from "react-i18next";
import { formatDurationsLabel } from "@/utils/duration_format";
import type { VoiceConsistencyTier } from "@/types";

// ---------------------------------------------------------------------------
// Voice-consistency tier metadata.
//
// Declare tier meanings only here: colors/icons/copy follow tiers together, preventing duplicated caller decisions.
// ---------------------------------------------------------------------------

const TIER_ICON: Record<VoiceConsistencyTier, typeof Mic> = {
  native: Mic,
  soft: Quote,
  none: VolumeX,
};

const TIER_CLASS: Record<VoiceConsistencyTier, string> = {
  native: "border-primary/25 bg-primary/10 text-primary",
  soft: "border-warn/35 bg-warn/10 text-warn",
  none: "border-border bg-foreground/5 text-muted-foreground",
};

/** Voice-consistency badges: icons, labels and hover explanations. */
export function VoiceConsistencyBadge({ tier }: { tier: VoiceConsistencyTier }) {
  const { t } = useTranslation("dashboard");
  const Icon = TIER_ICON[tier];
  const label = t(`voice_consistency_${tier}_label`);
  const desc = t(`voice_consistency_${tier}_desc`);
  return (
    // Screen readers miss degradation criteria in titles only; add role="note" and aria-label for accessibility-tree exposure.
    <span
      role="note"
      title={desc}
      aria-label={`${label}: ${desc}`}
      className={cn("inline-flex w-fit cursor-help items-center gap-1 rounded-sm border px-1.5 py-0.5 text-xs font-medium", TIER_CLASS[tier])}
    >
      <Icon className="size-3" aria-hidden />
      {label}
    </span>
  );
}

export interface VideoModelSpecBarProps {
  /** Unrestricted complete native model-duration sets; null when unknown. */
  durations: number[] | null;
  resolutions: string[];
  /** Voice-consistency tiers; null when unknown (capabilities unavailable). Audio cells share this source: tier!=="none" means audio. */
  tier: VoiceConsistencyTier | null;
}

/** Specification bars beneath selects: duration/resolution/audio/voice consistency show selected model capabilities in four cells. */
export function VideoModelSpecBar({ durations, resolutions, tier }: VideoModelSpecBarProps) {
  const { t } = useTranslation("dashboard");

  const cells: { label: string; content: React.ReactNode }[] = [
    {
      // Bars show entire supported-duration sets, not templates:duration_label (Default duration user preferences).
      label: t("video_spec_duration_label"),
      content:
        durations && durations.length > 0 ? (
          <span className="text-xs tabular-nums text-subtle-foreground">
            {formatDurationsLabel(durations)}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
    {
      label: t("resolution_label"),
      content:
        resolutions.length > 0 ? (
          <span className="text-xs text-subtle-foreground">{resolutions.join(" / ")}</span>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
    {
      label: t("video_spec_audio_label"),
      content:
        tier !== null ? (
          <span className="text-xs text-subtle-foreground">
            {t(tier === "none" ? "video_spec_audio_none" : "video_spec_audio_has")}
          </span>
        ) : (
          <span className="text-xs text-muted-foreground">—</span>
        ),
    },
    {
      label: t("voice_consistency_label"),
      content: tier !== null ? <VoiceConsistencyBadge tier={tier} /> : <span className="text-xs text-muted-foreground">—</span>,
    },
  ];

  // Two/four columns depend on bar widths themselves; they appear in global/project settings and creation wizards.
  return (
    <div className="@container">
      <div className="grid grid-cols-2 gap-y-3 rounded-md border border-border bg-muted/40 px-3 py-2.5 @md:grid-cols-4 @md:gap-y-0">
        {cells.map((c, i) => (
          <div
            key={c.label}
            className={cn("flex flex-col gap-1 pl-3 first:pl-0", i > 0 && "@md:border-l @md:border-border")}
          >
            <span className="text-xs text-muted-foreground">{c.label}</span>
            {c.content}
          </div>
        ))}
      </div>
    </div>
  );
}
