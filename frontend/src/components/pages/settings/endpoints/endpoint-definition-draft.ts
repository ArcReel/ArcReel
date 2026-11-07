import type {
  AnyEndpointDefinition,
  ComfyuiEndpointDefinition,
  EndpointDefinition,
  EndpointExtractSpec,
  EndpointInputEncoding,
  EndpointInputSource,
  EndpointPathItem,
  EndpointStandardStatus,
} from "@/types";

// Definition-draft read/write helpers. Forms/JSON views share EndpointDefinition objects,
// converting UI-required shapes and preserving unknown fields on writes only, without validation:
// server POST /custom-endpoints/validate owns validation exclusively.

/** Declarative media types; absent `media_type` means video. */
export type EndpointMediaType = "image" | "video";

export function definitionMediaType(definition: EndpointDefinition): EndpointMediaType {
  return definition.media_type ?? "video";
}

/** Output extraction keys for result URLs/inline base64 in polling or secondary retrieval sections. */
export type EndpointArtifactKey = "video_url" | "image_url" | "image_b64";

/**
 * Media-specific form differences match backend MEDIA_TYPE_RULES:
 * available material sources, output extraction keys in runtime precedence, and generation variables insertable into bodies.
 */
export interface MediaTypeFormProfile {
  inputSources: EndpointInputSource[];
  artifactKeys: EndpointArtifactKey[];
  /** Variable names and description-copy i18n keys. */
  generationVariables: { name: string; descKey: string }[];
}

const variable = (name: string, descKey = `ce_var_${name}`) => ({ name, descKey });

export const MEDIA_TYPE_FORM_PROFILES: Record<EndpointMediaType, MediaTypeFormProfile> = {
  video: {
    inputSources: ["start_image", "end_image", "reference_images", "reference_audio_files"],
    artifactKeys: ["video_url"],
    generationVariables: [
      variable("prompt"),
      variable("model"),
      variable("duration"),
      variable("resolution"),
      variable("aspect_ratio"),
      variable("width"),
      variable("height"),
      variable("generate_audio"),
    ],
  },
  image: {
    inputSources: ["reference_images"],
    artifactKeys: ["image_url", "image_b64"],
    generationVariables: [
      variable("prompt"),
      variable("model"),
      variable("resolution", "ce_var_resolution_image"),
      variable("aspect_ratio"),
      variable("width"),
      variable("height"),
      variable("seed"),
    ],
  },
};

export const INPUT_ENCODINGS: EndpointInputEncoding[] = ["data_uri", "base64"];

export const STANDARD_STATUSES: EndpointStandardStatus[] = [
  "queued",
  "running",
  "succeeded",
  "failed",
];

export const HTTP_METHODS = ["GET", "POST", "PUT"];

/** Form section IDs also locate diagnostics and number vertical rails. */
export type EndpointFormSection =
  | "meta"
  | "auth"
  | "inputs"
  | "submit"
  | "poll"
  | "status"
  | "capabilities"
  | "test";

/** New-definition prefill: methods/identity status mappings have common defaults; variable capabilities/extraction paths stay empty. */
export function newEndpointDefinition(author: string): EndpointDefinition {
  return {
    kind: "declarative",
    schema_version: "1.2.0",
    meta: { name: "", author, version: "1.0.0" },
    auth: { headers: { Authorization: "Bearer {{ api_key }}" } },
    inputs: { first_frame: { source: "start_image", encoding: "data_uri" } },
    submit: {
      method: "POST",
      url: "{{ base_url }}/",
      body: {},
      extract: { task_id: [] },
    },
    poll: {
      method: "GET",
      url: "{{ base_url }}/",
      extract: { status: [], video_url: [] },
    },
    status_map: {
      queued: "queued",
      processing: "running",
      succeeded: "succeeded",
      failed: "failed",
    },
  };
}

// ---------------------------------------------------------------------------
// Extraction paths
// ---------------------------------------------------------------------------

/** Read path arrays, normalizing short/full declarations; absence means empty arrays. */
export function readPaths(spec: EndpointExtractSpec | undefined): EndpointPathItem[] {
  if (spec === undefined) return [];
  if (Array.isArray(spec)) return spec;
  return spec.paths ?? [];
}

