import { useId, useState } from "react";
import { AlertTriangle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { GenerationRouteCards } from "@/components/shared/GenerationRouteCards";
import { GridStoryboardBar } from "@/components/shared/GridStoryboardBar";
import {
  EpisodeTargetDurationField,
  isValidEpisodeTargetDuration,
} from "@/components/shared/EpisodeTargetDurationField";
import { SpeechRateField, isValidSpeechRate } from "@/components/shared/SpeechRateField";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, radioCardClass } from "@/components/shared/darkroom-tokens";
import { FieldLabel } from "@/components/shared/FieldLabel";
import type { GenerationRoute } from "@/utils/generation-mode";

export interface WizardStep1Value {
  title: string;
  contentMode: "narration" | "drama" | "ad";
  aspectRatio: "9:16" | "16:9";
  /** 生成模式，创建时锁定。null = 未选：必选，未选不放行。 */
  generationRoute: GenerationRoute | null;
  /** 多宫格分镜装配开关，随创建写入；仅分镜图生视频有意义，ad 不支持。 */
  gridStoryboard: boolean;
  /** 仅 ad：目标总时长（秒）。UI 四档 15/30/60/90，默认 60。 */
  targetDuration: number;
  /** 口播语速估算（阅读单位 / 秒）；null = 未填，按项目语言的默认速度估算。 */
  speechRate: number | null;
  /** 单集目标时长（秒）；null = 未设目标。ad 不持有（整集体量按 targetDuration 预算）。 */
  episodeTargetDuration: number | null;
}

/** 广告/短片目标总时长的 UI 档位（数据层不硬枚举，任意正整数秒合法）。 */
const AD_TARGET_DURATION_TIERS = [15, 30, 60, 90] as const;

export interface WizardStep1BasicsProps {
  value: WizardStep1Value;
  onChange: (next: WizardStep1Value) => void;
  onNext: () => void;
  onCancel: () => void;
}

