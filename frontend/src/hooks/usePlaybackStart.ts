import { useMemo } from "react";

import { useAppStore } from "@/stores/app-store";
import type { PresentationResourceType } from "@/types/presentation";

/** 针对某个视频单元的起始播放请求（来自应用内链接），交给 `PresentationPlayer` 的 `startAt` 与 `onStartApplied`；没带时间点的请求不定位。 */
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
