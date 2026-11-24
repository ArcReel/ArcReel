import { useEffect, useRef } from "react";
import { create } from "zustand";

export type EpisodeSurface = "script_plan" | "prompt_authoring_draft" | "episode_source";

interface EpisodeSurfaceRequest {
  projectName: string;
  episode: number;
  surface: EpisodeSurface;
}

interface EpisodeSurfaceState {
  request: EpisodeSurfaceRequest | null;
  show: (request: EpisodeSurfaceRequest) => void;
}

export const useEpisodeSurfaceStore = create<EpisodeSurfaceState>((set) => ({
  request: null,
  show: (request) => set({ request: { ...request } }),
}));

export function useEpisodeSurfaceRequest(
  projectName: string,
  episode: number,
  surface: EpisodeSurface,
  onShow: () => void,
): void {
  const onShowRef = useRef(onShow);
  useEffect(() => {
    onShowRef.current = onShow;
  });
  useEffect(
    () =>
      useEpisodeSurfaceStore.subscribe((state, previous) => {
        const request = state.request;
        if (request === null || request === previous.request) return;
        if (request.projectName !== projectName || request.episode !== episode || request.surface !== surface) return;
        onShowRef.current();
      }),
    [projectName, episode, surface],
  );
}
