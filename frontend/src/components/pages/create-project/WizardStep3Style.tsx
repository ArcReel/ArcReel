import { useTranslation } from "react-i18next";
import { Loader2 } from "lucide-react";
import { StylePicker, type StylePickerValue } from "@/components/shared/StylePicker";
import { ACCENT_BTN_CLS, ACCENT_BUTTON_STYLE, GHOST_BTN_LG_CLS } from "@/components/ui/darkroom-tokens";

export type WizardStep3Value = StylePickerValue;

export interface WizardStep3StyleProps {
  value: WizardStep3Value;
  onChange: (next: WizardStep3Value) => void;
  onBack: () => void;
  onCreate: () => void;
  onCancel: () => void;
  creating: boolean;
}

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
