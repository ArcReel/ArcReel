/**
 * Script / segment / scene type definitions.
 *
 * Maps to backend models in:
 * - lib/script/script_models.py (NarrationSegment, DramaScene, ImagePrompt, VideoPrompt, etc.)
 */

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

/**
 * Ordered storyboard-level speech entries for drama. A discriminated union narrows by kind and encodes the kind ⇄ speaker
 * constraint in the type: dialogue requires a nonempty speaker; voiceover must not carry one. Invalid combinations (dialogue without
 * speaker, voiceover with a speaker) are rejected at compile time, matching the backend Utterance contract.
 */
export interface DialogueUtterance {
  kind: "dialogue";
  /** Character dialogue requires a nonempty speaker. */
  speaker: string;
  text: string;
}

export interface VoiceoverUtterance {
  kind: "voiceover";
  /** Voiceover without a speaker: speaker is always null or absent. */
  speaker?: null;
  text: string;
}

export type Utterance = DialogueUtterance | VoiceoverUtterance;

/**
 * Structured script_plan intermediate state (reviewable/editable at content confirmation). Maps to backend lib/script/script_models.py:
 * DramaSceneContent / DramaNormalizedScript and NarrationScriptPlanSegment / NarrationScriptPlanDraft.
 * script_plan defines the content layer; prompt_authoring visual generation (image_prompt / video_prompt) starts only after user confirmation.
 */
export interface DramaSceneContent {
  scene_id: string;
  duration_seconds: number;
  segment_break: boolean;
  characters_in_scene: string[];
  scenes: string[];
  props: string[];
  /** Free-form visual adaptation text (for prompt_authoring to generate visuals, without embedded speech). */
  scene_description: string;
  /** Ordered storyboard-level speech sequence: dialogue / voiceover in temporal order (a rich editing object at content confirmation). */
  utterances: Utterance[];
  /** Verbatim source excerpt (traceability anchor, not read aloud or voiced). */
  source_text: string;
}

/** Types of new episode assets: only characters, scenes, and props can be added during planning. */
export type NewAssetType = "character" | "scene" | "prop";

/** Decision for a new item: register as a new asset / merge into an existing asset / register as a character derivative / skip registration. */
export type NewAssetDecision = "register" | "merge" | "derivative" | "skip";

/**
 * One new episode asset produced by script planning. Maps to PlanNewAsset in backend lib/script/script_models.py:
 * planned entries reference `name`; confirmation rewrites and registers it according to `decision`.
 */
export interface PlanNewAsset {
  type: NewAssetType;
  /** Name used in the planned entry. */
  name: string;
  decision: NewAssetDecision;
  /** One-sentence rationale from the AI. */
  reason: string;
  /** register: appearance description; derivative: description of changes relative to the owner. */
  description: string;
  /** register: other names in the source. */
  aliases: string[];
  /** merge: name of the same-type asset or another new episode item to merge into; derivative: owner character name. */
  target: string;
  /** register: registered name; derivative: derivative name; uses `name` when empty. */
  asset_name: string;
}

export interface DramaNormalizedScript {
  title: string;
  scenes: DramaSceneContent[];
  new_assets?: PlanNewAsset[];
}

