/**
 * Grid image-to-video type definitions.
 *
 * Maps to backend models in lib/grid_manager.py and server/routers/grids.py.
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
 * 宫格档位能力。`max_cell_count` 是单张宫格的格数上限，前端据它镜像后端阶梯。
 * 注意 GridGeneration.grid_size 存的是历史记录写入时的档位字符串（含已停用的 grid_6），
 * 故保持宽松的 string 类型。
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
  /** 联合图生命周期：completed 仅表示联合图就绪，是否已落格由 split_at 表达 */
  status: "pending" | "generating" | "completed" | "failed";
  prompt: string | null;
  provider: string;
  model: string;
  grid_size: string;
  created_at: string;
  error_message: string | null;
  reference_images?: ReferenceImage[] | null;
  /** 最近一次按当前联合图切分落格的时间；联合图内容变更（重生成/上传/还原）后为 null */
  split_at: string | null;
}
