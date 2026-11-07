/**
 * Grid image-to-video type definitions.
 *
 * Maps to backend models in lib/script/grid/grid_manager.py and server/routers/grids.py.
 */

export interface ReferenceImage {
  path: string;
  name: string;
  ref_type: "character" | "scene" | "prop";
}

export interface FrameCell {
  index: number;
  row: number;
  col: number;
  frame_type: "first" | "transition" | "placeholder";
  prev_scene_id: string | null;
  next_scene_id: string | null;
  image_path: string | null;
}

/**
 * Grid tier capabilities. `max_cell_count` is the maximum cell count per grid image, used by the frontend to mirror the backend tiers.
 * GridGeneration.grid_size stores the tier string at the time the historical record was written (including the discontinued grid_6),
 * so its type remains an unrestricted string.
 */
export interface GridCapability {
  large_grid_allowed: boolean;
  max_cell_count: number;
}

export interface GridGeneration {
  id: string;
  episode: number;
  script_file: string;
  scene_ids: string[];
  grid_image_path: string | null;
  rows: number;
  cols: number;
  cell_count: number;
  frame_chain: FrameCell[];
  /** Composite image lifecycle: completed only means the composite is ready; split_at indicates whether it has been split into cells */
  status: "pending" | "generating" | "completed" | "failed";
  prompt: string | null;
  provider: string;
  model: string;
  grid_size: string;
  created_at: string;
  error_message: string | null;
  reference_images?: ReferenceImage[] | null;
  /** Latest time the current composite image was split into cells; null after its content changes (regeneration/upload/restoration) */
  split_at: string | null;
  /** Target aspect ratio per cell frozen when the current composite was produced; null for legacy records, which fall back to project settings */
  video_aspect_ratio?: string | null;
}
