import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface SegmentBreakToggleProps {
  checked: boolean;
  /** Write the new value; the toggle becomes operable again only after resolution. */
  onChange: (next: boolean) => void | Promise<void>;
  disabled?: boolean;
}

/**
 * Section-break toggle without confirmation. Explain its effect on storyboard-image reference chains and grid grouping on hover or focus.
 * Storyboard details write to unsaved shot edits; script-planning pages write to the planning draft.
 */
export function SegmentBreakToggle({ checked, onChange, disabled = false }: SegmentBreakToggleProps) {
  const { t } = useTranslation("dashboard");
  const labelId = useId();
  const hintId = useId();
  const [saving, setSaving] = useState(false);

  const toggle = async (next: boolean) => {
    if (saving) return;
    setSaving(true);
    try {
      await onChange(next);
    } finally {
      setSaving(false);
    }
  };

  return (
    <span className="inline-flex items-center gap-1.5">
      <span id={labelId} className="text-xs text-muted-foreground">
        {t("segment_break_toggle")}
      </span>
      <span id={hintId} hidden>
        {t("segment_break_hint")}
      </span>
      <Tooltip>
        <TooltipTrigger
          render={
            <Switch
              size="sm"
              checked={checked}
              onCheckedChange={(next) => void toggle(next)}
              aria-labelledby={labelId}
              aria-describedby={hintId}
              disabled={disabled || saving}
            />
          }
        />
        <TooltipContent>{t("segment_break_hint")}</TooltipContent>
      </Tooltip>
    </span>
  );
}
