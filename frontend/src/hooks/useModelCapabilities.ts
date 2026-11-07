import { useEffect, useRef, useState } from "react";
import { API, ApiRequestError } from "@/api";
import { isDemoProject } from "@/onboarding/demo-project";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import type { DurationExclusionReason, VideoCapabilities, VoiceConsistencyTier } from "@/types";

// ---------------------------------------------------------------------------
// Video-model capabilities: sole frontend consumer, reading server video-capabilities through one path.
//
// Every dimension comes from servers; frontend holds no duplicate rules:
//
//   firstFrame / lastFrame  → effective values (system decisions ⊕ user overrides), server-only.
//   voiceConsistency        → server two-dimensional derivation (model capabilities × project generation modes).
//   durations               → full model declarations + context-narrowed candidates/exclusion causes, all calculated by server video
//                             request facts. Resolution↔duration/reference↔duration narrowing rules live only in
//                             lib/config/resolver.py::constrain_durations.
//   Per-unit reference-video buckets/tiers bypass this hook: servers evaluate available references per unit, returning results with unit lists.
//
// Projects use /projects/{name}/video-capabilities (including unsaved form candidate models/constraint contexts);
// no-project creation wizards use /providers/video-capabilities with candidate models.
// ---------------------------------------------------------------------------

export interface ModelCapabilitiesInput {
  /** Project names; absent names (for example creation wizards before projects exist) use projectless endpoints with `videoBackend`. */
  projectName?: string | null;
  /**
   * Video backend "provider/model"; empty with projects follows persisted server configuration, empty without projects skips queries.
   * Pass unsaved form candidates here too; servers resolve candidate models × project generation modes.
   */
  videoBackend?: string | null;
  /**
   * Resolution for duration narrowing: undefined = saved project tiers (workbench); null = explicit form Auto,
   * never falling back to saved tiers; strings = specified tiers (unsaved form selections).
   */
  videoResolution?: string | null;
  /** Whether to narrow by reference-image paths; undefined lets servers decide from project generation modes. */
  usesReferenceImages?: boolean;
  /** False skips queries (demos, etc.). */
  enabled?: boolean;
}

export interface ModelCapabilities {
  /** Full model duration declarations (ascending), null if unknown. Distinguish unsupported model values from constraint narrowing when classifying bounds. */
  rawDurations: number[] | null;
  /** Context-narrowed duration candidates (ascending), null if unknown. */
  supportedDurations: number[] | null;
  /**
   * Script-planning duration tiers (ascending), null if unknown. Endpoint-fixed durations have empty `supportedDurations`;
   * these are server-borrowed planning tiers, used by content-confirmation pages for duration selection/bounds checks.
   */
  planningDurations: number[] | null;
  /** Full-set durations excluded by linked constraints (second-string keys) → causes; empty maps if unknown. */
  excludedDurations: Record<string, DurationExclusionReason>;
  /**
   * Empty tiers mean endpoint-fixed dimensions (ComfyUI workflows choose lengths), not missing model declarations.
   * Both disable duration controls, with different user explanations. Unknown means false, never falsely reporting.
   */
  durationEndpointFixed: boolean;
  /**
   * Actual queried `provider/model`, null if unknown.
   *
   * Inputs may be bare providers (servers supply default video models) or empty to follow global defaults; use server-resolved
   * results here. Every caller querying project configuration for what was saved for these backends must use this value.
   */
  resolvedVideoBackend: string | null;
  /** Effective first/last-frame values including user overrides; null on unknown/failure, never falsely reporting unsupported. */
  firstFrame: boolean | null;
  lastFrame: boolean | null;
  /** Three voice-consistency tiers; null before results or on query failure (unknown). */
  voiceConsistency: VoiceConsistencyTier | null;
  /**
   * Explicit server response that models lack bucket capabilities or cannot resolve (endpoint 400/422). Network/other failures remain false:
   * capabilities are merely unknown and cannot gate requests.
   */
  videoModelUnresolved: boolean;
  /** Server model-invalidation reasons and remediation. */
  videoModelError: string | null;
  /** Current-context queries in flight, including constraint-context refetches. */
  loading: boolean;
}

/**
 * Saved-duration out-of-bounds causes, null within bounds.
 *
 * Causes determine guidance: `model` requires different durations/models; `resolution` / `reference` also resolve by
 * corresponding setting changes. Values absent from full sets report `model`; otherwise consult server exclusions.
 */
export type DurationOutOfRangeReason = "model" | DurationExclusionReason;

