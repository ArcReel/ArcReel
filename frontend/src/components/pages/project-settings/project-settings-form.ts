import type { ProjectSettingsTab } from "@/app-routes";
import { executingImageModel, executingVideoModel } from "@/components/shared/LayeredModelFields";
import type { ModelConfigValue } from "@/components/shared/ModelConfigSection";
import type { NarrationDeliveryValue } from "@/components/shared/NarrationDeliveryFields";
import type { CharacterVoiceBinding, ModelSettingEntry, NarrationDelivery, ProjectData } from "@/types";
import { DEFAULT_CHARACTER_VOICE_BINDING } from "@/types";
import { normalizeRoute, type GenerationRoute } from "@/utils/generation-mode";

/** 全局各层的已配置值，原样带入、不折叠回退，穿透演算由模型选择字段族按解析链推导。 */
export interface GlobalModelDefaults {
  video: string;
  videoI2V: string;
  videoR2V: string;
  image: string;
  imageT2I: string;
  imageI2I: string;
  textDefault: string;
  textSimple: string;
  textComplex: string;
}

/** 项目的风格：模版与参考图二选一，也可以都不设。 */
export type ProjectStyle =
  | { kind: "none" }
  | { kind: "template"; templateId: string }
  | {
      kind: "image";
      /** 已保存的参考图地址，或待上传文件的 blob: 地址。 */
      preview: string;
      /** 服务端分析出的风格描述；待上传时为空。 */
      description: string;
      /** 待上传的新参考图；null 表示参考图已保存。 */
      file: File | null;
    };

/** 项目设置四个表单分页（基础、风格、模型、配音）共用的一个编辑单元。 */
export interface ProjectSettingsForm {
  aspectRatio: string;
  gridStoryboard: boolean;
  /** 口播语速估算（阅读单位 / 秒）；null 按项目语言的默认速度估算。 */
  speechRate: number | null;
  episodeTargetDuration: number | null;
  /** 广告项目的目标总时长（秒）；自定义输入不是正整数时为 null，拦住保存。 */
  adTargetDuration: number | null;
  style: ProjectStyle;
  /** 模型通道；`videoResolution` 按执行模型从 `videoResolutions` 现取，不单独保存。 */
  models: Omit<ModelConfigValue, "videoResolution"> & {
    /** 「生成有声视频」：null 跟随全局。 */
    generateAudio: boolean | null;
  };
  narration: NarrationDeliveryValue;
  voiceBinding: CharacterVoiceBinding;
  /** 项目已保存的 `model_settings` 全表，保存分辨率时在它上面合并。 */
  modelSettings: Record<string, { resolution: string | null }>;
}

/** 属于各表单分页的字段，用于在侧栏标出有修改的分页。 */
export const FORM_TAB_FIELDS = {
  basics: ["aspectRatio", "gridStoryboard", "speechRate", "episodeTargetDuration", "adTargetDuration"],
  style: ["style"],
  models: ["models"],
  voice: ["narration", "voiceBinding"],
} as const satisfies Partial<Record<ProjectSettingsTab, readonly (keyof ProjectSettingsForm)[]>>;

export type FormTab = keyof typeof FORM_TAB_FIELDS;

export function isFormTab(tab: ProjectSettingsTab): tab is FormTab {
  return tab in FORM_TAB_FIELDS;
}

function sameStyle(a: ProjectStyle, b: ProjectStyle): boolean {
  if (a.kind === "image" && b.kind === "image") {
    // File 序列化为空对象，按引用比较
    return a.preview === b.preview && a.file === b.file && a.description === b.description;
  }
  return JSON.stringify(a) === JSON.stringify(b);
}

function sameFields(a: ProjectSettingsForm, b: ProjectSettingsForm, fields: readonly (keyof ProjectSettingsForm)[]) {
  return fields.every((field) =>
    field === "style" ? sameStyle(a.style, b.style) : JSON.stringify(a[field]) === JSON.stringify(b[field]),
  );
}

const ALL_FIELDS = Object.keys({
  aspectRatio: 0,
  gridStoryboard: 0,
  speechRate: 0,
  episodeTargetDuration: 0,
  adTargetDuration: 0,
  style: 0,
  models: 0,
  narration: 0,
  voiceBinding: 0,
  modelSettings: 0,
} satisfies Record<keyof ProjectSettingsForm, 0>) as (keyof ProjectSettingsForm)[];

export function formEqual(a: ProjectSettingsForm, b: ProjectSettingsForm): boolean {
  return a === b || sameFields(a, b, ALL_FIELDS);
}

