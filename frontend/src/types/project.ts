/**
 * Project-related type definitions.
 *
 * Maps to backend models in:
 * - lib/project_manager.py (ProjectOverview, project.json structure)
 * - lib/status_calculator.py (ProjectStatus, EpisodeMeta computed fields)
 * - server/routers/projects.py (ProjectSummary list response)
 */

import type { VoiceConsistencyTier } from "@/types/provider";
import type { GenerationRoute } from "@/utils/generation-mode";

export interface ProjectOverview {
  synopsis: string;
  genre: string;
  theme: string;
  world_setting: string;
  generated_at?: string;
}

export interface Character {
  description: string;
  character_sheet?: string;
  voice_style?: string;
  reference_image?: string;
  reference_audio?: string;
  /** reference_audio 当前生效版本的设置/更新时间（ISO8601），由后端在写入时机械戳。 */
  voice_updated_at?: string;
  /** 已确认到的声音版本（ISO8601，取关闭时的 voice_updated_at 原值）；
   *  voice_updated_at 晚于此值即视为新版本。 */
  voice_notice_dismissed_at?: string;
}

export interface Scene {
  description: string;
  scene_sheet?: string;
}

export interface Prop {
  description: string;
  prop_sheet?: string;
}

export interface Product {
  description: string;
  /** 标准多角度产品参考图（可选，生成/上传后回写）。 */
  product_sheet?: string;
  /** 品牌要素自由文本。 */
  brand?: string;
  /** 用户上传的产品原图路径列表（保真验收锚点，系统级字段）。 */
  reference_images?: string[];
  /** 卖点列表（agent 起草、用户可改）。 */
  selling_points?: string[];
}

export interface AspectRatio {
  characters?: string;
  scenes?: string;
  props?: string;
  storyboard?: string;
  video?: string;
}

export interface ProgressCategory {
  total: number;
  completed: number;
}

export interface EpisodesSummary {
  total: number;
  scripted: number;
  in_production: number;
  completed: number;
}

export const PHASE_ORDER = [
  "setup",
  "worldbuilding",
  "scripting",
  "production",
  "completed",
] as const;

export type Phase = (typeof PHASE_ORDER)[number];

/** Injected by StatusCalculator.calculate_project_status at read time */
export interface ProjectStatus {
  current_phase: Phase;
  phase_progress: number;
  characters: ProgressCategory;
  scenes: ProgressCategory;
  props: ProgressCategory;
  episodes_summary: EpisodesSummary;
}

export interface EpisodeMeta {
  episode: number;
  title: string;
  script_file: string;
  /** Written by episode_planner at split time: ending hook / suspense */
  hook?: string;
  /** Written by episode_planner at split time: slice boundary in the source file (char offsets) */
  source_range?: { source_file?: string; start?: number; end?: number };
  /** Written by episode_planner at split time (drama only) */
  outline?: { story_beats?: string[]; next_episode_teaser?: string };
  /** Injected by StatusCalculator at read time */
  scenes_count?: number;
  /** Injected by StatusCalculator at read time */
  script_status?: "none" | "segmented" | "generated";
  /** Injected by StatusCalculator at read time */
  status?: "draft" | "scripted" | "in_production" | "completed" | "missing";
  /** Injected by StatusCalculator at read time */
  duration_seconds?: number;
  /** Injected by StatusCalculator at read time */
  storyboards?: ProgressCategory;
  /** Injected by StatusCalculator at read time */
  videos?: ProgressCategory;
  /** Injected by StatusCalculator at read time (reference_video route only) */
  units_count?: number;
}

export interface ModelSettingEntry {
  resolution?: string | null;
}

