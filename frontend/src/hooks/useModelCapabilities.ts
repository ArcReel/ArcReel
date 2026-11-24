import { useEffect, useRef, useState } from "react";
import { API, ApiRequestError } from "@/api";
import { isDemoProject } from "@/onboarding/demo-project";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import type { DurationExclusionReason, VideoCapabilities, VoiceConsistencyTier } from "@/types";

export interface ModelCapabilitiesInput {
  projectName?: string | null;
  videoBackend?: string | null;
  videoResolution?: string | null;
  usesReferenceImages?: boolean;
  usesEndFrame?: boolean;
  enabled?: boolean;
}

export interface ModelCapabilities {
  rawDurations: number[] | null;
  supportedDurations: number[] | null;
  planningDurations: number[] | null;
  excludedDurations: Record<string, DurationExclusionReason>;
  durationEndpointFixed: boolean;
  resolvedVideoBackend: string | null;
  firstFrame: boolean | null;
  lastFrame: boolean | null;
  voiceConsistency: VoiceConsistencyTier | null;
  videoModelUnresolved: boolean;
  videoModelError: string | null;
  loading: boolean;
}

export type DurationOutOfRangeReason = "model" | DurationExclusionReason;

export function durationOutOfRangeReason(
  saved: number | null | undefined,
  caps: Pick<ModelCapabilities, "rawDurations" | "supportedDurations" | "excludedDurations">,
): DurationOutOfRangeReason | null {
  const { rawDurations, supportedDurations, excludedDurations } = caps;
  if (saved == null || !supportedDurations || supportedDurations.includes(saved)) return null;
  if (supportedDurations.length === 0 && !rawDurations) return null;
  if (!rawDurations?.includes(saved)) return "model";
  return excludedDurations[String(saved)] ?? "model";
}

const EMPTY_EXCLUSIONS: Record<string, DurationExclusionReason> = {};

function ascending(values: readonly number[]): number[] {
  return [...values].sort((a, b) => a - b);
}

export function useModelCapabilities({
  projectName,
  videoBackend,
  videoResolution,
  usesReferenceImages,
  usesEndFrame = false,
  enabled = true,
}: ModelCapabilitiesInput): ModelCapabilities {
  const revision = useCapabilitiesStore((s) => s.revision);
  const [result, setResult] = useState<{
    key: string;
    contextKey: string;
    caps: VideoCapabilities | null;
    unresolved: boolean;
    error: string | null;
  } | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const active = enabled && (projectName ? !isDemoProject(projectName) : !!videoBackend);
  const key = active ? JSON.stringify([projectName ?? null, videoBackend ?? ""]) : null;
  const contextKey = JSON.stringify([
    videoResolution === undefined,
    videoResolution ?? null,
    usesReferenceImages ?? null,
  ]);

  useEffect(() => {
    abortRef.current?.abort();
    if (key === null) {
      abortRef.current = null;
      return;
    }
    const controller = new AbortController();
    abortRef.current = controller;
    const { signal } = controller;
    const query = {
      signal,
      resolution: videoResolution,
      usesReferenceImages,
    };
    const request = projectName
      ? API.getVideoCapabilities(projectName, { ...query, videoBackend: videoBackend || undefined })
      : API.getModelVideoCapabilities(videoBackend ?? "", query);
    request
      .then((next) => {
        if (signal.aborted) return;
        setResult({ key, contextKey, caps: next, unresolved: false, error: null });
      })
      .catch((err: unknown) => {
        if (signal.aborted) return;
        const unresolved = err instanceof ApiRequestError && (err.status === 400 || err.status === 422);
        setResult({ key, contextKey, caps: null, unresolved, error: unresolved ? err.message : null });
      });
    return () => {
      controller.abort();
    };
  }, [key, contextKey, projectName, videoBackend, videoResolution, usesReferenceImages, revision]);

  const settled = key !== null && result?.key === key;
  const caps = settled ? result.caps : null;
  const fresh = settled && result.contextKey === contextKey;
  const constraints = caps?.duration_constraints ?? null;

  return {
    rawDurations: caps?.supported_durations?.length ? ascending(caps.supported_durations) : null,
    supportedDurations: usesEndFrame && caps?.end_frame_unit_durations
      ? caps.end_frame_unit_durations.with_end_frame : constraints ? constraints.allowed : null,
    planningDurations: constraints ? (constraints.planning ?? constraints.allowed) : null,
    excludedDurations: usesEndFrame && caps?.end_frame_unit_durations
      ? caps.end_frame_unit_durations.excluded : constraints?.excluded ?? EMPTY_EXCLUSIONS,
    durationEndpointFixed: caps?.duration_endpoint_fixed ?? false,
    resolvedVideoBackend: caps ? `${caps.provider_id}/${caps.model}` : null,
    firstFrame: caps ? caps.first_frame : null,
    lastFrame: caps ? caps.last_frame : null,
    voiceConsistency: caps ? caps.voice_consistency : null,
    videoModelUnresolved: fresh && (result.unresolved || !!(usesEndFrame && caps?.end_frame_unit_durations?.problem)),
    videoModelError: fresh ? result.error : null,
    loading: key !== null && !fresh,
  };
}
