import type {
  ComfyuiEndpointDefinition,
  ComfyuiInferResponse,
  CustomEndpointInfo,
} from "@/types";

/**
 * Newly imported unsaved ComfyUI endpoints: definitions paired with this inference round.
 *
 * New imports have null `record`; reimports into saved endpoints reference them, saving through whole replacement
 * while preserving keys/model associations.
 */
export interface ComfyuiImportDraft {
  /**
   * Disposable import identities, changed for every new draft.
   *
   * Detail components own definitions/bindings/inference in `useState`, reading props only at mount. Reimports preserve
   * keys/selections, so unchanged instances would keep displaying/saving old workflows. Include this in detail
   * React keys, creating instances per new draft.
   */
  token: string;
  record: CustomEndpointInfo | null;
  definition: ComfyuiEndpointDefinition;
  /** Source filenames, displayed by detail headers. */
  fileName: string;
  inference: ComfyuiInferResponse;
}

/**
 * Attach imported payloads to existing endpoints: replace workflows, retaining identities/media types/credentials/confirmed bindings.
 * Rematching inputs are new workflows plus original bindings.
 *
 * Already-formed ComfyUI definitions bypass this step: users replace whole definitions, carrying their own
 * names/media types/bindings.
 */
export function reimportedDefinition(
  existing: ComfyuiEndpointDefinition,
  incoming: ComfyuiEndpointDefinition,
  incomingIsDefinition: boolean,
): ComfyuiEndpointDefinition {
  if (incomingIsDefinition) return incoming;
  return { ...existing, workflow: incoming.workflow };
}

/** Whether definitions are ComfyUI endpoint definitions. */
export function isComfyuiDefinition(value: unknown): value is ComfyuiEndpointDefinition {
  return typeof value === "object" && value !== null && (value as { kind?: unknown }).kind === "comfyui";
}
