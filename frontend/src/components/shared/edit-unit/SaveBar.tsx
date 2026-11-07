import { Loader2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { Button } from "@/components/ui/button";

import { SaveStatus, type EditUnitControls } from "./SaveStatus";

/**
 * Persistent settings-form save bar: disable the button with no changes; otherwise allow discard or save. Successful saves show no toast;
 * failures show an error within the bar and retain edits. The bar supplies content only; its containing row supplies background, outline and padding:
 * width-limited and full-width forms use the shell footer (`PageShellFooter`); full-bleed forms use the detail-pane bottom.
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