export function WizardStep1Basics({
  value,
  onChange,
  onNext,
  onCancel,
}: WizardStep1BasicsProps) {
  const { t } = useTranslation(["common", "dashboard", "templates"]);
  const [titleError, setTitleError] = useState("");
  const reactId = useId();
  const titleId = `${reactId}-title`;
  const titleErrorId = `${reactId}-title-error`;

  const handleTitleChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    setTitleError("");
    onChange({ ...value, title: e.target.value });
  };

  const handleNext = () => {
    if (!value.title.trim()) {
      setTitleError(t("dashboard:project_title_required"));
      return;
    }
    // 生成模式必选：无预选、未选不放行
    if (!value.generationRoute) return;
    // 口播语速越界不放行（区间与后端同一把尺）；未填合法
    if (!isValidSpeechRate(value.speechRate)) return;
    if (!isValidEpisodeTargetDuration(value.episodeTargetDuration)) return;
    onNext();
  };

  return (
    <div className="space-y-5">
      {/* Title */}
      <div>
        <FieldLabel htmlFor={titleId} required>
          {t("dashboard:project_title")}
        </FieldLabel>
        <div className="relative">
          <input
            id={titleId}
            type="text"
            value={value.title}
            onChange={handleTitleChange}
            placeholder={t("dashboard:rebirth_empress_example")}
            aria-required="true"
            aria-invalid={titleError ? "true" : undefined}
            aria-describedby={titleError ? titleErrorId : undefined}
            className="w-full rounded-md border border-border bg-card/55 px-3 py-2.5 text-[14px] text-foreground placeholder:text-muted-foreground transition-colors focus:border-primary/55 focus:bg-card/85 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
          />
        </div>
        {titleError ? (
          <p
            id={titleErrorId}
            role="alert"
            aria-live="polite"
            className="mt-1.5 inline-flex items-center gap-1.5 font-mono text-[10.5px] uppercase tracking-[0.08em] text-warn"
          >
            <AlertTriangle aria-hidden className="h-3 w-3" />
            {titleError}
          </p>
        ) : null}
        <p className="mt-1.5 text-[11.5px] text-muted-foreground">{t("dashboard:project_id_auto_gen_hint")}</p>
      </div>

      {/* Content Mode */}
      <div>
        <FieldLabel>{t("dashboard:content_mode")}</FieldLabel>
        <div className="flex gap-2.5" role="radiogroup" aria-label={t("dashboard:content_mode")}>
          <label className={radioCardClass(value.contentMode === "narration")}>
            <input
              type="radio"
              name="contentMode"
              value="narration"
              checked={value.contentMode === "narration"}
              onChange={() => onChange({ ...value, contentMode: "narration" })}
              className="sr-only"
            />
            {t("dashboard:narration_visuals")}
          </label>
          <label className={radioCardClass(value.contentMode === "drama")}>
            <input
              type="radio"
              name="contentMode"
              value="drama"
              checked={value.contentMode === "drama"}
              onChange={() => onChange({ ...value, contentMode: "drama" })}
              className="sr-only"
            />
            {t("dashboard:drama_animation")}
          </label>
          <label className={radioCardClass(value.contentMode === "ad")}>
            <input
              type="radio"
              name="contentMode"
              value="ad"
              checked={value.contentMode === "ad"}
              onChange={() =>
                // ad 不支持多宫格分镜：切到 ad 时清掉已勾选的装配开关
                onChange({ ...value, contentMode: "ad", gridStoryboard: false })
              }
              className="sr-only"
            />
            {t("dashboard:ad_short_video")}
          </label>
        </div>
        <p className="mt-2 text-[11.5px] leading-[1.55] text-muted-foreground">
          {value.contentMode === "narration"
            ? t("dashboard:content_mode_narration_desc")
            : value.contentMode === "drama"
              ? t("dashboard:content_mode_drama_desc")
              : t("dashboard:content_mode_ad_desc")}
        </p>
      </div>

      {/* Target Duration（仅 ad）。原生 radio：方向键切换开箱即用 */}
      {value.contentMode === "ad" && (
        <div>
          <FieldLabel>{t("dashboard:target_duration_label")}</FieldLabel>
          <div
            className="flex flex-wrap gap-2"
            role="radiogroup"
            aria-label={t("dashboard:target_duration_label")}
          >
            {AD_TARGET_DURATION_TIERS.map((tier) => {
              const active = value.targetDuration === tier;
              return (
                <label
                  key={tier}
                  className={
                    "cursor-pointer rounded-md border px-3 py-1.5 font-mono text-[10.5px] font-bold uppercase tracking-[0.14em] transition-colors has-[:focus-visible]:ring-2 has-[:focus-visible]:ring-ring " +
                    (active
                      ? "border-primary/45 bg-primary/12 text-primary"
                      : "border-border/50 bg-card/55 text-muted-foreground hover:border-border hover:text-foreground")
                  }
                  style={active ? { boxShadow: "0 0 18px -8px color-mix(in oklab, var(--primary) 35%, transparent)" } : undefined}
                >
                  <input
                    type="radio"
                    name="targetDuration"
                    value={tier}
                    checked={active}
                    onChange={() => onChange({ ...value, targetDuration: tier })}
                    aria-label={t("dashboard:duration_seconds_value_text", { value: tier })}
                    className="sr-only"
                  />
                  {t("dashboard:duration_seconds_value_text", { value: tier })}
                </label>
              );
            })}
          </div>
        </div>
      )}

      {/* 单集目标时长（非 ad）：ad 的整集体量由上面的目标总时长表达，两者互斥 */}
      {value.contentMode !== "ad" && (
        <EpisodeTargetDurationField
          value={value.episodeTargetDuration}
          onChange={(next) => onChange({ ...value, episodeTargetDuration: next })}
        />
      )}

      {/* 口播语速估算：项目还没有语言事实（source_language 由内容分析写入），单位按未知语言呈现 */}
      <SpeechRateField
        value={value.speechRate}
        onChange={(next) => onChange({ ...value, speechRate: next })}
      />

      {/* Aspect Ratio */}
      <div>
        <FieldLabel>{t("dashboard:aspect_ratio")}</FieldLabel>
        <div className="flex gap-2.5" role="radiogroup" aria-label={t("dashboard:aspect_ratio")}>
          <label className={radioCardClass(value.aspectRatio === "9:16")}>
            <input
              type="radio"
              name="aspectRatio"
              value="9:16"
              checked={value.aspectRatio === "9:16"}
              onChange={() => onChange({ ...value, aspectRatio: "9:16" })}
              className="sr-only"
            />
            <span className="inline-flex items-center gap-2">
              <span
                aria-hidden
                className="block h-3 w-[7.5px] rounded-xs border border-border"
                style={{
                  background:
                    value.aspectRatio === "9:16" ? "color-mix(in oklab, var(--primary) 22%, transparent)" : "transparent",
                }}
              />
              {t("dashboard:portrait_9_16")}
            </span>
          </label>
          <label className={radioCardClass(value.aspectRatio === "16:9")}>
            <input
              type="radio"
              name="aspectRatio"
              value="16:9"
              checked={value.aspectRatio === "16:9"}
              onChange={() => onChange({ ...value, aspectRatio: "16:9" })}
              className="sr-only"
            />
            <span className="inline-flex items-center gap-2">
              <span
                aria-hidden
                className="block h-[7.5px] w-3 rounded-xs border border-border"
                style={{
                  background:
                    value.aspectRatio === "16:9" ? "color-mix(in oklab, var(--primary) 22%, transparent)" : "transparent",
                }}
              />
              {t("dashboard:landscape_16_9")}
            </span>
          </label>
        </div>
      </div>

      {/* Generation route — 二选一，创建后不可更改 */}
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
        {/* ad 不支持宫格，其装配条不呈现 */}
        {value.generationRoute === "storyboard" && value.contentMode !== "ad" ? (
          <GridStoryboardBar
            checked={value.gridStoryboard}
            onToggle={(next) => onChange({ ...value, gridStoryboard: next })}
            animated
          />
        ) : null}
      </GenerationRouteCards>

      {/* Footer */}
      <div className="mt-7 flex items-center justify-between border-t border-border/50 pt-5">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-md px-2.5 py-1.5 text-[12.5px] text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          {t("common:cancel")}
        </button>
        <button
          type="button"
          onClick={handleNext}
          disabled={!value.title.trim() || !value.generationRoute}
          className={ACCENT_BTN_CLS}
          style={ACCENT_BUTTON_STYLE}
        >
          {t("templates:next_step")}
          <span aria-hidden>→</span>
        </button>
      </div>
    </div>
  );
}
