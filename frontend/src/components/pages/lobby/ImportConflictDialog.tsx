import { useState } from "react";
import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import type { ImportConflictPolicy } from "@/types";

interface ImportConflictDialogProps {
  /** 导入包要用的项目编号已被占用时为该编号，否则为 null（对话框关闭）。 */
  projectName: string | null;
  importing: boolean;
  onResolve: (policy: Exclude<ImportConflictPolicy, "prompt">) => void;
  onCancel: () => void;
}

/** 导入包的项目编号与现有项目重复：覆盖现有项目不可撤销，用 AlertDialog 确认；也可以改用新编号导入。 */
export function ImportConflictDialog({ projectName, importing, onResolve, onCancel }: ImportConflictDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  // 关闭动画期间保留上一次的编号，避免标题闪成空白。
  const [shown, setShown] = useState(projectName);
  if (projectName !== null && projectName !== shown) setShown(projectName);
  const [policy, setPolicy] = useState<Exclude<ImportConflictPolicy, "prompt"> | null>(null);

  const resolve = (next: Exclude<ImportConflictPolicy, "prompt">) => {
    setPolicy(next);
    onResolve(next);
  };

  return (
    <AlertDialog
      open={projectName !== null}
      onOpenChange={(open) => {
        // 导入中不响应 Esc，避免请求还在途时对话框先消失
        if (!open && !importing) onCancel();
      }}
    >
      <AlertDialogContent size="lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{t("dashboard:duplicate_project_id")}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody>
          <div className="flex flex-col gap-3">
            <AlertDialogDescription>
              {t("dashboard:lobby_import_conflict_desc", { name: shown ?? "" })}
            </AlertDialogDescription>
            <dl className="flex flex-col gap-2 text-sm">
              <dt className="font-medium">{t("dashboard:auto_rename_import")}</dt>
              <dd className="text-muted-foreground">{t("dashboard:rename_hint")}</dd>
              <dt className="font-medium">{t("dashboard:overwrite_existing")}</dt>
              <dd className="text-muted-foreground">{t("dashboard:overwrite_hint")}</dd>
            </dl>
          </div>
        </AlertDialogBody>
        <AlertDialogFooter>
          <AlertDialogCancel disabled={importing}>{t("common:cancel")}</AlertDialogCancel>
          <Button variant="outline" disabled={importing} onClick={() => resolve("rename")}>
            {importing && policy === "rename" && <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />}
            {t("dashboard:auto_rename_import")}
          </Button>
          <AlertDialogAction variant="destructive" disabled={importing} onClick={() => resolve("overwrite")}>
            {importing && policy === "overwrite" && (
              <Loader2 data-icon="inline-start" className="animate-spin" aria-hidden />
            )}
            {t("dashboard:overwrite_existing")}
          </AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
