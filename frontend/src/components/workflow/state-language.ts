import type { ArtifactStatus } from "@/types/workflow";
import { SEVERITY_TONES, type ToneTokens } from "@/utils/severity-tone";

/**
 * 工作流面板的状态色调：产物时效（过期产物提醒）、阻断提示与任务（描边胶囊）
 * 各用一组色调，颜色只在同一条轴内部区分程度。
 */

/**
 * 行内陈述里的动作（过期产物的查看与重生、档位确认）的统一形态：文字链而非实心按钮。
 * 实心与描边只留给下一步的主入口，行内动作不抢它的分量。焦点环与悬停态照旧齐备。
 */
export const INLINE_ACTION_CLS =
  "focus-ring rounded text-[11.5px] underline underline-offset-2 hover:opacity-80 disabled:opacity-50 disabled:hover:opacity-50";

/** 未登记状态词的落点：说不出程度就不着色，绝不在查表上崩掉整个面板。 */
const NEUTRAL_TONE: ToneTokens = {
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

/** 项目整体不可用（阻断）的色调。 */
export const BLOCKED_TONE: ToneTokens = SEVERITY_TONES.blocking;

/** 任务这一轴的色调：进行中用强调色，终态失败用告警色，其余中性。 */
export function taskTone(status: string): ToneTokens {
  if (status === "queued" || status === "running") return CURRENT_TONE;
  if (status === "failed" || status === "interrupted") return SEVERITY_TONES.blocking;
  if (status === "succeeded") return CURRENT_TONE;
  return NEUTRAL_TONE;
}
