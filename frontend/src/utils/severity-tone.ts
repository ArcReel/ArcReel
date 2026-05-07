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
    color: "var(--color-danger-2)",
    soft: "var(--color-danger-soft)",
    ring: "var(--color-danger-ring)",
    glow: "var(--color-danger-glow)",
  },
  auto_fixed: {
    color: "var(--color-accent-2)",
    soft: "var(--color-accent-dim)",
    ring: "var(--color-accent-soft)",
    glow: "var(--color-accent-glow)",
  },
  warnings: {
    color: "var(--color-warm)",
    soft: "var(--color-warm-soft)",
    ring: "var(--color-warm-ring)",
    glow: "var(--color-warm-glow)",
  },
};

// 暖色调单独导出，供非 severity 的暖系场景复用：JianYing 导出 / 资产名冲突 / stale 编辑提示等
export const WARM_TONE: ToneTokens = SEVERITY_TONES.warnings;