export interface NarrationScriptPlanSegment {
  segment_id: string;
  /** Original novel text (preserved verbatim, editable at content confirmation). */
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

/** Formal script entries overwritten by content confirmation, including their artifacts that cannot be recovered within the project and their ownership. */
export interface ScriptOverwriteEntry {
  id: string;
  has_storyboard: boolean;
  has_video: boolean;
  has_narration_audio: boolean;
  has_end_frame: boolean;
  /** Owning grid (composite image) ID; null when not in a grid. */
  grid_id: string | null;
}

/** Formal script overwritten by content confirmation: all old storyboards are removed, listing unrecoverable artifacts for each. */
export interface ScriptOverwrite {
  /** Revision of the listed formal script; return unchanged to acknowledge overwriting. Subsequent formal script changes cause another refusal with a new list. */
  revision: string;
  entries: ScriptOverwriteEntry[];
  storyboard_count: number;
  video_count: number;
  narration_audio_count: number;
  end_frame_count: number;
  grid_member_count: number;
  grid_count: number;
  /** Server-generated loss list text (rendered in the request language); displayed unchanged in the confirmation dialog and sent identically to the Agent. */
  text: string;
}

/** Content confirmation state for script_plan→prompt_authoring (GET response from backend server/routers/script_review.py). */
export interface ScriptReviewState {
  episode: number;
  content_mode: string | null;
  status: ScriptReviewStatus;
  fingerprint: string | null;
  confirmed_at: string | null;
  content: DramaNormalizedScript | NarrationScriptPlanDraft | ReferenceScriptPlanDraft | null;
  /** Non-null when a draft exists (possible for all three script_plan routes), null otherwise. */
  quarantine: ScriptReviewQuarantine | null;
  /** Per-unit degradation notices for the formal reference plan; informational, not blocking. */
  soft_violations?: DraftSoftViolation[];
  /**
   * Selectable unit duration tiers, non-null only for reference_video (also
   * null when no configured project video model can be resolved, falling back to read-only seconds). Uses the same tier table as backend read-time migration: the full structural interval set, excluding
   * coupled resolution / reference-image constraints.
   */
  supported_durations: number[] | null;
  /**
   * Effective per-unit tiers narrowed by reference-image presence, using the same criteria as prompt_authoring validation before persistence; null when the model
   * cannot be resolved.
   */
  duration_tiers: {
    with_references: number[];
    /** Server bucket results per unit (from usable reference images), used by the panel for tier selection and overrun checks instead of inferring from registered references. */
    units: ReferenceUnitCapabilityMap;
  } | null;
  /**
   * Project-level target episode duration preference (seconds), null when unset. The review panel uses it to compare episode total / target;
   * exceeding the target produces a notice without blocking confirmation or subsequent generation.
   */
  episode_target_duration: number | null;
  /** Formal script to overwrite on confirmation; null when none exists for the episode. When non-null, confirmation must include its `revision` to acknowledge overwriting. */
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
  storyboard_last_image: string | null;  // grid mode last frame
  grid_id: string | null;                // source grid ID
  grid_cell_index: number | null;        // cell index in source grid
  video_clip: string | null;
  video_thumbnail: string | null;
  video_uri: string | null;
  narration_audio?: string | null;       // narration audio file path
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
  /** `null` = pending authoring: the formal script produced by content confirmation has only content, with prompts not yet authored. */
  image_prompt: ImagePrompt | string | null;
  video_prompt: VideoPrompt | string | null;
  note?: string;
  /**
   * Last-frame snapshot path (project-relative). The video starts from the storyboard image and transitions to this image at its end.
   * Written only by the /end-frame set/clear endpoints; generic script PATCH deliberately rejects this field
   * to prevent dangling references that bypass snapshot copying (see server/routers/end_frames.py).
   */
  end_frame_image?: string | null;
  generated_assets?: GeneratedAssets;
  /** Pending authoring: prompt authoring has not filled in the visual layer. Set on new entries and cleared after prompt authoring writes back; read-only. */
  pending_authoring?: boolean;
}

export interface DramaScene {
  scene_id: string;
  duration_seconds: DurationSeconds;
  segment_break: boolean;
  characters_in_scene: string[];
  scenes?: string[];
  props?: string[];
  /** `null` = pending authoring: the formal script produced by content confirmation has only content, with prompts not yet authored. */
  image_prompt: ImagePrompt | string | null;
  video_prompt: VideoPrompt | string | null;
  /**
   * Ordered storyboard-level speech: character dialogue and voiceover in temporal order. New structure (drama);
   * legacy drama uses backend read-time migration, so this field may be absent when read by the frontend.
   */
  utterances?: Utterance[];
  /** Visual description: forwarded from script planning at content confirmation; empty or absent for manually added storyboards. */
  scene_description?: string;
  /** Corresponding source text: forwarded from script planning at content confirmation and read-only in the timeline; empty or absent for manually added storyboards. */
  source_text?: string;
  note?: string;
  /**
   * Last-frame snapshot path (project-relative). The video starts from the storyboard image and transitions to this image at its end.
   * Written only by the /end-frame set/clear endpoints; generic script PATCH deliberately rejects this field
   * to prevent dangling references that bypass snapshot copying (see server/routers/end_frames.py).
   */
  end_frame_image?: string | null;
  generated_assets?: GeneratedAssets;
  /** Pending authoring: prompt authoring has not filled in the visual layer. Set on new entries and cleared after prompt authoring writes back; read-only. */
  pending_authoring?: boolean;
}

/** Novel source information (present in both episode script types). */
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

/** Eight suggested section values for the sales framework (matching the backend-approved ratio table; not a strict enum, allowing custom values). */
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

/** Ad/short-film storyboard (flat shots[], with spoken copy as a first-class field). */
export interface AdShot {
  shot_id: string;
  /** Sales framework section label (eight suggestions such as hook/pain_point/..., not a strict enum). */
  section: string;
  duration_seconds: DurationSeconds;
  /** Spoken copy, the sole source for subtitle export and subsequent dubbing. */
  voiceover_text: string;
  characters_in_shot?: string[];
  scenes?: string[];
  props?: string[];
  /** Product name references; nonempty means a product storyboard. */
  products_in_shot?: string[];
  /** Null for a storyboard pending authoring (manually added), to be filled by prompt authoring. */
  image_prompt: ImagePrompt | string | null;
  video_prompt: VideoPrompt | string | null;
  note?: string;
  /**
   * Last-frame snapshot path (project-relative). The video starts from the storyboard image and transitions to this image at its end.
   * Written only by the /end-frame set/clear endpoints; generic script PATCH deliberately rejects this field
   * to prevent dangling references that bypass snapshot copying (see server/routers/end_frames.py).
   */
  end_frame_image?: string | null;
  generated_assets?: GeneratedAssets;
  /** Pending authoring: prompt authoring has not filled in the visual layer. Set on new entries and cleared after prompt authoring writes back; read-only. */
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

/** Prompt authoring request: scope (all pending entries when omitted), explicit rewrite flag, additional instructions, and overwrite token. */
export interface AuthorPromptsRequest {
  entry_ids?: string[] | null;
  rewrite?: boolean;
  instructions?: string | null;
  overwrite_revision?: string | null;
}

/** Existing visual content overwritten by an explicit rewrite; `text` is the server-rendered loss list, and `revision` is the acknowledgment token. */
export interface PromptOverwrite {
  revision: string | null;
  entries: { id: string; fields: string[] }[];
  text: string;
}

export interface AuthorPromptsResponse {
  batch: { batch_id: string; members: { unit_id: string; task_id: string | null; deduped?: boolean }[] };
}

/** AI script planning request: additional instructions (saved per episode with the request). */
export interface PlanScriptRequest {
  instructions?: string | null;
}

/** Generation batch returned immediately after submission, with the same shape as prompt authoring. */
export type PlanScriptResponse = AuthorPromptsResponse;

/** Ad/short-film AI script generation request: additional instructions apply only to this submission; a full regeneration includes the overwrite acknowledgment token. */
export interface GenerateAdScriptRequest {
  instructions?: string | null;
  regenerate?: boolean;
  overwrite_revision?: string | null;
}

/** Successful full ad/short-film generation result: newly registered assets (derivative names use owner/derivative). */
export interface AdScriptTaskResult {
  message?: string;
  new_assets?: { type: "character" | "scene" | "prop"; name: string }[];
}

/** AI repair of a draft awaiting repair: generation batch returned immediately after submission, with the same shape as prompt authoring. */
export type DraftRepairResponse = AuthorPromptsResponse;

/** Successful AI repair result: adopted when no violations remain; otherwise gives the remaining draft violation count. */
export interface DraftRepairTaskResult {
  adopted: boolean;
  violation_count: number;
}

/**
 * Final rendering of one prompt side. Exactly one of `text` and `unavailable` is non-null;
 * `unavailable` is already rendered by the backend in the request language and is not translated again by the frontend.
 */
export interface RenderedPromptPreview {
  text: string | null;
  unavailable: string | null;
  /** This entry's prompt on this side is currently in text form. */
  is_text_form: boolean;
  /** Notices produced while rendering this text (such as reference images trimmed to the backend limit), already rendered by the backend in the request language. */
  warnings: string[];
}

/** Final prompt preview for an entry: uses the same rendering exit as execution and exactly matches the text sent to the model. */
export interface ItemPromptPreview {
  item_id: string;
  content_mode: "narration" | "drama" | "ad";
  storyboard_image: RenderedPromptPreview;
  video: RenderedPromptPreview;
}
