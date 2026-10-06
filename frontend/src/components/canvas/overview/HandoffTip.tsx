import { Sparkles } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Alert, AlertAction, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { useHandoffTipStore } from "./useHandoffTip";

/**
 * 故事设定区底部的一次性提示：故事设定已提炼完成，接下来向 Agent 发送「开始制作」。
 * 点「知道了」或在这个项目里向 Agent 发出消息后消失，不再出现；提示出现之前已发过消息的项目不显示。
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
