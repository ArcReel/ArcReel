import type { ArtifactStatus, WorkflowStepState } from "@/types/workflow";
import { SEVERITY_TONES, type ToneTokens } from "@/utils/severity-tone";

/**
 * 工作流面板的状态语言。同一份事实有三条互不替代的轴，界面用**形状**区分它们，
 * 颜色只在同一条轴内部区分程度：
 *
 * - 产物时效 → 填充块（`ARTIFACT_FILLS`）：磁盘上确实有一件东西
 * - 步骤进度 → 左侧轨道刻度（`STEP_RAILS`）：编排走到哪
 * - 任务与 provider checkpoint → 描边胶囊：一次尝试，不是一件东西
 *
 * 形状先于颜色，是因为四种产物时效里有三种要在同一条计量条上并置：只靠色相区分，
 * 色觉差异用户会把 stale 读成 current。纹理差异在灰度下依然成立。
 */

/**
 * 计量条上并置的三种时效。`blocked` 不在其中：容器读不出来时一个 id 都数不出来，
 * 那一步整条计量条都不摊，只留一句「读不出来」。
 */
export const METER_SEGMENTS = ["current", "stale", "missing"] as const;

export type MeterSegment = (typeof METER_SEGMENTS)[number];

/**
 * 面板内联动作的统一形态：文字链而非实心按钮。面板的主体是陈述，动作是陈述里的一个去处，
 * 做成实心按钮会把「这里有事要你做」的分量加到每一行上。焦点环与悬停态照旧齐备。
 */
export const INLINE_ACTION_CLS =
  "focus-ring rounded text-[11.5px] underline underline-offset-2 hover:opacity-80 disabled:opacity-50 disabled:hover:opacity-50";

/** 未登记状态词的落点：说不出程度就不着色，绝不在查表上崩掉整个面板。 */
export const NEUTRAL_TONE: ToneTokens = {
  color: "var(--color-text-3)",
  soft: "transparent",
  ring: "var(--color-hairline-strong)",
  glow: "transparent",
};

const CURRENT_TONE: ToneTokens = {
  color: "var(--color-accent-2)",
  soft: "var(--color-accent-dim)",
  ring: "var(--color-accent-soft)",
  glow: "var(--color-accent-glow)",
};

/** 产物时效的色调。missing 刻意是中性色：缺失不是故障，只是还没做。 */
export const ARTIFACT_TONES: Record<ArtifactStatus, ToneTokens> = {
  current: CURRENT_TONE,
  stale: SEVERITY_TONES.warnings,
  missing: NEUTRAL_TONE,
  blocked: SEVERITY_TONES.blocking,
};

/**
 * 计量条分段的填充。三种并置时效三种纹理，灰度打印下仍可区分：
 * current 实心 / stale 斜纹 / missing 空槽。
 */
export function artifactFill(status: MeterSegment): React.CSSProperties {
  const tone = ARTIFACT_TONES[status];
  switch (status) {
    case "current":
      return { background: tone.color, border: `1px solid ${tone.color}` };
    case "stale":
      return {
        background: `repeating-linear-gradient(45deg, ${tone.color} 0 2px, transparent 2px 5px)`,
        border: `1px solid ${tone.ring}`,
      };
    case "missing":
      return { background: "transparent", border: `1px dashed ${tone.ring}` };
  }
}

/**
 * 状态词的色调。`partial` 与 stale 同调——都是「东西在，只是不齐」，
 * 用户要做的判断是同一类。未登记的取值走中性色，后端加新状态词时面板照常出得来。
 */
export function artifactStateTone(state: string): ToneTokens {
  if (state === "partial") return ARTIFACT_TONES.stale;
  return ARTIFACT_TONES[state as ArtifactStatus] ?? NEUTRAL_TONE;
}

export interface StepRail {
  tone: ToneTokens;
  /** 轨道刻度的呈现：实心 / 脉动 / 断口 / 空心 / 虚线。 */
  mark: "solid" | "pulse" | "gap" | "hollow" | "dashed";
}

/**
 * 步骤进度的轨道刻度。`active` 与 `ready` 都还没有产物，靠脉动与静止区分
 * 「正在跑」和「可以开跑」——这两者若同色同形，用户会重复点生成。
 */
export const STEP_RAILS: Record<WorkflowStepState, StepRail> = {
  completed: { tone: CURRENT_TONE, mark: "solid" },
  ready: { tone: CURRENT_TONE, mark: "hollow" },
  active: { tone: CURRENT_TONE, mark: "pulse" },
  blocked: { tone: SEVERITY_TONES.blocking, mark: "gap" },
  pending: { tone: NEUTRAL_TONE, mark: "hollow" },
  skipped: { tone: NEUTRAL_TONE, mark: "dashed" },
};

/** 任务这一轴的色调：进行中用强调色，终态失败用告警色，其余中性。 */
export function taskTone(status: string): ToneTokens {
  if (status === "queued" || status === "running" || status === "cancelling") return CURRENT_TONE;
  if (status === "failed" || status === "interrupted") return SEVERITY_TONES.blocking;
  if (status === "succeeded") return CURRENT_TONE;
  return NEUTRAL_TONE;
}
