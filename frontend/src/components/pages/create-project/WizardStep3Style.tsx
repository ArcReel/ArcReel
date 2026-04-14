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

export function WizardStep3Style({
  value,
  onChange,
  onBack,
  onCreate,
  onCancel,
  creating,
}: WizardStep3StyleProps) {
  const { t } = useTranslation(["common", "dashboard", "templates"]);

  const isCreateDisabled =
    creating || (value.mode === "custom" && value.uploadedFile === null);

  return (
    <div className="space-y-4">
      <StylePicker value={value} onChange={onChange} />

      <div className="flex items-center justify-between mt-6 pt-4 border-t border-gray-800">
        <button
          type="button"
          onClick={onCancel}
          className="text-sm text-gray-400 hover:text-gray-200 transition-colors"
        >
          {t("common:cancel")}
        </button>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={onBack}
            className="rounded-lg border border-gray-700 px-4 py-2 text-sm text-gray-300 hover:bg-gray-800 transition-colors"
          >
            {t("templates:prev_step")}
          </button>
          <button
            type="button"
            onClick={onCreate}
            disabled={isCreateDisabled}
            className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {creating ? (
              <>
                <Loader2 className="h-4 w-4 animate-spin" />
                {t("dashboard:creating")}
              </>
            ) : (
              t("dashboard:create_project")
            )}
          </button>
        </div>
      </div>
    </div>
  );
}
