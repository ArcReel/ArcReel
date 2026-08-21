import type { TFunction } from "i18next";
import type {
  AdmissionProblem,
  BatchAdmissionUnit,
  GenerationProblem,
  WorkflowActionType,
  WorkflowBlocker,
} from "@/types/workflow";

/**
 * 面板里所有「哪里出了问题」的统一呈现形状。三类来源（整批准入判定的逐单元缺口、
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
 * 各调用点不各写一遍。动作类型是 {@link WorkflowActionType} 那个闭集，每个取值都配了
 * 文案（覆盖检查见 `action-language.test.ts`）；`action_unknown` 只作运行时防线，接住
 * 后端先于前端上线的新动作——说不出是哪个动作，也好过把这一步整个吞掉。
 */
export function nextStepForAction(t: Translate, action: WorkflowActionType): string {
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
function problemUnitId(problem: GenerationProblem | AdmissionProblem): string | null {
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

/** 整批准入判定里「自身没问题、随本批一起未提交」的标记。 */
const WITHHELD_CODE = "generation_batch_admission_withheld";

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

/**
 * 入队中断时没轮到的目标。准入已经通过，缺口不在单元自身，所以不带档位对比——
 * 用户要知道的是哪几个没排上、各自为什么，档位在这里只是噪声。
 *
 * 参数取结构而非具体类型：这层是通用问题行的归一处，不反向依赖某条路线的回执类型。
 * 服务端已把文案本地化进 `message`，与准入缺口同一形状；`detail` 是可选字段，缺省时
 * 问题行不带折叠详情。
 */
export function enqueueFailureViews(
  t: Translate,
  failures: readonly { unit_id: string; problem: AdmissionProblem }[],
): ProblemView[] {
  return failures.map((failure, index) => ({
    key: `enqueue-${failure.unit_id}-${index}`,
    unitId: failure.unit_id,
    field: problemField(failure.problem),
    summary:
      failure.problem.message ??
      localizedSummary(t, failure.problem.code, failure.problem.detail ?? ""),
    nextStep: nextStepFor(t, failure.problem.action),
    detail: failure.problem.detail ?? null,
  }));
}