export function tabDirty(tab: FormTab, value: ProjectSettingsForm, saved: ProjectSettingsForm): boolean {
  return !sameFields(value, saved, FORM_TAB_FIELDS[tab]);
}

/** 已保存参考图的地址。 */
export function styleImageUrl(projectName: string, styleImage: string): string {
  return `/api/v1/files/${encodeURIComponent(projectName)}/${styleImage}`;
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/** 从项目数据读出表单的已保存内容。 */
export function deriveForm(
  projectData: ProjectData,
  projectName: string,
  globals: GlobalModelDefaults,
): ProjectSettingsForm {
  const project = projectData as unknown as Record<string, unknown>;
  const route = normalizeRoute(project.generation_mode);
  const models = {
    videoBackend: str(project.video_backend),
    videoProviderI2V: str(project.video_provider_i2v),
    videoProviderR2V: str(project.video_provider_r2v),
    imageBackendDefault: str(project.default_image_backend),
    imageBackendT2I: str(project.image_provider_t2i),
    imageBackendI2I: str(project.image_provider_i2i),
    textBackendDefault: str(project.default_text_backend),
    textBackendSimple: str(project.text_backend_simple),
    textBackendComplex: str(project.text_backend_complex),
  };

  // model_settings 的 key 用执行模型（细分项 ‖ 项目默认 ‖ 全局细分 ‖ 全局默认），与保存一致：
  // 后端按执行模型查这张表，键位对不上，用户选的分辨率会被静默忽略。视频另有一条兼容回退：
  // 读 legacy video_model_settings。
  const executingVb = executingVideoModel(models, globals, route === "reference_video");
  const executingI2V = executingVideoModel(models, globals, false);
  const executingIb = executingImageModel(models, globals);
  const modelSettings = (project.model_settings ?? {}) as Record<string, { resolution: string | null }>;
  const legacyVideo = (project.video_model_settings ?? {}) as Record<string, { resolution?: string | null }>;
  const videoResolutions: Record<string, string | null> = Object.fromEntries(
    Object.entries(modelSettings).map(([model, settings]) => [model, settings?.resolution ?? null]),
  );
  for (const model of new Set([executingVb, executingI2V])) {
    if (!model) continue;
    const modelId = model.includes("/") ? model.split("/")[1] : model;
    videoResolutions[model] = videoResolutions[model] ?? legacyVideo[modelId]?.resolution ?? null;
  }

  const rawSpeed = project.narration_speed;
  const rawTarget = project.target_duration;
  const styleImage = str(project.style_image);
  const templateId = str(project.style_template_id);

  return {
    // 后端 get_aspect_ratio() 未设置时按 9:16 生成，这里显示实际生效的比例
    aspectRatio: str(project.aspect_ratio) || "9:16",
    gridStoryboard: project.grid_storyboard === true,
    speechRate: finiteOrNull(project.speech_rate_units_per_second),
    episodeTargetDuration: finiteOrNull(project.episode_target_duration),
    adTargetDuration: typeof rawTarget === "number" && Number.isInteger(rawTarget) && rawTarget > 0 ? rawTarget : null,
    style: styleImage
      ? { kind: "image", preview: styleImageUrl(projectName, styleImage), description: str(project.style_description), file: null }
      : templateId
        ? { kind: "template", templateId }
        : { kind: "none" },
    models: {
      ...models,
      defaultDuration: finiteOrNull(project.default_duration),
      videoResolutions,
      imageResolution: executingIb ? (modelSettings[executingIb]?.resolution ?? null) : null,
      generateAudio: typeof project.video_generate_audio === "boolean" ? project.video_generate_audio : null,
    },
    narration: {
      delivery: (project.narration_delivery === "use_tts" ? "use_tts" : "post_production") satisfies NarrationDelivery,
      audioBackend: str(project.audio_backend),
      narrationVoice: str(project.narration_voice),
      narrationSpeed: typeof rawSpeed === "number" && Number.isFinite(rawSpeed) ? rawSpeed : null,
    },
    voiceBinding: project.character_voice_binding === "reference_audio" ? "reference_audio" : DEFAULT_CHARACTER_VOICE_BINDING,
    modelSettings,
  };
}

export interface ProjectFacts {
  contentMode: string;
  generationRoute: GenerationRoute;
}

/** 宫格是分镜图生视频内的装配选项；参考生视频与不支持宫格的广告项目既不呈现也不参与保存。 */
export function gridToggleVisible(facts: ProjectFacts): boolean {
  return facts.generationRoute === "storyboard" && facts.contentMode !== "ad";
}

type ProjectPatch = Partial<ProjectData> & { clear_style_image?: boolean };

/** 表单值对应的全部 PATCH 键；风格键只在与已保存风格不同时出现。 */
function fullProjectPatch(
  value: ProjectSettingsForm,
  saved: ProjectSettingsForm,
  facts: ProjectFacts,
  globals: GlobalModelDefaults,
): ProjectPatch {
  const { models, narration } = value;

  // resolution 的 key 用执行模型，与读侧一致
  const executingImage = executingImageModel(models, globals);
  const modelSettings: Record<string, { resolution: string | null }> = { ...value.modelSettings };
  for (const [model, resolution] of Object.entries(models.videoResolutions ?? {})) {
    modelSettings[model] = { resolution };
  }
  if (executingImage) modelSettings[executingImage] = { resolution: models.imageResolution };

  const styleChanged = !sameStyle(value.style, saved.style);
  const stylePatch =
    !styleChanged || value.style.kind === "image"
      ? {}
      : value.style.kind === "template"
        ? { style_template_id: value.style.templateId }
        : { style_template_id: null, clear_style_image: true };

  return {
    video_backend: models.videoBackend || null,
    video_provider_i2v: models.videoProviderI2V || null,
    video_provider_r2v: models.videoProviderR2V || null,
    default_image_backend: models.imageBackendDefault || null,
    image_provider_t2i: models.imageBackendT2I || null,
    image_provider_i2i: models.imageBackendI2I || null,
    video_generate_audio: models.generateAudio,
    narration_delivery: narration.delivery,
    audio_backend: narration.audioBackend || null,
    // 音色与后端 .strip() 对齐，保存时去首尾空白
    narration_voice: narration.narrationVoice.trim() || null,
    narration_speed: narration.narrationSpeed,
    // 绑定方式只在参考生视频路线上有效，其余路线该键与项目无关，不写
    ...(facts.generationRoute === "reference_video" ? { character_voice_binding: value.voiceBinding } : {}),
    // null 即清除项目级覆盖、回退语言默认
    speech_rate_units_per_second: value.speechRate,
    default_text_backend: models.textBackendDefault || null,
    text_backend_simple: models.textBackendSimple || null,
    text_backend_complex: models.textBackendComplex || null,
    aspect_ratio: value.aspectRatio || undefined,
    // 生成方式创建后不可更改，不在 PATCH 面上；宫格只在开关可见时写，广告项目对 true 返回 400
    ...(gridToggleVisible(facts) ? { grid_storyboard: value.gridStoryboard } : {}),
    // 广告项目写目标总时长（正整数秒，不可清空），后端对 default_duration 与 episode_target_duration
    // 出现本身返回 400；非广告项目反之，两者恒写，null 即清除
    ...(facts.contentMode === "ad"
      ? value.adTargetDuration !== null
        ? { target_duration: value.adTargetDuration }
        : {}
      : { default_duration: models.defaultDuration, episode_target_duration: value.episodeTargetDuration }),
    model_settings: modelSettings,
    ...stylePatch,
  };
}

function sameModelSettings(a: Record<string, ModelSettingEntry>, b: Record<string, ModelSettingEntry>): boolean {
  const keys = Object.keys(a);
  return keys.length === Object.keys(b).length && keys.every((key) => key in b && a[key].resolution === b[key].resolution);
}

/**
 * 生成项目 PATCH 的请求体：只含与已保存内容不同的键，`model_settings` 整体比较、有变化时整表写入。
 * 未改动的键不回写，避免覆盖别处刚保存的设置；旧项目可能留着裸供应商的音频后端，原样回写也会被快照校验拒绝。
 * 风格选了模版或被清除时一并写入；选了新参考图时不在这里，由随后的参考图上传写入（上传同时清掉模版）。
 */
export function buildProjectPatch(
  value: ProjectSettingsForm,
  saved: ProjectSettingsForm,
  facts: ProjectFacts,
  globals: GlobalModelDefaults,
): ProjectPatch {
  const next = fullProjectPatch(value, saved, facts, globals);
  const base = fullProjectPatch(saved, saved, facts, globals);
  const patch: ProjectPatch = {};
  for (const key of Object.keys(next) as (keyof ProjectPatch)[]) {
    const same =
      key === "model_settings"
        ? sameModelSettings(next.model_settings ?? {}, base.model_settings ?? {})
        : next[key] === base[key];
    if (!same) Object.assign(patch, { [key]: next[key] });
  }
  return patch;
}

/** 是否有待上传的新参考图。 */
export function pendingStyleUpload(value: ProjectSettingsForm, saved: ProjectSettingsForm): File | null {
  return value.style.kind === "image" && value.style.file && !sameStyle(value.style, saved.style)
    ? value.style.file
    : null;
}
