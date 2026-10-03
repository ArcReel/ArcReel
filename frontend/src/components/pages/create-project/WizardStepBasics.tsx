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
  /** 创作类型。null = 未选：必选，没有默认值。 */
  contentMode: ContentMode | null;
  aspectRatio: "9:16" | "16:9";
  /** 生成模式，创建时锁定。null = 未选：必选，没有默认值。 */
  generationRoute: GenerationRoute | null;
  /** 多宫格分镜装配开关；只对分镜图生视频有意义，广告项目不支持。 */
  gridStoryboard: boolean;
}

/** 选择卡的呈现顺序：剧情演绎在前。 */
const CONTENT_MODES: readonly { mode: ContentMode; labelKey: string; descKey: string }[] = [
  { mode: "drama", labelKey: "drama_animation", descKey: "content_mode_drama_desc" },
  { mode: "narration", labelKey: "narration_visuals", descKey: "content_mode_narration_desc" },
  { mode: "ad", labelKey: "ad_short_video", descKey: "content_mode_ad_desc" },
];

const ASPECT_RATIOS = [
  { ratio: "9:16", labelKey: "portrait_9_16", glyph: "h-3.5 w-2" },
  { ratio: "16:9", labelKey: "landscape_16_9", glyph: "h-2 w-3.5" },
] as const;

/** 整块可点的单选卡：选中项用品牌色描边与浅底。 */
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
                  // 广告项目不支持多宫格分镜：切到广告时清掉已打开的开关
                  onChange({ ...value, contentMode: mode, gridStoryboard: mode === "ad" ? false : value.gridStoryboard })
                }
                className="sr-only"
              />
              <span className="text-sm font-medium text-foreground">{t(labelKey)}</span>
              {/* 选中项浅底上用中间档文字，保证对比度 */}
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
            // 宫格是分镜图生视频内的装配选项：切到参考生视频即清空
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
