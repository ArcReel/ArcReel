import { API } from "@/api";
import type { ProjectSettingsTab } from "@/app-routes";
import { executingImageModel, executingVideoModel } from "@/components/shared/model-layers";
import type { ModelConfigValue } from "@/components/shared/ModelConfigSection";
import type { NarrationDeliveryValue } from "@/components/shared/narration-delivery";
import type {
  AutoBackendsResponse,
  CharacterVoiceBinding,
  ModelSettingEntry,
  NarrationDelivery,
  ProjectData,
} from "@/types";
import { DEFAULT_CHARACTER_VOICE_BINDING } from "@/types";
import { normalizeRoute, type GenerationRoute } from "@/utils/generation-mode";

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
  auto?: AutoBackendsResponse | null;
}

export type ProjectStyle =
  | { kind: "none" }
  | { kind: "template"; templateId: string }
  | {
      kind: "image";
      preview: string;
      description: string;
      file: File | null;
    };

export interface ProjectSettingsForm {
  aspectRatio: string;
  gridStoryboard: boolean;
  speechRate: number | null;
  episodeTargetDuration: number | null;
  adTargetDuration: number | null;
  style: ProjectStyle;
  models: Omit<ModelConfigValue, "videoResolution" | "videoResolutions" | "imageResolution" | "imageResolutions"> & {
    modelResolutions: Record<string, string | null>;
    generateAudio: boolean | null;
  };
  narration: NarrationDeliveryValue;
  voiceBinding: CharacterVoiceBinding;
  modelSettings: Record<string, ModelSettingEntry>;
}

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

export function styleImageUrl(projectName: string, styleImage: string, version: number | string | null): string {
  return API.getFileUrl(projectName, styleImage, version);
}

export function keepSavedStylePreview(next: ProjectSettingsForm, saved: ProjectSettingsForm): ProjectSettingsForm {
  if (next.style.kind !== "image" || saved.style.kind !== "image" || saved.style.file) return next;
  const unversioned = (url: string) => url.split("?")[0];
  if (unversioned(next.style.preview) !== unversioned(saved.style.preview)) return next;
  return { ...next, style: { ...next.style, preview: saved.style.preview } };
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

export function storedImageQuality(settings: Record<string, ModelSettingEntry>, model: string): string | null {
  const value = settings[model]?.quality;
  return typeof value === "string" ? value : null;
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function settingEntry(value: unknown): ModelSettingEntry {
  return value !== null && typeof value === "object" && !Array.isArray(value) ? value : {};
}

export function deriveForm(
  projectData: ProjectData,
  projectName: string,
  globals: GlobalModelDefaults,
  fingerprints: Readonly<Record<string, number>> | null,
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

  const executingVb = executingVideoModel(models, globals, route === "reference_video");
  const executingI2V = executingVideoModel(models, globals, false);
  const executingIb = executingImageModel(models, globals, "t2i");
  const executingI2I = executingImageModel(models, globals, "i2i");
  const modelSettings = (project.model_settings ?? {}) as Record<string, ModelSettingEntry>;
  const legacyVideo = (project.video_model_settings ?? {}) as Record<string, { resolution?: string | null }>;
  const modelResolutions: Record<string, string | null> = Object.fromEntries(
    Object.entries(modelSettings).map(([model, settings]) => [model, settingEntry(settings).resolution ?? null]),
  );
  for (const model of new Set([executingVb, executingI2V])) {
    if (!model) continue;
    const modelId = model.includes("/") ? model.split("/")[1] : model;
    modelResolutions[model] = modelResolutions[model] ?? legacyVideo[modelId]?.resolution ?? null;
  }
  for (const model of new Set([executingIb, executingI2I])) {
    if (!model) continue;
    modelResolutions[model] = modelResolutions[model] ?? null;
  }

  const rawSpeed = project.narration_speed;
  const rawTarget = project.target_duration;
  const styleImage = str(project.style_image);
  const templateId = str(project.style_template_id);

  return {
    aspectRatio: str(project.aspect_ratio) || "9:16",
    gridStoryboard: project.grid_storyboard === true,
    speechRate: finiteOrNull(project.speech_rate_units_per_second),
    episodeTargetDuration: finiteOrNull(project.episode_target_duration),
    adTargetDuration: typeof rawTarget === "number" && Number.isInteger(rawTarget) && rawTarget > 0 ? rawTarget : null,
    style: styleImage
      ? {
          kind: "image",
          preview: styleImageUrl(projectName, styleImage, fingerprints?.[styleImage] ?? null),
          description: str(project.style_description),
          file: null,
        }
      : templateId
        ? { kind: "template", templateId }
        : { kind: "none" },
    models: {
      ...models,
      defaultDuration: finiteOrNull(project.default_duration),
      modelResolutions,
      imageQuality: storedImageQuality(modelSettings, executingIb),
      generateAudio: typeof project.video_generate_audio === "boolean" ? project.video_generate_audio : null,
    },
    narration: {
      override: Boolean(project.audio_backend),
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

export function gridToggleVisible(facts: ProjectFacts): boolean {
  return facts.generationRoute === "storyboard" && facts.contentMode !== "ad";
}

type ProjectPatch = Partial<ProjectData> & { clear_style_image?: boolean };

function fullProjectPatch(
  value: ProjectSettingsForm,
  saved: ProjectSettingsForm,
  facts: ProjectFacts,
  globals: GlobalModelDefaults,
): ProjectPatch {
  const { models, narration } = value;

  const executingImage = executingImageModel(models, globals, "t2i");
  const modelSettings: Record<string, ModelSettingEntry> = { ...value.modelSettings };
  for (const [model, resolution] of Object.entries(models.modelResolutions)) {
    modelSettings[model] = { ...settingEntry(modelSettings[model]), resolution };
  }
  if (executingImage) {
    const { quality: _quality, ...rest } = settingEntry(modelSettings[executingImage]);
    modelSettings[executingImage] = {
      ...rest,
      resolution: models.modelResolutions[executingImage] ?? null,
      ...(models.imageQuality ? { quality: models.imageQuality } : {}),
    };
  }

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
    narration_voice: narration.narrationVoice.trim() || null,
    narration_speed: narration.narrationSpeed,
    ...(facts.generationRoute === "reference_video" ? { character_voice_binding: value.voiceBinding } : {}),
    speech_rate_units_per_second: value.speechRate,
    default_text_backend: models.textBackendDefault || null,
    text_backend_simple: models.textBackendSimple || null,
    text_backend_complex: models.textBackendComplex || null,
    aspect_ratio: value.aspectRatio || undefined,
    ...(gridToggleVisible(facts) ? { grid_storyboard: value.gridStoryboard } : {}),
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
  return (
    keys.length === Object.keys(b).length &&
    keys.every(
      (key) =>
        key in b &&
        a[key].resolution === b[key].resolution &&
        (a[key].quality ?? null) === (b[key].quality ?? null),
    )
  );
}

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
  if (value.narration.override && (value.narration.audioBackend !== saved.narration.audioBackend || !saved.narration.override)) {
    patch.audio_backend = value.narration.audioBackend;
    patch.narration_voice = value.narration.narrationVoice.trim();
    patch.narration_speed = value.narration.narrationSpeed;
  } else if (!value.narration.override && saved.narration.override) {
    patch.audio_backend = null;
    delete patch.narration_voice;
    delete patch.narration_speed;
  }
  return patch;
}

export function pendingStyleUpload(value: ProjectSettingsForm, saved: ProjectSettingsForm): File | null {
  return value.style.kind === "image" && value.style.file && !sameStyle(value.style, saved.style)
    ? value.style.file
    : null;
}
