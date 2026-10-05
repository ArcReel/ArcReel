import { useTranslation } from "react-i18next";
import { Badge } from "@/components/ui/badge";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

/**
 * 演示工作台顶栏项目标题旁的「演示 · 只读」徽标，悬停或聚焦时说明写操作都不可用。
 * 说明同时以 sr-only 文字写进徽标，读屏不依赖提示弹层。不可用的按钮仍各自就地说明「演示中不可用」。
 */
export function DemoReadOnlyBadge() {
  const { t } = useTranslation("onboarding");
  const hint = t("demo_badge_hint");

  return (
    <Tooltip>
      <TooltipTrigger
        render={
          <Badge
            variant="outline"
            tabIndex={0}
          />
        }
      >
        {t("demo_badge")}
        <span className="sr-only">{hint}</span>
      </TooltipTrigger>
      <TooltipContent side="bottom">
        {hint}
      </TooltipContent>
    </Tooltip>
  );
}
