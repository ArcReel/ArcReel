import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { StylePicker, type StylePickerValue } from "@/components/shared/StylePicker";
import type { CustomStyle } from "@/api";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";

export interface WizardStep3Value extends StylePickerValue {
  styleDescription: string;
}

export interface WizardStep3StyleProps {
  value: WizardStep3Value;
  onChange: (next: WizardStep3Value) => void;
  onBack: () => void;
  onCreate: () => void;
  onCancel: () => void;
  creating: boolean;
  analyzing: boolean;
  onAnalyze: () => void;
  customStyles: CustomStyle[];
  customStylesLoading: boolean;
}

export function WizardStep3Style({
  value,
  onChange,
  onBack,
  onCreate,
  onCancel,
  creating,
  analyzing,
  onAnalyze,
  customStyles,
  customStylesLoading,
}: WizardStep3StyleProps) {
  const { t } = useTranslation(["common", "dashboard", "templates"]);

  // 风格为可选项：不选模版且未上传自定义图也可创建（项目建好后为"无风格"态，
  // 生成链路不附加风格 prompt）。
  const isCreateDisabled = creating || analyzing;

  return (
    <div className="space-y-5">
      <StylePicker
        value={value}
        onChange={(next) => onChange({ ...next, styleDescription: value.styleDescription })}
        customStyles={customStyles}
        customStylesLoading={customStylesLoading}
        onSelectCustomStyle={(style, next) => onChange({
          ...next,
          styleDescription: style.description,
        })}
        onCreateCustomStyle={(next) => onChange({ ...next, styleDescription: "" })}
        customPreviewAction={value.uploadedPreview ? (
          <button
            type="button"
            onClick={onAnalyze}
            disabled={analyzing || creating}
            className="inline-flex items-center gap-1.5 rounded-[6px] border border-white/15 bg-black/65 px-2.5 py-1.5 text-[11px] font-medium text-white shadow-lg backdrop-blur-md transition-colors hover:bg-black/80 disabled:cursor-not-allowed disabled:opacity-60"
          >
            {analyzing ? <Loader2 aria-hidden className="h-3 w-3 motion-safe:animate-spin" /> : null}
            {analyzing ? t("dashboard:style_analyzing") : t("dashboard:style_analyze")}
          </button>
        ) : undefined}
      />

      {value.mode === "custom" && value.customStyleId === null && (
        <div className="mt-4">
          <div className="mb-4 flex items-center gap-3" aria-hidden>
            <span className="h-px flex-1 bg-hairline-soft" />
            <span className="font-mono text-[10px] font-semibold uppercase tracking-[0.16em] text-text-4">
              {t("dashboard:style_input_or")}
            </span>
            <span className="h-px flex-1 bg-hairline-soft" />
          </div>
          <label
            htmlFor="new-project-style-description"
            className="mb-1.5 block text-[12px] font-medium text-text-2"
          >
            {t("dashboard:style_description_label")}
          </label>
          <textarea
            id="new-project-style-description"
            value={value.styleDescription}
            onChange={(event) => onChange({ ...value, styleDescription: event.target.value })}
            placeholder={t("dashboard:style_description_placeholder")}
            rows={5}
            className="w-full resize-y rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] leading-[1.6] text-text outline-none transition-colors placeholder:text-text-4 focus:border-accent/55 focus:ring-2 focus:ring-accent/15"
          />
          <p className="mt-1.5 text-[11px] leading-[1.5] text-text-3">
            {t("dashboard:style_description_hint")}
          </p>
        </div>
      )}

      <div className="mt-7 flex items-center justify-between border-t border-hairline-soft pt-5">
        <button
          type="button"
          onClick={onCancel}
          className="rounded-[7px] px-2.5 py-1.5 text-[12.5px] text-text-3 transition-colors hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        >
          {t("common:cancel")}
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onBack}
            className={GHOST_BTN_LG_CLS}
          >
            <span aria-hidden>←</span>
            {t("templates:prev_step")}
          </button>
          <button
            type="button"
            onClick={onCreate}
            disabled={isCreateDisabled}
            className={ACCENT_BTN_CLS}
            style={ACCENT_BUTTON_STYLE}
          >
            {creating ? (
              <>
                <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin" aria-hidden />
                {t("dashboard:creating")}
              </>
            ) : (
              <>
                ●&nbsp;{t("dashboard:create_project")}
              </>
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
