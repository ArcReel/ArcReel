/**
 * Script / segment / scene type definitions.
 *
 * Maps to backend models in:
 * - lib/script_models.py (NarrationSegment, DramaScene, ImagePrompt, VideoPrompt, etc.)
 */

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
  "Tracking Shot",
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
  "Tracking Shot": "camera_motion_tracking_shot",
};

export type TransitionType = "cut" | "fade" | "dissolve";
export type DurationSeconds = number;
export type AssetStatus = "pending" | "storyboard_ready" | "completed";

export interface Dialogue {
  speaker: string;
  line: string;
}

export type UtteranceKind = "dialogue" | "voiceover";

/**
 * Drama 场景级有序发声条目，判别式联合（discriminated union）按 kind 收窄，把 kind ⇄ speaker
 * 约束编码进类型：dialogue 必带非空 speaker、voiceover 不得带 speaker。非法组合（dialogue 缺
 * speaker、voiceover 带 speaker）编译期即被拒，与运行时 isUtterance 守卫及后端 Utterance 契约一致。
 * 取代旧 video_prompt.dialogue + 场景 voiceover 双字段（见 ADR 0040）。
 * 富审阅 / 编辑 UI 后续提供；本阶段仅类型 / 形状守卫。
 */
export interface DialogueUtterance {
  kind: "dialogue";
  /** 角色台词必带非空说话人。 */
  speaker: string;
  text: string;
}

export interface VoiceoverUtterance {
  kind: "voiceover";
  /** 无说话人画外音：speaker 恒为 null 或缺省。 */
  speaker?: null;
  text: string;
}

export type Utterance = DialogueUtterance | VoiceoverUtterance;

/**
 * step1 结构化中间态（审核 gate 的可审 / 可改对象）。映射后端 lib/script_models.py 的
 * DramaSceneContent / DramaNormalizedScript 与 NarrationStep1Segment / NarrationStep1Draft：
 * step1 已定内容层，step2 视觉生成（image_prompt / video_prompt）由用户确认后才触发。
 */
export interface DramaSceneContent {
  scene_id: string;
  duration_seconds: number;
  segment_break: boolean;
  characters_in_scene: string[];
  scenes: string[];
  props: string[];
  /** 视觉改编自由文本（供 step2 生成画面，不内嵌口播）。 */
  scene_description: string;
  /** 场景级有序发声序列：台词 / 画外音按时序排列（审核 gate 的富编辑对象）。 */
  utterances: Utterance[];
  /** 逐字原文摘录（追溯锚，不朗读、不出音）。 */
  source_text: string;
}

export interface DramaNormalizedScript {
  title: string;
  scenes: DramaSceneContent[];
}

export interface NarrationStep1Segment {
  segment_id: string;
  /** 小说原文（逐字保留，审核 gate 的可编辑对象）。 */
  novel_text: string;
  duration_seconds: number;
  segment_break: boolean;
  characters_in_segment: string[];
  scenes: string[];
  props: string[];
}

export interface NarrationStep1Draft {
  segments: NarrationStep1Segment[];
  episode?: number;
}

export type ScriptReviewStatus =
  | "not_applicable"
  | "no_step1"
  | "pending_review"
  | "confirmed";

/** step1→step2 审核 gate 状态（后端 server/services/script_review.py 的 get_state 响应）。 */
export interface ScriptReviewState {
  episode: number;
  content_mode: string | null;
  status: ScriptReviewStatus;
  fingerprint: string | null;
  confirmed_at: string | null;
  content: DramaNormalizedScript | NarrationStep1Draft | null;
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
  image_prompt: ImagePrompt | string;
  video_prompt: VideoPrompt | string;
  transition_to_next: TransitionType;
  note?: string;
  generated_assets?: GeneratedAssets;
}

export interface DramaScene {
  scene_id: string;
  duration_seconds: DurationSeconds;
  segment_break: boolean;
  characters_in_scene: string[];
  scenes?: string[];
  props?: string[];
  image_prompt: ImagePrompt | string;
  video_prompt: VideoPrompt | string;
  /**
   * 场景级有序发声序列：角色台词与画外音按时序排列。新结构（drama）；
   * 存量 drama 走后端读时迁移，前端读到时此字段可能缺省。
   */
  utterances?: Utterance[];
  transition_to_next: TransitionType;
  note?: string;
  generated_assets?: GeneratedAssets;
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
  duration_seconds: number;
  schema_version?: number;
  novel: NovelInfo;
  segments: NarrationSegment[];
}

export interface DramaEpisodeScript {
  episode: number;
  title: string;
  content_mode: "drama";
  duration_seconds: number;
  schema_version?: number;
  novel: NovelInfo;
  scenes: DramaScene[];
}

/**
 * 参考生视频路径下单镜头时长可选值（1-15 秒自由整数）。
 * 与后端 lib/script_models.py 的 REFERENCE_SHOT_DURATION_RANGE 同源，调整区间时两侧同步。
 */
export const REFERENCE_SHOT_DURATION_OPTIONS: number[] = Array.from(
  { length: 15 },
  (_, i) => i + 1,
);

/** 带货框架 section 八值引导（与后端审定配比表用词一致；不硬枚举，允许自定义值）。 */
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

/** 广告/短片模式镜头（平铺 shots[]，口播文案一等）。 */
export interface AdShot {
  shot_id: string;
  /** 带货框架段落标签（hook/pain_point/... 八值引导，不硬枚举）。 */
  section: string;
  duration_seconds: DurationSeconds;
  /** 口播文案，字幕导出与后续配音的唯一来源。 */
  voiceover_text: string;
  characters_in_shot?: string[];
  scenes?: string[];
  props?: string[];
  /** 产品名称引用，非空即产品镜头。 */
  products_in_shot?: string[];
  image_prompt: ImagePrompt | string;
  video_prompt: VideoPrompt | string;
  transition_to_next: TransitionType;
  note?: string;
  generated_assets?: GeneratedAssets;
}

export interface AdEpisodeScript {
  episode: number;
  title: string;
  content_mode: "ad";
  duration_seconds: number;
  schema_version?: number;
  novel: NovelInfo;
  shots: AdShot[];
}

export type EpisodeScript = NarrationEpisodeScript | DramaEpisodeScript | AdEpisodeScript;
