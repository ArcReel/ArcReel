import { AlertCircle, Check, Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";

import { Button } from "@/components/ui/button";

import type { EditUnit } from "./useEditUnit";

/** Save bars and inline notices use only the content-type-independent part of the editing unit. */
export type EditUnitControls = Pick<
  EditUnit<unknown>,
  "dirty" | "status" | "error" | "externallyUpdated" | "externalChangeMessage" | "save" | "discard"
>;

/** Left-side status in save bars and inline notices: unsaved changes, saving, saved, save failed or external update. */
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
        // Adopting new content discards local edits and returns to the externally updated saved content
        <Button variant="link" size="sm" onClick={unit.discard}>
          {t("adopt_new_content")}
        </Button>
      ) : null}
    </div>
  );
}
