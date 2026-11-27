import { create } from "zustand";

export interface GenerationOptions {
  backend?: string;
  resolution?: string | null;
  aspect_ratio?: "16:9" | "9:16" | "1:1" | "4:3" | "3:4";
  generate_audio?: boolean;
  duration_seconds?: number;
}
export interface ChatOptions {
  model?: string;
  effort?: "low" | "medium" | "high";
}
type RunSettings = { image: GenerationOptions; video: GenerationOptions; chat: ChatOptions };
const EMPTY: RunSettings = { image: {}, video: {}, chat: {} };
export const useRunSettingsStore = create<{
  projects: Record<string, RunSettings>;
  set: (project: string, kind: keyof RunSettings, value: GenerationOptions | ChatOptions) => void;
}>((set) => ({
  projects: {},
  set: (project, kind, value) => set((state) => ({
    projects: { ...state.projects, [project]: { ...(state.projects[project] ?? EMPTY), [kind]: value } },
  })),
}));
export function generationOptions(project: string, kind: "image" | "video"): GenerationOptions | undefined {
  const options = useRunSettingsStore.getState().projects[project]?.[kind];
  return options && Object.keys(options).length > 0 ? options : undefined;
}

export function generationArgs(project: string, kind: "image" | "video"): [] | [GenerationOptions] {
  const value = generationOptions(project, kind);
  return value ? [value] : [];
}