export interface ProjectData {
  title: string;
  content_mode: "narration" | "drama" | "ad";
  /** 源文件性质：novel（默认，AI 改编）/ screenplay（成品剧本，逐字提取）。创建即定、不可变。 */
  source_kind?: "novel" | "screenplay";
  style: string;
  style_template_id?: string | null;
  style_image?: string;
  style_description?: string;
  overview?: ProjectOverview;
  aspect_ratio?: string | AspectRatio;  // 新项目为 string，旧项目可能为 dict
  default_duration?: number | null;     // 新增
  /** 仅 ad：目标总时长（秒）。 */
  target_duration?: number;
  /** 仅 ad：创作诉求短文本（可空）。 */
  brief?: string;
  schema_version?: number;
  episodes: EpisodeMeta[];
  characters: Record<string, Character>;
  scenes?: Record<string, Scene>;
  props?: Record<string, Prop>;
  /** 产品资产（广告/短片项目使用，v1 单产品设定，字段形态为映射）。 */
  products?: Record<string, Product>;
  /** Injected by StatusCalculator.enrich_project at read time */
  status?: ProjectStatus;
  video_backend?: string | null;
  /** 视频能力桶（docs/adr/0054）项目级覆盖；空值 = 回退 video_backend 与全局层 */
  video_provider_i2v?: string | null;
  video_provider_r2v?: string | null;
  image_backend?: string | null;
  /** 项目默认图片模型；图片能力桶留空时回退到它，再回退全局层 */
  default_image_backend?: string | null;
  image_provider_t2i?: string | null;
  image_provider_i2i?: string | null;
  /** 生成路线，创建时锁定、之后不可更改。 */
  generation_mode?: GenerationRoute;
  /** 分镜板（宫格）装配开关；仅分镜路线有意义，随时可切。 */
  grid_storyboard?: boolean;
  video_generate_audio?: boolean | null;
  /** 旁白配音（TTS）项目级覆盖：音频后端 / 音色 / 语速，留空即跟随全局默认 */
  audio_backend?: string | null;
  narration_voice?: string | null;
  narration_speed?: number | null;
  text_backend_simple?: string | null;
  text_backend_complex?: string | null;
  default_text_backend?: string | null;
  model_settings?: Record<string, ModelSettingEntry>;
  /** Legacy field: keyed by model_id only (before composite key refactor). Read-only at UI layer. */
  video_model_settings?: Record<string, { resolution?: string | null }>;
  metadata?: {
    created_at: string;
    updated_at: string;
  };
}

/**
 * Summary shape returned by GET /api/v1/projects (list endpoint).
 *
 * Note: `status` may be an empty object `{}` when the project
 * has no project.json or encounters an error during loading.
 */
export interface ProjectSummary {
  name: string;
  title: string;
  style: string;
  style_template_id?: string | null;
  style_image?: string | null;
  thumbnail: string | null;
  status: ProjectStatus | Record<string, never>;
}

export type ImportConflictPolicy = "prompt" | "rename" | "overwrite";

export interface ArchiveDiagnostic {
  code: string;
  message: string;
  location?: string;
}

export interface ImportSuccessDiagnostics {
  auto_fixed: ArchiveDiagnostic[];
  warnings: ArchiveDiagnostic[];
}

export interface ImportFailureDiagnostics {
  blocking: ArchiveDiagnostic[];
  auto_fixable: ArchiveDiagnostic[];
  warnings: ArchiveDiagnostic[];
}

export interface ExportDiagnostics {
  blocking: ArchiveDiagnostic[];
  auto_fixed: ArchiveDiagnostic[];
  warnings: ArchiveDiagnostic[];
}

export interface ImportProjectResponse {
  success: boolean;
  project_name: string;
  project: ProjectData;
  warnings: string[];
  conflict_resolution: "none" | "renamed" | "overwritten";
  diagnostics: ImportSuccessDiagnostics;
}

/**
 * 三级解析（项目 > 系统设置 > 系统默认）后的视频模型能力。
 * 后端：server/routers/projects.py 的 /projects/{name}/video-capabilities。
 */
export interface VideoCapabilities {
  provider_id: string;
  model: string;
  supported_durations: number[];
  max_duration: number;
  max_reference_images: number;
  /** 生效值（系统判定 ⊕ 用户覆盖），与执行层注入 backend 的能力同源。 */
  first_frame: boolean;
  last_frame: boolean;
  source: "registry" | "custom";
  default_duration?: number | null;
  content_mode?: string | null;
  generation_mode?: string | null;
  /** 声音一致性三级标识（模型能力 × generation_mode 二维派生），服务端唯一派生点。 */
  voice_consistency: VoiceConsistencyTier;
}
