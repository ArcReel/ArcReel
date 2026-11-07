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

/** Raw configured global layers, without collapsing fallbacks; model-selection field families derive effective values through resolution chains. */
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
  /** Backend automatic models when all layers are empty, matching execution; fetch failures leave inference unresolved. */
  auto?: AutoBackendsResponse | null;
}

/** Project styles: templates/references mutually exclusive, or neither. */
export type ProjectStyle =
  | { kind: "none" }
  | { kind: "template"; templateId: string }
  | {
      kind: "image";
      /** Saved reference URLs, or blob: URLs for pending uploads. */
      preview: string;
      /** Server-analyzed style descriptions, empty before upload. */
      description: string;
      /** New references awaiting uploads; null means references already saved. */
      file: File | null;
    };

/** One edit unit shared by basics/style/model/dubbing project-setting tabs. */
export interface ProjectSettingsForm {
  aspectRatio: string;
  gridStoryboard: boolean;
  /** Estimated speaking rates in reading units/second; null uses project-language defaults. */
  speechRate: number | null;
  episodeTargetDuration: number | null;
  /** Ad target total seconds; nonpositive/noninteger custom inputs mean null, blocking saves. */
  adTargetDuration: number | null;
  style: ProjectStyle;
  /** Model lanes; `videoResolution` derives from execution-model `videoResolutions`, never separately saved. */
  models: Omit<ModelConfigValue, "videoResolution"> & {
    /** Generate audio; null follows globals. */
    generateAudio: boolean | null;
  };
  narration: NarrationDeliveryValue;
  voiceBinding: CharacterVoiceBinding;
  /** Full saved project `model_settings`, merged when saving resolutions. */
  modelSettings: Record<string, { resolution: string | null }>;
}

/** Fields by form tab, marking edited sidebar tabs. */
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
    // File serializes as empty objects; compare references.
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

/**
 * Saved-reference URLs. Filenames stay fixed (style_reference.*), so image replacements retain paths;
 * `version` carries fingerprints/upload instants to replace browser caches; null only without actual versions.
 */
export function styleImageUrl(projectName: string, styleImage: string, version: number | string | null): string {
  return API.getFileUrl(projectName, styleImage, version);
}

/**
 * Project PATCH changes no reference files and returns no fingerprints; references remaining saved reuse versions from saved URLs,
 * preventing saves from replacing previews with unversioned addresses.
 */
export function keepSavedStylePreview(next: ProjectSettingsForm, saved: ProjectSettingsForm): ProjectSettingsForm {
  if (next.style.kind !== "image" || saved.style.kind !== "image" || saved.style.file) return next;
  const unversioned = (url: string) => url.split("?")[0];
  if (unversioned(next.style.preview) !== unversioned(saved.style.preview)) return next;
  return { ...next, style: { ...next.style, preview: saved.style.preview } };
}

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Read saved form contents from project data.
 * `fingerprints` are detail-response asset fingerprints (path → mtime_ns), versioning reference URLs; null when omitted.
 */
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

  // model_settings keys use execution models (subtiers ‖ project defaults ‖ global subtiers ‖ global defaults), matching saves:
  // backends query by execution identities; mismatched keys silently lose user resolutions. Video additionally falls back to
  // legacy video_model_settings.
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
    // Unset backend get_aspect_ratio() generates 9:16; show effective ratios here.
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

/** Grids are storyboard image-to-video assembly options; reference video/unsupported ad projects neither display nor save them. */
export function gridToggleVisible(facts: ProjectFacts): boolean {
  return facts.generationRoute === "storyboard" && facts.contentMode !== "ad";
}

type ProjectPatch = Partial<ProjectData> & { clear_style_image?: boolean };

/** All PATCH keys for form values; style keys appear only when differing from saved styles. */
function fullProjectPatch(
  value: ProjectSettingsForm,
  saved: ProjectSettingsForm,
  facts: ProjectFacts,
  globals: GlobalModelDefaults,
): ProjectPatch {
  const { models, narration } = value;

  // Resolution keys use execution models, matching readers.
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
    // Trim voice names on saves, matching backend .strip().
    narration_voice: narration.narrationVoice.trim() || null,
    narration_speed: narration.narrationSpeed,
    // Binding modes apply only to reference video; other routes omit unrelated keys.
    ...(facts.generationRoute === "reference_video" ? { character_voice_binding: value.voiceBinding } : {}),
    // null clears project overrides, falling back to language defaults.
    speech_rate_units_per_second: value.speechRate,
    default_text_backend: models.textBackendDefault || null,
    text_backend_simple: models.textBackendSimple || null,
    text_backend_complex: models.textBackendComplex || null,
    aspect_ratio: value.aspectRatio || undefined,
    // Generation modes cannot change after creation, outside PATCH; grids write only when visible, ads reject true with 400.
    ...(gridToggleVisible(facts) ? { grid_storyboard: value.gridStoryboard } : {}),
    // Ads write positive-integer total target seconds, never clearable; mere presence of default_duration / episode_target_duration
    // returns backend 400. Nonads do the reverse, always writing both, null clearing.
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
 * Build project PATCH bodies with changed keys only; compare whole `model_settings`, writing full tables when changed.
 * Never rewrite unchanged keys, avoiding overriding external saves; old bare-provider audio settings fail snapshot validation even when echoed unchanged.
 * Selected/cleared templates write style here; new reference images instead upload afterward, clearing templates during upload.
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

/** Whether new references await upload. */
export function pendingStyleUpload(value: ProjectSettingsForm, saved: ProjectSettingsForm): File | null {
  return value.style.kind === "image" && value.style.file && !sameStyle(value.style, saved.style)
    ? value.style.file
    : null;
}
