export type DiagnosticSeverity = "blocking" | "auto_fixed" | "warnings";

export interface ToneTokens {
  /** Foreground (icon / text) */
  color: string;
  /** Soft panel background */
  soft: string;
  /** Border / ring */
  ring: string;
  /** Drop shadow color */
  glow: string;
}

export const SEVERITY_TONES: Record<DiagnosticSeverity, ToneTokens> = {
  blocking: {
    color: "var(--destructive)",
    soft: "color-mix(in oklab, var(--destructive) 10%, transparent)",
    ring: "color-mix(in oklab, var(--destructive) 30%, transparent)",
    glow: "color-mix(in oklab, var(--destructive) 35%, transparent)",
  },
  auto_fixed: {
    color: "var(--primary)",
    soft: "color-mix(in oklab, var(--primary) 12%, transparent)",
    ring: "color-mix(in oklab, var(--primary) 22%, transparent)",
    glow: "color-mix(in oklab, var(--primary) 35%, transparent)",
  },
  warnings: {
    color: "var(--warn)",
    soft: "color-mix(in oklab, var(--warn) 10%, transparent)",
    ring: "color-mix(in oklab, var(--warn) 30%, transparent)",
    glow: "color-mix(in oklab, var(--warn) 35%, transparent)",
  },
};

// 暖色调单独导出，供非 severity 的暖系场景复用：JianYing 导出 / 资产名冲突 / stale 编辑提示等
export const WARM_TONE: ToneTokens = SEVERITY_TONES.warnings;
