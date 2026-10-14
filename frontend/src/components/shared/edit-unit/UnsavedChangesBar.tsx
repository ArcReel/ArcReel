import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { Button } from "@/components/ui/button";

import { SaveStatus, type EditUnitControls } from "./SaveStatus";

/**
 * 画布内容（资产描述、分镜提示词、故事设定等）的内联未保存提示条，放在所属内容下方。
 * 只在有修改、保存中、刚保存或保存失败时出现；外部更新时提供「采用新内容」。
 * `busy`：保存之外的在途操作正占用本单元（如提示词形态切换），与保存中一样禁用保存和放弃。
 */
export function UnsavedChangesBar({
  unit,
  busy = false,
  className,
}: {
  unit: EditUnitControls;
  busy?: boolean;
  className?: string;
}) {
  const { t } = useTranslation("common");
  const saving = unit.status === "saving";
  if (!unit.dirty && unit.status === "idle") return null;

  return (
    <div className={cn("flex items-center gap-3 rounded-lg border border-border bg-muted/40 px-3 py-2", className)}>
      <div className="min-w-0 flex-1">
        <SaveStatus unit={unit} />
        {unit.externalChangeMessage ? (
          <p role="status" className="text-sm text-subtle-foreground">{unit.externalChangeMessage}</p>
        ) : null}
      </div>
      {unit.dirty ? (
        <div className="flex shrink-0 items-center gap-1.5">
          <Button variant="ghost" size="sm" disabled={saving || busy} onClick={unit.discard}>
            {t("discard_changes")}
          </Button>
          <Button size="sm" disabled={saving || busy} onClick={() => void unit.save()}>
            {saving ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
            {t("save")}
          </Button>
        </div>
      ) : null}
    </div>
  );
}
