/**
 * Grid image-to-video type definitions.
 *
 * Maps to backend models in lib/grid_manager.py and server/routers/grids.py.
 */

export interface FrameCell {
  index: number;
  row: number;
  col: number;
  frame_type: "first" | "transition" | "placeholder";
  prev_scene_id: string | null;
  next_scene_id: string | null;
  image_path: string | null;
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
  status: "pending" | "generating" | "splitting" | "completed" | "failed";
  prompt: string | null;
  provider: string;
  model: string;
  grid_size: string;
  created_at: string;
  error_message: string | null;
}