/** Write paths retaining original accept; empty arrays become empty shorthand, never deleted keys, avoiding silent field loss. */
export function writePaths(
  spec: EndpointExtractSpec | undefined,
  paths: EndpointPathItem[],
): EndpointExtractSpec {
  if (spec !== undefined && !Array.isArray(spec)) {
    return spec.accept === undefined ? { paths } : { paths, accept: spec.accept };
  }
  return paths;
}

/** Whether path items edit directly in forms; json_decode edits only through JSON views. */
export function isPlainPath(item: EndpointPathItem): item is string {
  return typeof item === "string";
}

export function pathItemText(item: EndpointPathItem): string {
  return isPlainPath(item) ? item : item.path;
}

// ---------------------------------------------------------------------------
// Diagnostic locations
// ---------------------------------------------------------------------------

const SECTION_BY_ROOT: Record<string, EndpointFormSection> = {
  meta: "meta",
  auth: "auth",
  inputs: "inputs",
  submit: "submit",
  poll: "poll",
  result: "poll",
  status_map: "status",
  capabilities: "capabilities",
};

/**
 * Map diagnostic paths, such as `poll.extract.video_url[0]`, to form sections.
 * Fields without controls, such as `enum_maps`, `defaults`, `schema_version`, return null,
 * requiring JSON views; assigning sections would send users somewhere the field does not exist.
 */
export function sectionOfIssuePath(path: string): EndpointFormSection | null {
  const root = path.replace(/^\$\.?/, "").split(/[.[]/, 1)[0];
  return SECTION_BY_ROOT[root] ?? null;
}

// ---------------------------------------------------------------------------
// Immutable writes
// ---------------------------------------------------------------------------

/** Rebuild objects in key order so renaming preserves field order, avoiding needless JSON-view jumps. */
export function renameKey<T>(
  record: Record<string, T>,
  from: string,
  to: string,
  value: T,
): Record<string, T> {
  if (from !== to && to in record) return record;
  const next: Record<string, T> = {};
  for (const [k, v] of Object.entries(record)) {
    if (k === from) next[to] = value;
    else next[k] = v;
  }
  if (!(from in record)) next[to] = value;
  return next;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * Whether definitions are declarative. ComfyUI has only `workflow`/`bindings`, lacking directly dereferenced
 * `submit` / `poll` in forms/headers; both must branch on the same predicate.
 */
export function isDeclarativeDefinition(value: AnyEndpointDefinition): value is EndpointDefinition {
  return value.kind === "declarative";
}

/**
 * Structural gates before JSON-view draft writes: forms/headers do not defensively access definitions,
 * directly dereferencing meta, submit.extract and poll.extract; any missing container prevents rendering.
 * Missing individual fields such as meta.name affect single controls only; server validation diagnoses them.
 */
export function isRenderableDefinition(value: unknown): value is EndpointDefinition {
  return (
    isRecord(value) &&
    isRecord(value.meta) &&
    isRecord(value.submit) &&
    isRecord(value.submit.extract) &&
    isRecord(value.poll) &&
    isRecord(value.poll.extract)
  );
}

/**
 * ComfyUI structure gates serve {@link isRenderableDefinition}'s purpose, checking only display-dereferenced
 * meta/workflow containers; servers validate individual fields.
 */
export function isRenderableComfyuiDefinition(value: unknown): value is ComfyuiEndpointDefinition {
  return isRecord(value) && value.kind === "comfyui" && isRecord(value.meta) && isRecord(value.workflow);
}

/**
 * Derive market slugs by `^[a-z0-9][a-z0-9-]{0,63}$`: remove accents, lowercase, replace other characters with hyphens;
 * names without usable ASCII yield empty strings.
 */
export function slugFromName(name: string): string {
  return name
    .normalize("NFKD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+/, "")
    .slice(0, 64)
    .replace(/-+$/, "");
}

/** Export filenames use installed slugs, otherwise derive from meta.name, falling back to `endpoint` when empty. */
export function definitionFileName(definition: AnyEndpointDefinition, installationSlug?: string | null): string {
  if (installationSlug) return `${installationSlug}.json`;
  return `${slugFromName(definition.meta?.name ?? "") || "endpoint"}.json`;
}