export function durationOutOfRangeReason(
  saved: number | null | undefined,
  caps: Pick<ModelCapabilities, "rawDurations" | "supportedDurations" | "excludedDurations">,
): DurationOutOfRangeReason | null {
  const { rawDurations, supportedDurations, excludedDurations } = caps;
  if (saved == null || !supportedDurations || supportedDurations.includes(saved)) return null;
  if (!rawDurations?.includes(saved)) return "model";
  return excludedDurations[String(saved)] ?? "model";
}

const EMPTY_EXCLUSIONS: Record<string, DurationExclusionReason> = {};

function ascending(values: readonly number[]): number[] {
  return [...values].sort((a, b) => a - b);
}

/**
 * Request-identity keys include only contexts deciding whether results remain usable: projects/backends. Changes immediately discard old
 * capabilities, avoiding stale-value gates. Constraint contexts (resolutions/reference paths) and revisions trigger refetches without entering
 * identity keys: old same-model narrowed results remain best estimates; including them would flash loading and remove/recreate duration
 * selectors whenever forms switch resolutions.
 *
 * Derive loading from persisted-result keys matching current keys, never synchronous setState inside effects,
 * which causes cascading renders (react-hooks/set-state-in-effect).
 */
export function useModelCapabilities({
  projectName,
  videoBackend,
  videoResolution,
  usesReferenceImages,
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

  // Backend demos do not exist; return empty capabilities without requests. Without projects, absent candidate models also prevent queries.
  const active = enabled && (projectName ? !isDemoProject(projectName) : !!videoBackend);
  // Encode tuples instead of concatenating: separators can appear within fields, making ("a b", "c") and ("a", "b c") collide,
  // incorrectly treating previous-context results as current after switches.
  const key = active ? JSON.stringify([projectName ?? null, videoBackend ?? ""]) : null;
  // undefined (follow project) and null (explicit Auto) are distinct requests, but JSON writes array undefined as null;
  // use a separate distinguishing flag.
  const contextKey = JSON.stringify([
    videoResolution === undefined,
    videoResolution ?? null,
    usesReferenceImages ?? null,
  ]);

  useEffect(() => {
    // Taking-over requests rotate controllers, invalidating predecessors before slow responses overwrite newer values.
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
    // Include videoBackend so unsaved form candidates receive their own capabilities; otherwise servers resolve persisted
    // configuration, keeping tiers/other two-dimensional derivations on last-saved models.
    const request = projectName
      ? API.getVideoCapabilities(projectName, { ...query, videoBackend: videoBackend || undefined })
      : API.getModelVideoCapabilities(videoBackend ?? "", query);
    request
      .then((next) => {
        // State-write checkpoint after network awaits: abort may follow already-resolved responses.
        if (signal.aborted) return;
        setResult({ key, contextKey, caps: next, unresolved: false, error: null });
      })
      .catch((err: unknown) => {
        if (signal.aborted) return;
        // Treat resolution failures as unknown capabilities; consumers decide gate degradation, never invent values here.
        const unresolved = err instanceof ApiRequestError && (err.status === 400 || err.status === 422);
        setResult({ key, contextKey, caps: null, unresolved, error: unresolved ? err.message : null });
      });
    return () => {
      controller.abort();
    };
    // projectName / videoBackend already encode into key, videoResolution / usesReferenceImages into
    // contextKey; list only for exhaustive-deps, without extra requests.
  }, [key, contextKey, projectName, videoBackend, videoResolution, usesReferenceImages, revision]);

  const settled = key !== null && result?.key === key;
  const caps = settled ? result.caps : null;
  const fresh = settled && result.contextKey === contextKey;
  const constraints = caps?.duration_constraints ?? null;

  return {
    rawDurations: caps?.supported_durations?.length ? ascending(caps.supported_durations) : null,
    supportedDurations: constraints ? constraints.allowed : null,
    planningDurations: constraints ? (constraints.planning ?? constraints.allowed) : null,
    excludedDurations: constraints?.excluded ?? EMPTY_EXCLUSIONS,
    durationEndpointFixed: caps?.duration_endpoint_fixed ?? false,
    resolvedVideoBackend: caps ? `${caps.provider_id}/${caps.model}` : null,
    firstFrame: caps ? caps.first_frame : null,
    lastFrame: caps ? caps.last_frame : null,
    voiceConsistency: caps ? caps.voice_consistency : null,
    videoModelUnresolved: fresh && result.unresolved,
    videoModelError: fresh ? result.error : null,
    loading: key !== null && !fresh,
  };
}
