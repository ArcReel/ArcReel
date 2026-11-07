import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { Button } from "@/components/ui/button";

import { SaveStatus, type EditUnitControls } from "./SaveStatus";

/**
 * Inline unsaved notice for canvas content such as asset descriptions, storyboard prompts and story settings, placed below its content.
 * Appears only with changes, while saving, just saved or on save failure; offers Adopt new content for external updates.
 * `busy`: another in-flight operation occupies this unit, such as prompt-shape switching; disable save and discard as while saving.
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
