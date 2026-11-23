
import type { DraftSoftViolation, ReferenceScriptPlanDraft, ReferenceUnitCapabilityMap, ScriptReviewQuarantine } from "./reference-video";

export const SHOT_TYPES = [
  "Extreme Close-up",
  "Close-up",
  "Medium Close-up",
  "Medium Shot",
  "Medium Long Shot",
  "Long Shot",
  "Extreme Long Shot",
  "Over-the-shoulder",
  "Point-of-view",
] as const;

export type ShotType = (typeof SHOT_TYPES)[number];

export const SHOT_TYPE_I18N_KEYS: Record<ShotType, string> = {
  "Extreme Close-up": "shot_type_extreme_close_up",
  "Close-up": "shot_type_close_up",
  "Medium Close-up": "shot_type_medium_close_up",
  "Medium Shot": "shot_type_medium_shot",
  "Medium Long Shot": "shot_type_medium_long_shot",
  "Long Shot": "shot_type_long_shot",
  "Extreme Long Shot": "shot_type_extreme_long_shot",
  "Over-the-shoulder": "shot_type_over_the_shoulder",
  "Point-of-view": "shot_type_point_of_view",
};

export const CAMERA_MOTIONS = [
  "Static",
  "Pan Left",
  "Pan Right",
  "Tilt Up",
  "Tilt Down",
  "Zoom In",
  "Zoom Out",
  "Push In",
  "Pull Out",
  "Truck Left",
  "Truck Right",
  "Pedestal Up",
  "Pedestal Down",
  "Orbit",
  "Tracking Shot",
  "Shake",
] as const;

export type CameraMotion = (typeof CAMERA_MOTIONS)[number];

export const CAMERA_MOTION_I18N_KEYS: Record<CameraMotion, string> = {
  Static: "camera_motion_static",
  "Pan Left": "camera_motion_pan_left",
  "Pan Right": "camera_motion_pan_right",
  "Tilt Up": "camera_motion_tilt_up",
  "Tilt Down": "camera_motion_tilt_down",
  "Zoom In": "camera_motion_zoom_in",
  "Zoom Out": "camera_motion_zoom_out",
  "Push In": "camera_motion_push_in",
  "Pull Out": "camera_motion_pull_out",
  "Truck Left": "camera_motion_truck_left",
  "Truck Right": "camera_motion_truck_right",
  "Pedestal Up": "camera_motion_pedestal_up",
  "Pedestal Down": "camera_motion_pedestal_down",
  Orbit: "camera_motion_orbit",
  "Tracking Shot": "camera_motion_tracking_shot",
  Shake: "camera_motion_shake",
};

export type DurationSeconds = number;
export type AssetStatus = "pending" | "storyboard_ready" | "completed";

export interface Dialogue {
  speaker: string;
  line: string;
}

export type UtteranceKind = "dialogue" | "voiceover";

export interface DialogueUtterance {
  kind: "dialogue";
  speaker: string;
  text: string;
}

export interface VoiceoverUtterance {
  kind: "voiceover";
  speaker?: null;
  text: string;
}

export type Utterance = DialogueUtterance | VoiceoverUtterance;

export interface DramaSceneContent {
  scene_id: string;
  duration_seconds: number;
  segment_break: boolean;
  characters_in_scene: string[];
  scenes: string[];
  props: string[];
  scene_description: string;
  utterances: Utterance[];
  source_text: string;
}

export type NewAssetType = "character" | "scene" | "prop";

export type NewAssetDecision = "register" | "merge" | "derivative" | "skip";

export interface PlanNewAsset {
  type: NewAssetType;
  name: string;
  decision: NewAssetDecision;
  reason: string;
  description: string;
  aliases: string[];
  target: string;
  asset_name: string;
}

export interface DramaNormalizedScript {
  title: string;
  scenes: DramaSceneContent[];
  new_assets?: PlanNewAsset[];
}

export interface NarrationScriptPlanSegment {
  segment_id: string;
  novel_text: string;
  duration_seconds: number;
  segment_break: boolean;
  characters_in_segment: string[];
  scenes: string[];
  props: string[];
}

export interface NarrationScriptPlanDraft {
  segments: NarrationScriptPlanSegment[];
  episode?: number;
  new_assets?: PlanNewAsset[];
}

export type ScriptReviewStatus =
  | "not_applicable"
  | "no_script_plan"
  | "pending_review"
  | "confirmed";

export interface ScriptOverwriteEntry {
  id: string;
  has_storyboard: boolean;
  has_video: boolean;
  has_narration_audio: boolean;
  has_end_frame: boolean;
  grid_id: string | null;
}

export interface ScriptOverwrite {
  revision: string;
  entries: ScriptOverwriteEntry[];
  storyboard_count: number;
  video_count: number;
  narration_audio_count: number;
  end_frame_count: number;
  grid_member_count: number;
  grid_count: number;
  text: string;
}

