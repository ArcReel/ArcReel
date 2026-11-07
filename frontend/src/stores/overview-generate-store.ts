import { create } from "zustand";

import { API } from "@/api";
import { errMsg } from "@/utils/async";
import { outputTruncationOfError, type OutputTruncation } from "@/utils/output-truncation";

import { useProjectsStore } from "./projects-store";

/**
 * Why source-generated story settings were not filled in place: `generate` means failed requests, including remedies for truncated output;
 * `refresh` means generation persisted but subsequent project fetches failed, cleared once project data later loads successfully.
 */
export type StoryGenerateError =
  | { kind: "generate"; message: string; truncation: OutputTruncation | null }
  | { kind: "refresh" };

interface OverviewGenerateState {
  /** Projects currently generating story settings from sources. */
  generating: Record<string, true>;
  /** Latest unfilled generation reason per project, cleared on regeneration. */
  errors: Record<string, StoryGenerateError>;
  generate: (projectName: string) => Promise<void>;
}

function without<T>(record: Record<string, T>, key: string): Record<string, T> {
  const next = { ...record };
  delete next[key];
  return next;
}

/**
 * Overview Generate from source progress lives in stores, not overview components: leaving during generation does not stop server persistence;
 * returning must retain loading and fill results on completion. Leaving never cancels requests; page refreshes do not restore them.
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
      // refreshProject reports failure through settlement values, not rejection: persisted generation with old displayed data invites duplicate generation.
      // Switching projects settles cancelled; reloading on return brings results.
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

// Persisted-generation/failed-fetch applies only while pages retain old data. Later successful reloads (for example leaving/returning)
// include generated results and clear notices. Generation-failure notices are data-independent, retained until regeneration.
useProjectsStore.subscribe((state, prev) => {
  const projectName = state.currentProjectName;
  if (!projectName || !state.currentProjectData || state.currentProjectData === prev.currentProjectData) return;
  if (useOverviewGenerateStore.getState().errors[projectName]?.kind !== "refresh") return;
  useOverviewGenerateStore.setState((s) => ({ errors: without(s.errors, projectName) }));
});
