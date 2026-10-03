import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { Button } from "@/components/ui/button";

import { SaveStatus, type EditUnitControls } from "./SaveStatus";

/**
 * 设置表单的常驻保存栏：没有修改时按钮置灰，有修改时可以放弃或保存；保存成功不弹提示，
 * 失败在栏内显示错误并保留修改。放在表单滚动区之外的底部，由调用方决定位置（`className`）。
 */
export function SaveBar({ unit, className }: { unit: EditUnitControls; className?: string }) {
  const { t } = useTranslation("common");
  const saving = unit.status === "saving";

  return (
    <div className={cn("flex min-h-14 items-center gap-4 border-t border-border bg-card px-6 py-3", className)}>
      <div className="min-w-0 flex-1">
        <SaveStatus unit={unit} />
      </div>
      <div className="flex shrink-0 items-center gap-2">
        <Button variant="outline" disabled={!unit.dirty || saving} onClick={unit.discard}>
          {t("discard_changes")}
        </Button>
        <Button disabled={!unit.dirty || saving} onClick={() => void unit.save()}>
          {saving ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
          {t("save")}
        </Button>
      </div>
    </div>
  );
}
