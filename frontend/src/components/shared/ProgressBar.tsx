import type { CSSProperties } from "react";

interface ProgressBarProps {
  value: number;
  label?: string;
  min?: number;
  max?: number;
  className?: string;
  barClassName?: string;
  style?: CSSProperties;
  barStyle?: CSSProperties;
}

function clamp(value: number, min: number, max: number): number {
  if (Number.isNaN(value)) return min;
  return Math.min(Math.max(value, min), max);
}

function cx(...classes: (string | undefined)[]): string {
  return classes.filter(Boolean).join(" ");
}

/** Accessible progress bar — role=progressbar + aria-value*; visual styling via className / barClassName. */
export function ProgressBar({
  value,
  label,
  min = 0,
  max = 100,
  className,
  barClassName,
  style,
  barStyle,
}: ProgressBarProps) {
  const clamped = clamp(value, min, max);
  const pct = max === min ? 0 : ((clamped - min) / (max - min)) * 100;

  return (
    <div
      role="progressbar"
      aria-valuenow={clamped}
      aria-valuemin={min}
      aria-valuemax={max}
      aria-label={label}
      className={cx("h-1.5 w-full overflow-hidden rounded-full bg-bg-grad-a", className)}
      style={style}
    >
      <div
        className={cx("h-full rounded-full bg-accent transition-[width]", barClassName)}
        style={{ ...barStyle, width: `${pct}%` }}
      />
    </div>
  );
}
