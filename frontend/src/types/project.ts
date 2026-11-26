
import type { VoiceConsistencyTier } from "@/types/provider";
import type { NarrationDelivery } from "@/types/workflow";
import type { GenerationRoute } from "@/utils/generation-mode";

export interface ProjectOverview {
  synopsis: string;
  genre: string;
  theme: string;
  world_setting: string;
  generated_at?: string;
}

export interface CharacterDerivative {
  description: string;
  character_sheet?: string;
  referenced?: boolean | null;
}

export interface CharacterDerivativeStatus extends CharacterDerivative {
  stale: boolean;
  artifact_status?: "current" | "stale" | "missing" | "blocked";
}

export interface Character {
  description: string;
  character_sheet?: string;
  voice_style?: string;
  reference_image?: string;
  reference_audio?: string;
  voice_updated_at?: string;
  voice_notice_dismissed_at?: string;
  derivatives?: Record<string, CharacterDerivative>;
  aliases?: string[];
}

export interface Scene {
  description: string;
  scene_sheet?: string;
  aliases?: string[];
}

export interface Prop {
  description: string;
  prop_sheet?: string;
  aliases?: string[];
}

export interface Product {
  description: string;
  product_sheet?: string;
  brand?: string;
  reference_images?: string[];
  selling_points?: string[];
}

export interface AspectRatio {
  characters?: string;
  scenes?: string;
  props?: string;
  storyboard?: string;
  video?: string;
}

export interface EpisodesSummary {
  total: number;
  scripted: number;
  in_production: number;
  completed: number;
}

export interface ArtifactCount {
  total: number;
  available: number;
  stale?: number;
}

export interface ProjectStatus {
  currency?: "registered" | "verified";
  needs_repair: boolean;
  repair_reason: string | null;
  assets: Record<string, ArtifactCount>;
  episodes_summary: EpisodesSummary;
  source_remaining: boolean;
}

export interface WorkspaceEpisodeSummary {
  episode: number;
  script_status: "none" | "segmented" | "generated";
  status: "draft" | "scripted" | "in_production" | "completed" | "missing";
  item_count: number;
  duration_seconds: number;
  needs_replan: number;
  videos: ArtifactCount;
  storyboards: ArtifactCount;
}

export interface WorkspaceSummary extends Omit<ProjectStatus, "episodes_summary"> {
  episodes_summary: EpisodesSummary | null;
  currency: "registered";
  episodes: WorkspaceEpisodeSummary[];
}

export interface WorkspaceResponse {
  project: ProjectData;
  summary: WorkspaceSummary;
  asset_fingerprints?: Record<string, number>;
}

export type ItemLocationStatus = "found" | "deleted" | "ambiguous" | "unloaded";

export interface ItemLocation {
  episode: number | null;
  item_id: string;
  position: number | null;
  status: ItemLocationStatus;
}

export interface ItemLocationsResponse {
  items: ItemLocation[];
}

export interface EpisodeItemRef {
  episode_title: string;
  episode_position: number;
  item_id: string;
  item_position: number | null;
  item_excerpt: string;
  item_thumbnail: string | null;
}

export interface EpisodeMeta {
  episode: number;
  title: string;
  script_file: string;
  hook?: string;
  source_range?: { source_file?: string; start?: number; end?: number; end_file?: string };
  source_origin?: "whole_source" | "own" | "none";
  source_kind?: "novel" | "screenplay";
  ledger_status?: "planned" | "consumed" | "stale";
  stale_script_plan_revision?: string | null;
  stale_script_plan_rebuilt_revision?: string | null;
  outline?: { story_beats?: string[]; next_episode_teaser?: string };
  prompt_authoring_instructions?: string;
  script_plan_instructions?: string;
  item_count?: number;
  script_status?: "none" | "segmented" | "generated";
  status?: "draft" | "scripted" | "in_production" | "completed" | "missing";
  duration_seconds?: number;
  needs_replan?: number;
  storyboards?: ArtifactCount;
  videos?: ArtifactCount;
}

export type CharacterVoiceBinding = "prompt" | "reference_audio";

export const DEFAULT_CHARACTER_VOICE_BINDING: CharacterVoiceBinding = "prompt";

export interface ModelSettingEntry {
  resolution?: string | null;
  quality?: string | null;
}

export interface ProjectData {
  title: string;
  content_mode: "narration" | "drama" | "ad";
  style: string;
  style_template_id?: string | null;
  style_image?: string;
  style_description?: string;
  overview?: ProjectOverview;
  aspect_ratio?: string | AspectRatio;
  default_duration?: number | null;
  episode_target_duration?: number | null;
  target_duration?: number;
  brief?: string;
  schema_version?: number;
  whole_source_files?: { source_file: string; source_kind?: "novel" | "screenplay" }[];
  episodes: EpisodeMeta[];
  characters: Record<string, Character>;
  scenes?: Record<string, Scene>;
  props?: Record<string, Prop>;
  products?: Record<string, Product>;
  status?: ProjectStatus;
  video_backend?: string | null;
  video_provider_i2v?: string | null;
  video_provider_r2v?: string | null;
  image_backend?: string | null;
  default_image_backend?: string | null;
  image_provider_t2i?: string | null;
  image_provider_i2i?: string | null;
  generation_mode?: GenerationRoute;
  grid_storyboard?: boolean;
  video_generate_audio?: boolean | null;
  character_voice_binding?: CharacterVoiceBinding;
  narration_delivery?: NarrationDelivery;
  audio_backend?: string | null;
  narration_voice?: string | null;
  narration_speed?: number | null;
  source_language?: string | null;
  speech_rate_units_per_second?: number | null;
  text_backend_simple?: string | null;
  text_backend_complex?: string | null;
  default_text_backend?: string | null;
  model_settings?: Record<string, ModelSettingEntry>;
  video_model_settings?: Record<string, { resolution?: string | null }>;
  metadata?: {
    created_at: string;
    updated_at: string;
  };
}

export interface ProjectSummary {
  name: string;
  title: string;
  style: string;
  style_template_id?: string | null;
  style_image?: string | null;
  thumbnail: string | null;
  status: ProjectStatus | Record<string, never>;
  last_activity_at: string | null;
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

export type DurationExclusionReason = "resolution" | "reference" | "end_frame";

export interface DurationConstraints {
  resolution: string | null;
  uses_reference_images: boolean;
  allowed: number[];
  planning?: number[];
  excluded: Record<string, DurationExclusionReason>;
}

export interface VideoCapabilityProblem {
  code: string;
  params: Record<string, unknown>;
  action: string;
}

export interface VideoCapabilities {
  provider_id: string;
  model: string;
  supported_durations: number[];
  max_duration: number;
  max_reference_images: number | null;
  first_frame: boolean;
  last_frame: boolean;
  source: "registry" | "custom";
  default_duration?: number | null;
  content_mode?: string | null;
  generation_mode?: string | null;
  voice_consistency: VoiceConsistencyTier;
  duration_constraints: DurationConstraints;
  end_frame_unit_durations?: {
    without_end_frame: number[];
    with_end_frame: number[];
    excluded: Record<string, DurationExclusionReason>;
    problem: VideoCapabilityProblem | null;
  };
  duration_endpoint_fixed?: boolean;
  duration_endpoint_fixed_reason?: "endpoint" | null;
}
