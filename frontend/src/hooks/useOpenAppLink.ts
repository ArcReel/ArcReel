import { startTransition, useCallback } from "react";
import { useLocation } from "wouter";

import { openUnitPreview } from "@/actions/open-unit-preview";
import { useProjectsStore } from "@/stores/projects-store";
import type { AppLink } from "@/utils/app-link";
import { normalizeRoute } from "@/utils/generation-mode";

export function useOpenAppLink(): (link: AppLink) => void {
  const [, navigate] = useLocation();
  return useCallback(
    (link) => {
      const { currentProjectName, currentProjectData, projectDetailLoading } = useProjectsStore.getState();
      const unit = link.unit;
      if (unit?.project && unit.project === currentProjectName && currentProjectData && !projectDetailLoading) {
        startTransition(() => navigate(`~${link.to}`));
        openUnitPreview(normalizeRoute(currentProjectData.generation_mode), unit.id, unit.seconds);
      } else {
        startTransition(() => navigate(`~${link.href}`));
      }
    },
    [navigate],
  );
}
