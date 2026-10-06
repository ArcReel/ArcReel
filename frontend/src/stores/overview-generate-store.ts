import { create } from "zustand";

import { API } from "@/api";
import { errMsg } from "@/utils/async";
import { outputTruncationOfError, type OutputTruncation } from "@/utils/output-truncation";

import { useProjectsStore } from "./projects-store";

/**
 * 从原文生成故事设定没有就地填入的原因：`generate` 是生成请求失败，输出被截断时附带出路；
 * `refresh` 是生成已落盘，之后取回项目数据失败，该项目的数据之后重新加载成功即清除。
 */
export type StoryGenerateError =
  | { kind: "generate"; message: string; truncation: OutputTruncation | null }
  | { kind: "refresh" };

interface OverviewGenerateState {
  /** 正在从原文生成故事设定的项目。 */
  generating: Record<string, true>;
  /** 各项目最近一次生成没有就地填入的原因；再次生成时清除。 */
  errors: Record<string, StoryGenerateError>;
  generate: (projectName: string) => Promise<void>;
}

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record };
  delete next[key];
  return next;
}

/**
 * 概览「从原文生成」的进行状态。放在 store 而不是概览组件里：生成途中离开概览，服务端仍会写入结果，
 * 回到概览时要接着显示读取中，完成后就地填入。请求不随离开取消；页面刷新后不恢复。
 */
export const useOverviewGenerateStore = create<OverviewGenerateState>((set, get) => ({
  generating: {},
  errors: {},
  generate: async (projectName) => {
    if (get().generating[projectName]) return;
    set((s) => ({ generating: { ...s.generating, [projectName]: true }, errors: without(s.errors, projectName) }));
    let error: StoryGenerateError | null = null;
    try {
      await API.generateOverview(projectName);
      // refreshProject 以结算值报告失败而不 reject：生成已落盘却停在旧内容上，会引人再生成一次。
      // 已切到别的项目时结算为 cancelled，回到该项目时重新加载即带回结果。
      const refreshed = await useProjectsStore.getState().refreshProject(projectName);
      if (refreshed === "failed") error = { kind: "refresh" };
    } catch (err) {
      error = { kind: "generate", message: errMsg(err), truncation: outputTruncationOfError(err) };
    }
    set((s) => ({
      generating: without(s.generating, projectName),
      errors: error ? { ...s.errors, [projectName]: error } : s.errors,
    }));
  },
}));

// 「生成已落盘、取回失败」只在页面还停在旧数据时成立：该项目的数据之后重新加载成功（如切走再回来），
// 已带上生成结果，提示随之清除。生成失败的提示与数据无关，保留到再次生成。
useProjectsStore.subscribe((state, prev) => {
  const projectName = state.currentProjectName;
  if (!projectName || !state.currentProjectData || state.currentProjectData === prev.currentProjectData) return;
  if (useOverviewGenerateStore.getState().errors[projectName]?.kind !== "refresh") return;
  useOverviewGenerateStore.setState((s) => ({ errors: without(s.errors, projectName) }));
});
