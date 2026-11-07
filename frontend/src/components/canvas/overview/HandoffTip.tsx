import { Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Alert, AlertAction, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useHandoffTipStore } from "./useHandoffTip";

/**
 * One-time hint below story settings: settings are extracted; next send the start-production request to the Agent.
 * Dismiss permanently after acknowledgement or sending an Agent message in this project; omit for projects that already sent a message before the hint appeared.
 */
export function HandoffTip({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const visible = useHandoffTipStore((s) => s.pending.has(projectName));
  const dismiss = useHandoffTipStore((s) => s.dismiss);

  if (!visible) return null;

  return (
    <Alert role="status" className="max-w-[40em]">
      <Sparkles aria-hidden />
      <AlertTitle>{t("overview_handoff_tip")}</AlertTitle>
      <AlertAction>
        <Button variant="outline" size="xs" onClick={() => dismiss(projectName)}>
          {t("overview_handoff_dismiss")}
        </Button>
      </AlertAction>
    </Alert>
  );
}
