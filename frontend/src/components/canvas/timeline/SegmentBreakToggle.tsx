import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Switch } from "@/components/ui/switch";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";

interface SegmentBreakToggleProps {
  checked: boolean;
  /** 写入新值；resolve 后开关才可再次操作。 */
  onChange: (next: boolean) => void | Promise<void>;
  disabled?: boolean;
}

/**
 * 章节切分点开关，不弹确认。悬停或聚焦时说明它对分镜图参考链和宫格分组的影响。
 * 分镜详情里它写进分镜的未保存修改；脚本规划页里写进规划草稿。
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
