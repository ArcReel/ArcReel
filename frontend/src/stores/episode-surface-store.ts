import { useEffect, useRef } from "react";
import { create } from "zustand";

/**
 * Episode-canvas regions addressable for opening: script planning, including content confirmation and planning drafts,
 * reference-to-video prompt-authoring drafts, and episode source text, including input for sourceless episodes.
 */
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

/**
 * Destination for production-panel navigation such as Confirm or Edit manually: register only the requested region,
 * and let the episode canvas switch tabs. Each registration is a new object so repeated clicks on the same region switch again.
 */
export const useEpisodeSurfaceStore = create<EpisodeSurfaceState>((set) => ({
  request: null,
  show: (request) => set({ request: { ...request } }),
}));

/** Canvas subscription: call `onShow` when this project, episode and region are requested. */
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
