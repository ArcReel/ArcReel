/**
 * Project-related type definitions.
 *
 * Maps to backend models in:
 * - lib/project_manager.py (ProjectOverview, project.json structure)
 * - lib/status_calculator.py (ProjectProgress, EpisodeMeta computed fields)
 * - webui/server/routers/projects.py (ProjectSummary list response)
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

export interface Clue {
  type: "prop" | "location";
  description: string;
  importance: "major" | "minor";
  clue_sheet?: string;
}

export interface AspectRatio {
  characters?: string;
  clues?: string;
  storyboard?: string;
  video?: string;
}

export interface ProgressCategory {
  total: number;
  completed: number;
}

export interface ProjectProgress {
  characters: ProgressCategory;
  clues: ProgressCategory;
  storyboards: ProgressCategory;
  videos: ProgressCategory;
}

export interface EpisodeMeta {
  episode: number;
  title: string;
  script_file: string;
  /** Injected by StatusCalculator at read time */
  scenes_count?: number;
  /** Injected by StatusCalculator at read time */
  status?: "draft" | "in_production" | "completed" | "missing";
  /** Injected by StatusCalculator at read time */
  duration_seconds?: number;
  /** Injected by StatusCalculator at read time */
  storyboards_completed?: number;
  /** Injected by StatusCalculator at read time */
  videos_completed?: number;
}

export interface ProjectData {
  title: string;
  content_mode: "narration" | "drama";
  style: string;
  style_image?: string;
  style_description?: string;
  overview?: ProjectOverview;
  aspect_ratio?: AspectRatio;
  episodes: EpisodeMeta[];
  characters: Record<string, Character>;
  clues: Record<string, Clue>;
  /** Injected by StatusCalculator.enrich_project at read time */
  status?: {
    progress: ProjectProgress;
    current_phase: string;
  };
  metadata?: {
    created_at: string;
    updated_at: string;
  };
}

/**
 * Summary shape returned by GET /api/v1/projects (list endpoint).
 *
 * Note: `progress` may be an empty object `{}` when the project
 * has no project.json or encounters an error during loading.
 */
export interface ProjectSummary {
  name: string;
  title: string;
  style: string;
  thumbnail: string | null;
  progress: ProjectProgress | Record<string, never>;
  current_phase: string;
}
