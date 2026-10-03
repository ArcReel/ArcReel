import { useId, type ReactNode } from "react";
import { Play } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { LANGUAGE_DISPLAY_LABELS, SUPPORTED_LANGUAGES, type SupportedLanguage } from "@/i18n";
import { useOnboardingStore } from "@/stores/onboarding-store";

const LANGUAGE_ITEMS = SUPPORTED_LANGUAGES.map((lang) => ({ value: lang, label: LANGUAGE_DISPLAY_LABELS[lang] }));

function isSupportedLanguage(value: unknown): value is SupportedLanguage {
  return SUPPORTED_LANGUAGES.includes(value as SupportedLanguage);
}

/**
 * 全局设置「通用」：界面语言与重看新手引导。
 * 两项都立即生效：界面语言是本机偏好，不经后端保存，选中后整页文案随即切换，因此这个视图没有保存栏。
 */
export function GeneralSection() {
  const { t, i18n } = useTranslation(["dashboard", "onboarding"]);
  const startTour = useOnboardingStore((s) => s.start);
  const languageLabelId = useId();
  const currentLanguage = i18n.language.split("-")[0];

  return (
    <div className="flex flex-col gap-6">
      <h2 className="text-lg font-medium">{t("dashboard:settings_general")}</h2>
      <div className="flex flex-col divide-y divide-border rounded-lg border border-border bg-card">
        <SettingRow
          label={
            <span id={languageLabelId} className="text-sm font-medium">
              {t("dashboard:settings_interface_language")}
            </span>
          }
        >
          <Select
            items={LANGUAGE_ITEMS}
            value={isSupportedLanguage(currentLanguage) ? currentLanguage : null}
            onValueChange={(value) => {
              if (isSupportedLanguage(value)) void i18n.changeLanguage(value);
            }}
          >
            <SelectTrigger aria-labelledby={languageLabelId} className="w-44">
              <SelectValue />
            </SelectTrigger>
            <SelectContent>
              {LANGUAGE_ITEMS.map((item) => (
                <SelectItem key={item.value} value={item.value}>
                  {item.label}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </SettingRow>
        <SettingRow
          label={<span className="text-sm font-medium">{t("onboarding:replay_title")}</span>}
          description={t("onboarding:replay_desc")}
        >
          <Button variant="outline" onClick={startTour}>
            <Play aria-hidden data-icon="inline-start" />
            {t("onboarding:replay_action")}
          </Button>
        </SettingRow>
      </div>
    </div>
  );
}

function SettingRow({ label, description, children }: { label: ReactNode; description?: string; children: ReactNode }) {
  return (
    <div className="flex items-center justify-between gap-6 px-4 py-3">
      <div className="flex min-w-0 flex-col gap-0.5">
        {label}
        {description && <p className="text-sm text-muted-foreground">{description}</p>}
      </div>
      <div className="shrink-0">{children}</div>
    </div>
  );
}