export interface ScriptReviewState {
  episode: number;
  content_mode: string | null;
  status: ScriptReviewStatus;
  fingerprint: string | null;
  confirmed_at: string | null;
  content: DramaNormalizedScript | NarrationScriptPlanDraft | ReferenceScriptPlanDraft | null;
  quarantine: ScriptReviewQuarantine | null;
  soft_violations?: DraftSoftViolation[];
  supported_durations: number[] | null;
  duration_tiers: {
    with_references: number[];
    units: ReferenceUnitCapabilityMap;
  } | null;
  episode_target_duration: number | null;
  script_overwrite: ScriptOverwrite | null;
}

export interface Composition {
  shot_type: ShotType;
  lighting: string;
  ambiance: string;
}

export interface ImagePrompt {
  scene: string;
  composition: Composition;
}

export interface VideoPrompt {
  action: string;
  camera_motion: CameraMotion;
  ambiance_audio: string;
  dialogue: Dialogue[];
}

export interface GeneratedAssets {
  storyboard_image: string | null;
  storyboard_last_image: string | null;
  grid_id: string | null;
  grid_cell_index: number | null;
  video_clip: string | null;
  video_thumbnail: string | null;
  video_uri: string | null;
  narration_audio?: string | null;
  status: AssetStatus;
}

export interface NarrationSegment {
  segment_id: string;
  episode: number;
  duration_seconds: DurationSeconds;
  segment_break: boolean;
  novel_text: string;
  characters_in_segment: string[];
  scenes?: string[];
  props?: string[];
  image_prompt: ImagePrompt | string | null;
  video_prompt: VideoPrompt | string | null;
  note?: string;
  end_frame_image?: string | null;
  generated_assets?: GeneratedAssets;
  pending_authoring?: boolean;
}

export interface DramaScene {
  scene_id: string;
  duration_seconds: DurationSeconds;
  segment_break: boolean;
  characters_in_scene: string[];
  scenes?: string[];
  props?: string[];
  image_prompt: ImagePrompt | string | null;
  video_prompt: VideoPrompt | string | null;
  utterances?: Utterance[];
  scene_description?: string;
  source_text?: string;
  note?: string;
  end_frame_image?: string | null;
  generated_assets?: GeneratedAssets;
  pending_authoring?: boolean;
}

export interface NovelInfo {
  title: string;
  chapter: string;
}

export interface NarrationEpisodeScript {
  episode: number;
  title: string;
  content_mode: "narration";
  schema_version?: number;
  novel: NovelInfo;
  segments: NarrationSegment[];
}

export interface DramaEpisodeScript {
  episode: number;
  title: string;
  content_mode: "drama";
  schema_version?: number;
  novel: NovelInfo;
  scenes: DramaScene[];
}

export const AD_SECTION_VALUES = [
  "hook",
  "pain_point",
  "product_reveal",
  "selling_point",
  "demo",
  "trust",
  "price_promo",
  "cta",
] as const;

export interface AdShot {
  shot_id: string;
  section: string;
  duration_seconds: DurationSeconds;
  voiceover_text: string;
  characters_in_shot?: string[];
  scenes?: string[];
  props?: string[];
  products_in_shot?: string[];
  image_prompt: ImagePrompt | string | null;
  video_prompt: VideoPrompt | string | null;
  note?: string;
  end_frame_image?: string | null;
  generated_assets?: GeneratedAssets;
  pending_authoring?: boolean;
}

export interface AdEpisodeScript {
  episode: number;
  title: string;
  content_mode: "ad";
  schema_version?: number;
  novel: NovelInfo;
  shots: AdShot[];
}

export type EpisodeScript = NarrationEpisodeScript | DramaEpisodeScript | AdEpisodeScript;

export interface AuthorPromptsRequest {
  entry_ids?: string[] | null;
  rewrite?: boolean;
  instructions?: string | null;
  overwrite_revision?: string | null;
}

export interface PromptOverwrite {
  revision: string | null;
  entries: { id: string; fields: string[] }[];
  text: string;
}

export interface AuthorPromptsResponse {
  batch: { batch_id: string; members: { unit_id: string; task_id: string | null; deduped?: boolean }[] };
}

export interface PlanScriptRequest {
  instructions?: string | null;
}

export type PlanScriptResponse = AuthorPromptsResponse;

export interface GenerateAdScriptRequest {
  instructions?: string | null;
  regenerate?: boolean;
  overwrite_revision?: string | null;
}

export interface AdScriptTaskResult {
  message?: string;
  new_assets?: { type: "character" | "scene" | "prop"; name: string }[];
}

export type DraftRepairResponse = AuthorPromptsResponse;

export interface DraftRepairTaskResult {
  adopted: boolean;
  violation_count: number;
}

export interface RenderedPromptPreview {
  unavailable_code?: string;
  reference_count?: number;
  max_reference_images?: number | null;
  previous_storyboard?: string | null;
  text: string | null;
  unavailable: string | null;
  is_text_form: boolean;
  warnings: string[];
}

export interface ItemPromptPreview {
  item_id: string;
  content_mode: "narration" | "drama" | "ad";
  storyboard_image: RenderedPromptPreview;
  video: RenderedPromptPreview;
}
