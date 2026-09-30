import { useEffect, useRef } from "react";
import { create } from "zustand";

/**
 * 集页画布上可以被点名打开的区域：脚本规划（内容确认与脚本规划草稿都在这里），
 * 参考生视频的提示词编写草稿，以及集原文（无原文的集的填写框）。
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
 * 制作进度面板里「去确认」「手动修改」这类跳转的投递处：面板只登记要看的区域，
 * 集页上的画布切到对应的标签页。每次登记都是一个新对象，重复点同一处也会再切一次。
 */
export const useEpisodeSurfaceStore = create<EpisodeSurfaceState>((set) => ({
  request: null,
  show: (request) => set({ request: { ...request } }),
}));

/** 画布订阅：点名本项目、本集、这块区域时调用 `onShow`。 */
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
