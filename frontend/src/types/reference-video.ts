/**
 * Reference-to-video unit types — mirrors lib/script_models.py Pydantic models.
 *
 * One "unit" produces one rendered video clip. Each unit may contain 1-4 shots.
 */

import type { TransitionType } from "./script";

export type AssetKind = "character" | "scene" | "prop";

export interface Shot {
  /** 1-15s per shot */
  duration: number;
  /** Raw prompt text including @mentions */
  text: string;
}

export interface ReferenceResource {
  type: AssetKind;
  /** Must already exist in project.json {characters|scenes|props} bucket */
  name: string;
}

/**
 * Raw persisted status value returned by the backend in `generated_assets.status`.
 * Mirrors lib/script_models.py:GeneratedAssets.status Pydantic Literal exactly.
 * Note: "storyboard_ready" never appears for reference_video units — it's a legacy
 * storyboard-mode value retained in the shared GeneratedAssets model.
 */
export type UnitPersistedStatus = "pending" | "storyboard_ready" | "completed";

/**
 * UI-derived status shown in the UnitList status dot and preview panel.
 * Composed from (persisted status + task-queue state + error signals) by UI code.
 * Not sent to or received from the backend.
 */
export type UnitStatus = "pending" | "running" | "ready" | "failed";

export interface UnitGeneratedAssets {
  storyboard_image: string | null;
  storyboard_last_image: string | null;
  grid_id: string | null;
  grid_cell_index: number | null;
  video_clip: string | null;
  video_uri: string | null;
  /** Raw backend status — use `UnitStatus` for UI display. */
  status: UnitPersistedStatus;
}

export interface ReferenceVideoUnit {
  /** Format: "E{episode}U{index}" */
  unit_id: string;
  shots: Shot[];
  /** Ordered — position defines [图N] index in the final prompt */
  references: ReferenceResource[];
  /** Sum of shots[].duration; server-derived */
  duration_seconds: number;
  /** True when prompt has no Shot markers and user set duration manually */
  duration_override: boolean;
  transition_to_next: TransitionType;
  note: string | null;
  generated_assets: UnitGeneratedAssets;
}

export interface ReferenceVideoScript {
  episode: number;
  title: string;
  content_mode: "reference_video";
  duration_seconds: number;
  summary: string;
  schema_version?: number;
  novel: { title: string; chapter: string };
  video_units: ReferenceVideoUnit[];
}
