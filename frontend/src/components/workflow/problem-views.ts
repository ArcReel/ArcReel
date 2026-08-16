import type { TFunction } from "i18next";
import type {
  AdmissionProblem,
  BatchAdmissionUnit,
  GenerationProblem,
  WorkflowBlocker,
} from "@/types/workflow";

/**
 * 面板里所有「哪里出了问题」的统一呈现形状。三类来源（批量准入的逐单元缺口、
 * 计划里的结构化问题、数据损坏 blocker）归一到这里，界面只认这一种行。
 *
 * 四个位置各司其职，不互相顶替：`unitId` / `field` 说的是**在哪**，`summary` 说的是
 * **什么原因**，`nextStep` 说的是**接下来做什么**，`detail` 是留给排障的原文。
 */
export interface ProblemView {
  key: string;
  /** 出问题的单元；项目级问题为空。 */
  unitId?: string | null;
  /** 出问题的字段路径，如 `generation_settings.audio_backend`。 */
  field?: string | null;
  /** 已本地化的一句话原因。 */
  summary: string;
  /** 附加度量，如档位对比。 */
  meta?: string | null;
  /** 已本地化的下一步动作陈述；没有对应文案时为空，不编造。 */
  nextStep?: string | null;
  /** 服务端原文，折叠展示，不进摘要。 */
  detail?: string | null;
}

type Translate = TFunction<"workflow">;

/** 服务端原文只作为兜底：有对应译文时优先用译文，界面不混入未翻译的技术串。 */
function localizedSummary(t: Translate, code: string, fallback: string): string {
  const translated = t(`problem_${code}`, { defaultValue: "" });
  return translated || fallback || code;
}

/**
 * 复述后端给的下一步动作。动作译文是裸的祈使短语，「下一步：」这层框架在这里统一加，
 * 各调用点不各写一遍。动作类型是开放集合，未登记的取值落到 `action_unknown` 兜底陈述——
 * 说不出是哪个动作，也好过把后端明确给出的这一步整个吞掉。
 */
export function nextStepForAction(t: Translate, action: string): string {
  return t("next_step", {
    step: t(`action_${action}`, { defaultValue: t("action_unknown") }),
  });
}

/** 问题行里的下一步。没有动作、或动作没有对应译文时留空，不编造。 */
function nextStepFor(t: Translate, action: string | null | undefined): string | null {
  if (!action || action === "none") return null;
  const phrase = t(`action_${action}`, { defaultValue: "" });
  return phrase ? t("next_step", { step: phrase }) : null;
}

function stringParam(params: Record<string, unknown> | undefined, key: string): string | null {
  const value = params?.[key];
  return typeof value === "string" && value ? value : null;
}

/** 结构化问题里的定位信息藏在 params 里，按已知键提取，取不到就留空而不是瞎猜。 */
export function problemUnitId(problem: GenerationProblem | AdmissionProblem): string | null {
  const direct = stringParam(problem.params, "unit_id");
  if (direct) return direct;
  const admission = problem.params?.["speech_admission"];
  if (admission && typeof admission === "object") {
    const nested = (admission as Record<string, unknown>)["unit_id"];
    if (typeof nested === "string" && nested) return nested;
  }
  return null;
}

function problemField(problem: GenerationProblem | AdmissionProblem): string | null {
  const field = stringParam(problem.params, "field") ?? stringParam(problem.params, "path");
  if (field) return field;
  const path = problem.params?.["path"];
  if (Array.isArray(path)) {
    const parts = path.filter((part): part is string => typeof part === "string");
    if (parts.length > 0) return parts.join(".");
  }
  return null;
}

export function problemViews(
  t: Translate,
  problems: GenerationProblem[],
  keyPrefix = "problem",
): ProblemView[] {
  return problems.map((problem, index) => ({
    key: `${keyPrefix}-${problem.code}-${index}`,
    unitId: problemUnitId(problem),
    field: problemField(problem),
    summary: localizedSummary(t, problem.code, problem.detail),
    nextStep: nextStepFor(t, problem.action),
    detail: problem.detail,
  }));
}

/**
 * 数据损坏的 blocker。`path` 是用户要去修的具体字段，进摘要行；`reason` 是
 * 服务端原文，进折叠区——先给能读懂的一句，排障细节在展开后才出现。
 */
export function blockerViews(t: Translate, blockers: WorkflowBlocker[]): ProblemView[] {
  return blockers.map((blocker, index) => ({
    key: `blocker-${blocker.code}-${index}`,
    field: blocker.path,
    summary: localizedSummary(t, blocker.code, t("blocker_generic")),
    nextStep: nextStepFor(t, "repair_project_data"),
    detail: blocker.reason,
  }));
}

/** 批量准入里「自身没问题、随本批一起未提交」的标记。 */
export const WITHHELD_CODE = "generation_batch_admission_withheld";

export function isWithheld(unit: BatchAdmissionUnit): boolean {
  return unit.withheld === true || unit.problems.some((problem) => problem.code === WITHHELD_CODE);
}

/**
 * 逐单元的准入缺口。档位对比与原因同行呈现：光说「时长超上限」看不出差多少，
 * 用户判断该去改什么主要靠这两个数字。
 */
export function admissionUnitViews(
  t: Translate,
  units: BatchAdmissionUnit[],
  formatSeconds: (value: number) => string,
): ProblemView[] {
  const views: ProblemView[] = [];
  for (const unit of units) {
    const meta =
      unit.current_duration_seconds != null || unit.request_duration_seconds != null
        ? t("unit_tiers", {
            current:
              unit.current_duration_seconds != null
                ? formatSeconds(unit.current_duration_seconds)
                : t("tier_unknown"),
            request:
              unit.request_duration_seconds != null
                ? formatSeconds(unit.request_duration_seconds)
                : t("tier_unknown"),
          })
        : null;
    unit.problems.forEach((problem, index) => {
      views.push({
        key: `${unit.unit_id}-${problem.code}-${index}`,
        unitId: unit.unit_id,
        field: problemField(problem),
        // 批量端点已经把文案本地化进 message；计划端点没有，回退到按 code 查译文表。
        summary: problem.message ?? localizedSummary(t, problem.code, problem.detail ?? ""),
        meta: index === 0 ? meta : null,
        nextStep: nextStepFor(t, problem.action),
        detail: problem.detail ?? null,
      });
    });
  }
  return views;
}
