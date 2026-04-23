/**
 * Project-related type definitions.
 *
 * Maps to backend models in:
 * - lib/project_manager.py (ProjectOverview, project.json structure)
 * - lib/status_calculator.py (ProjectStatus, EpisodeMeta computed fields)
 * - server/routers/projects.py (ProjectSummary list response)
 */

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
}

export interface Scene {
  description: string;
  scene_sheet?: string;
}

export interface Prop {
  description: string;
  prop_sheet?: string;
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

/** Injected by StatusCalculator.calculate_project_status at read time */
export interface ProjectStatus {
  current_phase: "setup" | "worldbuilding" | "scripting" | "production" | "completed";
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
  /** Injected by StatusCalculator at read time (reference_video mode only) */
  units_count?: number;
  /**
   * Optional episode-level override; falls back to project.generation_mode.
   * Never "single" — legacy value only exists at project level.
   */
  generation_mode?: "storyboard" | "grid" | "reference_video";
}

export interface ModelSettingEntry {
  resolution?: string | null;
}

export interface ProjectData {
  title: string;
  content_mode: "narration" | "drama";
  style: string;
  style_template_id?: string | null;
  style_image?: string;
  style_description?: string;
  overview?: ProjectOverview;
  aspect_ratio?: string | AspectRatio;  // 新项目为 string，旧项目可能为 dict
  default_duration?: number | null;     // 新增
  schema_version?: number;
  episodes: EpisodeMeta[];
  characters: Record<string, Character>;
  scenes?: Record<string, Scene>;
  props?: Record<string, Prop>;
  /** Injected by StatusCalculator.enrich_project at read time */
  status?: ProjectStatus;
  video_backend?: string | null;
  image_backend?: string | null;
  /** Canonical values: storyboard | grid | reference_video. "single" is legacy-only. */
  generation_mode?: "storyboard" | "grid" | "reference_video" | "single";
  video_generate_audio?: boolean | null;
  text_backend_script?: string | null;
  text_backend_overview?: string | null;
  text_backend_style?: string | null;
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
