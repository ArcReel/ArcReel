import { useId } from "react";
import { LayoutGrid } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Switch } from "@/components/ui/switch";

export interface GridStoryboardBarProps {
  checked: boolean;
  onToggle: (next: boolean) => void;
}

/**
 * Grid-storyboard assembly bar.
 *
 * Structurally separate from generation-mode cards: grids change only storyboard-image production, leaving the video-model input contract unchanged.
 * They are therefore an option within storyboard-image-to-video, rather than a third mode. Wizard and settings share copy and toggle semantics.
 */
export function GridStoryboardBar({ checked, onToggle }: GridStoryboardBarProps) {
  const { t } = useTranslation("dashboard");
  const reactId = useId();
  const labelId = `${reactId}-grid-label`;
  const descId = `${reactId}-grid-desc`;

  return (
    <div className="flex items-start gap-2.5 rounded-lg border border-border px-3.5 py-2.5">
      <LayoutGrid aria-hidden className={cn("mt-0.5 size-4 shrink-0", checked ? "text-primary" : "text-muted-foreground")} />
      <div className="min-w-0 flex-1">
        <div id={labelId} className="text-sm font-medium text-foreground">
          {t("grid_storyboard_label")}
        </div>
        <div id={descId} className="mt-0.5 text-xs text-muted-foreground">
          {t("grid_storyboard_desc")}
        </div>
      </div>
      <Switch
        checked={checked}
        onCheckedChange={onToggle}
        aria-labelledby={labelId}
        aria-describedby={descId}
        className="mt-0.5"
      />
    </div>
  );
}
