interface PillSwitchProps {
  checked: boolean;
  onToggle: () => void;
  /** 无可见文字标签时的无障碍名称；有关联可见标签时传 labelledBy 代替。 */
  label?: string;
  labelledBy?: string;
  describedBy?: string;
  disabled?: boolean;
}

/** 极简 pill 开关。原生 button + role="switch"，键盘与读屏走平台默认语义。 */
export function PillSwitch({
  checked,
  onToggle,
  label,
  labelledBy,
  describedBy,
  disabled,
}: PillSwitchProps) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={labelledBy ? undefined : label}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={onToggle}
      className={`relative h-[18px] w-8 shrink-0 rounded-full border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent disabled:cursor-not-allowed disabled:opacity-40 ${
        checked ? "border-accent/60 bg-accent-soft" : "border-hairline bg-bg-grad-a/70"
      }`}
    >
      {/* 动画只走 transform / color，不动 left：布局属性逐帧触发重排 */}
      <span
        aria-hidden
        className={`absolute left-[2px] top-1/2 h-3 w-3 -translate-y-1/2 rounded-full transition-[translate,background-color] duration-150 motion-reduce:transition-none ${
          checked ? "translate-x-[13px] bg-accent-2" : "translate-x-0 bg-text-4"
        }`}
      />
    </button>
  );
}
