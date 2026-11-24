import { useAppStore } from "@/stores/app-store";
import type { GenerationRoute } from "@/utils/generation-mode";

export function openUnitPreview(route: GenerationRoute, unitId: string, seconds: number | null): void {
  const referenceVideo = route === "reference_video";
  const app = useAppStore.getState();
  app.triggerScrollTo({ type: referenceVideo ? "reference_unit" : "segment", id: unitId });
  app.requestPlaybackStart({
    resource_type: referenceVideo ? "reference_videos" : "videos",
    resource_id: unitId,
    seconds,
  });
}
