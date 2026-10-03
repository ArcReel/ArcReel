import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { PillSwitch } from "@/components/legacy/PillSwitch";

interface SegmentBreakToggleProps {
  checked: boolean;
  /** 写入新值；resolve 后开关才可再次操作。 */
  onChange: (next: boolean) => void | Promise<void>;
  disabled?: boolean;
}

/** 章节切分点开关：切换即保存，不弹确认。悬停说明它对分镜图参考链和宫格分组的影响。 */
export function SegmentBreakToggle({ checked, onChange, disabled = false }: SegmentBreakToggleProps) {
  const { t } = useTranslation("dashboard");
  const labelId = useId();
  const hintId = useId();
  const [saving, setSaving] = useState(false);

  const toggle = async () => {
    if (saving) return;
    setSaving(true);
    try {
      await onChange(!checked);
    } finally {
      setSaving(false);
    }
  };

  return (
    <span className="inline-flex items-center gap-1.5" title={t("segment_break_hint")}>
      <span id={labelId} className="text-[11px]" style={{ color: "var(--color-text-3)" }}>
        {t("segment_break_toggle")}
      </span>
      <span id={hintId} hidden>
        {t("segment_break_hint")}
      </span>
      <PillSwitch
        checked={checked}
        onToggle={() => void toggle()}
        labelledBy={labelId}
        describedBy={hintId}
        disabled={disabled || saving}
      />
    </span>
  );
}
