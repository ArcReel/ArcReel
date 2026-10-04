import { useTranslation } from "react-i18next";
import { useLocation } from "wouter";

import { GHOST_BTN_CLS } from "@/components/shared/darkroom-tokens";
import { customModelSettingsPath, type OutputTruncation } from "@/utils/output-truncation";

/**
 * 文本任务输出被截断时的出路：自定义供应商的模型给「去登记最大输出长度」按钮，跳到设置里这个模型的条目；
 * 内置模型提示换一个文本模型。各文本任务的失败反馈共用这一处。
 */
export function OutputTruncationHint({ truncation }: { truncation: OutputTruncation }) {
  const { t } = useTranslation("dashboard");
  const [, setLocation] = useLocation();
  if (!truncation.custom) return <p className="m-0 text-muted-foreground">{t("text_output_truncated_switch_model")}</p>;
  const settingsPath = customModelSettingsPath(truncation.providerId, truncation.model);
  if (settingsPath === null) return null;
  return (
    <button type="button" className={GHOST_BTN_CLS} onClick={() => setLocation(`~${settingsPath}`)}>
      {t("text_output_truncated_register_limit")}
    </button>
  );
}
