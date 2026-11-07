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
  /** The requested project ID when an import package conflicts with an existing ID; otherwise null closes the dialog. */
  projectName: string | null;
  importing: boolean;
  onResolve: (policy: Exclude<ImportConflictPolicy, "prompt">) => void;
  onCancel: () => void;
}

/** The import package's project ID conflicts: overwriting is irreversible and requires AlertDialog confirmation; importing under a new ID is also available. */
export function ImportConflictDialog({ projectName, importing, onResolve, onCancel }: ImportConflictDialogProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  // Keep the last ID during closing animation so the title does not flash empty.
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
        // Ignore Escape during import so the dialog cannot disappear before the request completes
        if (!open && !importing) onCancel();
      }}
    >
      <AlertDialogContent size="lg">
        <AlertDialogHeader>
          <AlertDialogTitle>{t("dashboard:duplicate_project_id")}</AlertDialogTitle>
        </AlertDialogHeader>
        <AlertDialogBody tabIndex={0} role="region" aria-label={t("dashboard:duplicate_project_id")}>
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
