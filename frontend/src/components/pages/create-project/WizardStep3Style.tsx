import type { CSSProperties } from "react";
import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { StylePicker, type StylePickerValue } from "@/components/shared/StylePicker";

export type WizardStep3Value = StylePickerValue;

export interface WizardStep3StyleProps {
  value: WizardStep3Value;
  onChange: (next: WizardStep3Value) => void;
  onBack: () => void;
  onCreate: () => void;
  onCancel: () => void;
  creating: boolean;
}

const ACCENT_BUTTON_STYLE: CSSProperties = {
  color: "oklch(0.14 0 0)",
  background: "linear-gradient(180deg, var(--color-accent-2), var(--color-accent))",
  boxShadow:
    "inset 0 1px 0 oklch(1 0 0 / 0.3), 0 0 0 1px oklch(0.55 0.10 295 / 0.4), 0 6px 18px -8px var(--color-accent-glow)",
};

export function WizardStep3Style({
  value,
  onChange,
  onBack,
  onCreate,
  onCancel,
  creating,
}: WizardStep3StyleProps) {
  const { t } = useTranslation(["common", "dashboard", "templates"]);

  // 风格为可选项：不选模版且未上传自定义图也可创建（项目建好后为"无风格"态，
  // 生成链路不附加风格 prompt）。
  const isCreateDisabled = creating;

  return (
    <div className="space-y-5">
      <StylePicker value={value} onChange={onChange} />

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
            className="inline-flex items-center gap-1.5 rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3.5 py-2 text-[12.5px] text-text-2 transition-colors hover:border-hairline-strong hover:bg-bg-grad-a hover:text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
          >
            <span aria-hidden>←</span>
            {t("templates:prev_step")}
          </button>
          <button
            type="button"
            onClick={onCreate}
            disabled={isCreateDisabled}
            className="inline-flex items-center gap-2 rounded-[8px] px-4 py-2 text-[12.5px] font-semibold transition-transform motion-safe:hover:-translate-y-px focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-50"
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
