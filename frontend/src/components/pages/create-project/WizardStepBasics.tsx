import { useId, type Ref } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Input } from "@/components/ui/input";
import { GenerationRouteCards } from "@/components/shared/GenerationRouteCards";
import { GridStoryboardBar } from "@/components/shared/GridStoryboardBar";
import type { GenerationRoute } from "@/utils/generation-mode";
import { WizardSection } from "./WizardSection";

export type ContentMode = "drama" | "narration" | "ad";

export interface WizardBasicsValue {
  title: string;
  /** Creation types; null = unselected, required without defaults. */
  contentMode: ContentMode | null;
  aspectRatio: "9:16" | "16:9";
  /** Generation modes, fixed on creation; null = unselected, required without defaults. */
  generationRoute: GenerationRoute | null;
  /** Multigrid storyboard assembly switch, meaningful only for storyboard image-to-video, unsupported for ads. */
  gridStoryboard: boolean;
}

/** Choice-card order: drama first. */
const CONTENT_MODES: readonly { mode: ContentMode; labelKey: string; descKey: string }[] = [
  { mode: "drama", labelKey: "drama_animation", descKey: "content_mode_drama_desc" },
  { mode: "narration", labelKey: "narration_visuals", descKey: "content_mode_narration_desc" },
  { mode: "ad", labelKey: "ad_short_video", descKey: "content_mode_ad_desc" },
];

const ASPECT_RATIOS = [
  { ratio: "9:16", labelKey: "portrait_9_16", glyph: "h-3.5 w-2" },
  { ratio: "16:9", labelKey: "landscape_16_9", glyph: "h-2 w-3.5" },
] as const;

/** Whole-card radio targets, selected with brand-color borders and pale backgrounds. */
function choiceClass(selected: boolean): string {
  return cn(
    "relative flex cursor-pointer rounded-lg border transition-colors has-focus-visible:ring-3 has-focus-visible:ring-ring/50",
    selected ? "border-primary/60 bg-primary/10" : "border-border hover:bg-accent",
  );
}

export interface WizardStepBasicsProps {
  value: WizardBasicsValue;
  onChange: (next: WizardBasicsValue) => void;
  titleRef?: Ref<HTMLInputElement>;
}

export function WizardStepBasics({ value, onChange, titleRef }: WizardStepBasicsProps) {
  const { t } = useTranslation("dashboard");
  const reactId = useId();
  const titleId = `${reactId}-title`;
  const titleHintId = `${reactId}-title-hint`;
  const contentModeId = `${reactId}-content-mode`;
  const aspectId = `${reactId}-aspect`;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1.5">
        <label htmlFor={titleId} className="text-sm font-medium text-foreground">
          {t("project_title")}
        </label>
        <Input
          ref={titleRef}
          id={titleId}
          value={value.title}
          onChange={(e) => onChange({ ...value, title: e.target.value })}
          placeholder={t("rebirth_empress_example")}
          aria-required="true"
          aria-describedby={titleHintId}
          autoComplete="off"
        />
        <p id={titleHintId} className="text-xs text-muted-foreground">
          {t("project_id_auto_gen_hint")}
        </p>
      </div>

      <WizardSection title={<span id={contentModeId}>{t("content_mode")}</span>} description={t("wizard_content_mode_hint")}>
        <div role="radiogroup" aria-labelledby={contentModeId} aria-required="true" className="grid grid-cols-3 gap-2.5">
          {CONTENT_MODES.map(({ mode, labelKey, descKey }) => (
            <label key={mode} className={cn(choiceClass(value.contentMode === mode), "flex-col gap-1 px-3 py-2.5")}>
              <input
                type="radio"
                name={contentModeId}
                value={mode}
                checked={value.contentMode === mode}
                onChange={() =>
                  // Ads do not support multigrid storyboards; switching to ads clears enabled switches.
                  onChange({ ...value, contentMode: mode, gridStoryboard: mode === "ad" ? false : value.gridStoryboard })
                }
                className="sr-only"
              />
              <span className="text-sm font-medium text-foreground">{t(labelKey)}</span>
              {/* Use medium-tier text against pale selected backgrounds for contrast. */}
              <span className={cn("text-xs", value.contentMode === mode ? "text-subtle-foreground" : "text-muted-foreground")}>
                {t(descKey)}
              </span>
            </label>
          ))}
        </div>
      </WizardSection>

      <WizardSection title={<span id={aspectId}>{t("aspect_ratio")}</span>}>
        <div role="radiogroup" aria-labelledby={aspectId} className="flex gap-2.5">
          {ASPECT_RATIOS.map(({ ratio, labelKey, glyph }) => (
            <label key={ratio} className={cn(choiceClass(value.aspectRatio === ratio), "items-center gap-2 px-3 py-2")}>
              <input
                type="radio"
                name={aspectId}
                value={ratio}
                checked={value.aspectRatio === ratio}
                onChange={() => onChange({ ...value, aspectRatio: ratio })}
                className="sr-only"
              />
              <span aria-hidden className={cn("rounded-xs border border-muted-foreground", glyph)} />
              <span className="text-sm text-foreground">{t(labelKey)}</span>
            </label>
          ))}
        </div>
      </WizardSection>

      <GenerationRouteCards
        value={value.generationRoute}
        onChange={(next) =>
          onChange({
            ...value,
            generationRoute: next,
            // Grids are storyboard image-to-video assembly options; switching to reference video clears them.
            gridStoryboard: next === "storyboard" ? value.gridStoryboard : false,
          })
        }
      >
        {value.generationRoute === "storyboard" && value.contentMode !== "ad" ? (
          <GridStoryboardBar
            checked={value.gridStoryboard}
            onToggle={(next) => onChange({ ...value, gridStoryboard: next })}
          />
        ) : null}
      </GenerationRouteCards>
    </div>
  );
}
