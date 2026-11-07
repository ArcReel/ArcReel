import { startTransition, useCallback } from "react";
import { useLocation } from "wouter";

import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { AppLink } from "@/utils/app-link";
import { normalizeRoute } from "@/utils/generation-mode";

/**
 * Open in-app links by navigating to the target route. For video-unit links, reuse existing scroll/focus behavior to select the unit,
 * request its preview, and start at the supplied timestamp if present.
 * Focus-target and playback-resource types use the current project's generation mode, so links to other projects only navigate without focus or playback requests.
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
