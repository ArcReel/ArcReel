import { startTransition, useCallback } from "react";
import { useLocation } from "wouter";

import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { AppLink } from "@/utils/app-link";
import { normalizeRoute } from "@/utils/generation-mode";

/**
 * 在应用内打开链接：跳转到目标路由；指向视频单元的链接再复用既有的滚动聚焦（选中该单元），
 * 并请求打开单元预览，带了时间点时从该时间开始播放。
 */
export function useOpenAppLink(): (link: AppLink) => void {
  const [, navigate] = useLocation();
  return useCallback(
    (link) => {
      startTransition(() => navigate(`~${link.to}`));
      if (!link.unit) return;
      const referenceVideo = normalizeRoute(useProjectsStore.getState().currentProjectData?.generation_mode) === "reference_video";
      const app = useAppStore.getState();
      app.triggerScrollTo({ type: referenceVideo ? "reference_unit" : "segment", id: link.unit.id });
      app.requestPlaybackStart({
        resource_type: referenceVideo ? "reference_videos" : "videos",
        resource_id: link.unit.id,
        seconds: link.unit.seconds,
      });
    },
    [navigate],
  );
}
