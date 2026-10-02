import { startTransition, useCallback } from "react";
import { useLocation } from "wouter";

import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { AppLink } from "@/utils/app-link";
import { normalizeRoute } from "@/utils/generation-mode";

/**
 * 在应用内打开链接：跳转到目标路由；指向视频单元的链接再复用既有的滚动聚焦（选中该单元），
 * 并请求打开单元预览，带了时间点时从该时间开始播放。
 * 聚焦目标类型与播放资源类型取自当前项目的生成模式，所以链接指向其他项目时只跳转，不发聚焦与起播请求。
 */
export function useOpenAppLink(): (link: AppLink) => void {
  const [, navigate] = useLocation();
  return useCallback(
    (link) => {
      startTransition(() => navigate(`~${link.to}`));
      if (!link.unit) return;
      const { currentProjectName, currentProjectData } = useProjectsStore.getState();
      if (link.unit.project === null || link.unit.project !== currentProjectName) return;
      const referenceVideo = normalizeRoute(currentProjectData?.generation_mode) === "reference_video";
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
