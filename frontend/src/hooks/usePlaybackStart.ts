import { useMemo } from "react";

import { useAppStore } from "@/stores/app-store";
import type { PresentationResourceType } from "@/types/presentation";

export function usePlaybackStart(resourceType: PresentationResourceType, resourceId: string) {
  const request = useAppStore((s) => s.playbackStart);
  const clearPlaybackStart = useAppStore((s) => s.clearPlaybackStart);
  const matches = request?.resource_type === resourceType && request.resource_id === resourceId;
  const seconds = matches ? request.seconds : null;
  const requestId = matches ? request.request_id : null;
  const startAt = useMemo(
    () => (seconds !== null && requestId !== null ? { seconds, requestId } : undefined),
    [seconds, requestId],
  );
  return { startAt, onStartApplied: clearPlaybackStart };
}
