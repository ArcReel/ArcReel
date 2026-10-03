import { AlertCircle, Check, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

import type { EditUnit } from "./useEditUnit";

/** 保存栏与内联提示条只用到编辑单元里与内容类型无关的部分。 */
export type EditUnitControls = Pick<
  EditUnit<unknown>,
  "dirty" | "status" | "error" | "externallyUpdated" | "save" | "discard"
>;

/** 保存栏与内联提示条左侧的状态：有未保存的修改、正在保存、已保存、保存失败、外部更新。 */
export function SaveStatus({ unit }: { unit: EditUnitControls }) {
  const { t } = useTranslation("common");

  if (unit.status === "error") {
    return (
      <p role="alert" className="flex min-w-0 items-center gap-2 text-sm text-destructive">
        <AlertCircle aria-hidden className="size-4 shrink-0" />
        <span className="min-w-0 wrap-break-word">{t("save_failed_detail", { message: unit.error })}</span>
      </p>
    );
  }

  return (
    <div className="flex min-w-0 items-center gap-2 text-sm">
      <p role="status" className="flex min-w-0 items-center gap-2 text-muted-foreground">
        {unit.status === "saving" ? (
          <>
            <Loader2 aria-hidden className="size-4 shrink-0 animate-spin" />
            {t("save_status_saving")}
          </>
        ) : unit.dirty ? (
          <>
            <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-warn" />
            {unit.externallyUpdated ? t("updated_by_agent") : t("unsaved_changes")}
          </>
        ) : unit.status === "saved" ? (
          <>
            <Check aria-hidden className="size-4 shrink-0 text-good" />
            {t("save_status_saved")}
          </>
        ) : null}
      </p>
      {unit.externallyUpdated && unit.status !== "saving" ? (
        // 采用新内容即放弃本地修改，回到外部更新后的已保存内容
        <Button variant="link" size="sm" onClick={unit.discard}>
          {t("adopt_new_content")}
        </Button>
      ) : null}
    </div>
  );
}
