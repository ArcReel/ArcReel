import { AlertCircle, Check, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

import type { EditUnit } from "./useEditUnit";

export type EditUnitControls = Pick<
  EditUnit<unknown>,
  "dirty" | "status" | "error" | "externallyUpdated" | "externalChangeMessage" | "save" | "discard"
>;

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
        <Button variant="link" size="sm" onClick={unit.discard}>
          {t("adopt_new_content")}
        </Button>
      ) : null}
    </div>
  );
}
