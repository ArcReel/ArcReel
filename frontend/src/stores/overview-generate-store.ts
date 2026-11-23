import { create } from "zustand";

import { API } from "@/api";
import { errMsg } from "@/utils/async";
import { outputTruncationOfError, type OutputTruncation } from "@/utils/output-truncation";

import { useProjectsStore } from "./projects-store";

export type StoryGenerateError =
  | { kind: "generate"; message: string; truncation: OutputTruncation | null }
  | { kind: "refresh" };

interface OverviewGenerateState {
  generating: Record<string, true>;
  errors: Record<string, StoryGenerateError>;
  generate: (projectName: string) => Promise<void>;
}

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record };
  delete next[key];
  return next;
}

export const useOverviewGenerateStore = create<OverviewGenerateState>((set, get) => ({
  generating: {},
  errors: {},
  generate: async (projectName) => {
    if (get().generating[projectName]) return;
    set((s) => ({ generating: { ...s.generating, [projectName]: true }, errors: without(s.errors, projectName) }));
    let error: StoryGenerateError | null = null;
    try {
      await API.generateOverview(projectName);
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

useProjectsStore.subscribe((state, prev) => {
  const projectName = state.currentProjectName;
  if (!projectName || !state.currentProjectData || state.currentProjectData === prev.currentProjectData) return;
  if (useOverviewGenerateStore.getState().errors[projectName]?.kind !== "refresh") return;
  useOverviewGenerateStore.setState((s) => ({ errors: without(s.errors, projectName) }));
});
