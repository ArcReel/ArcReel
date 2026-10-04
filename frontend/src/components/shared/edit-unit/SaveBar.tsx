import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { Button } from "@/components/ui/button";

import { SaveStatus, type EditUnitControls } from "./SaveStatus";

/**
 * 设置表单的常驻保存栏：没有修改时按钮置灰，有修改时可以放弃或保存；保存成功不弹提示，
 * 失败在栏内显示错误并保留修改。保存栏只有内容，底色、描边与内边距由所在的行提供：
 * 限宽与铺满档放进外壳底行（`PageShellFooter`），全出血档放在详情栏的底部。
 */
export function SaveBar({ unit, className }: { unit: EditUnitControls; className?: string }) {
  const { t } = useTranslation("common");
  const saving = unit.status === "saving";

  return (
    <div className={cn("flex items-center gap-4", className)}>
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
