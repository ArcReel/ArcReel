
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

export interface GridCapability {
  reference_inputs?: Array<{ scene_ids: string[]; count: number; limit: number | null; message?: string | null }>;
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
  status: "pending" | "generating" | "completed" | "failed";
  prompt: string | null;
  provider: string;
  model: string;
  grid_size: string;
  created_at: string;
  error_message: string | null;
  reference_images?: ReferenceImage[] | null;
  split_at: string | null;
  video_aspect_ratio?: string | null;
}
