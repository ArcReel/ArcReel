import type {
  ComfyuiEndpointDefinition,
  ComfyuiInferResponse,
  CustomEndpointInfo,
} from "@/types";

export interface ComfyuiImportDraft {
  token: string;
  record: CustomEndpointInfo | null;
  definition: ComfyuiEndpointDefinition;
  fileName: string;
  inference: ComfyuiInferResponse;
}

export function reimportedDefinition(
  existing: ComfyuiEndpointDefinition,
  incoming: ComfyuiEndpointDefinition,
  incomingIsDefinition: boolean,
): ComfyuiEndpointDefinition {
  if (incomingIsDefinition) return incoming;
  return { ...existing, workflow: incoming.workflow };
}

export function isComfyuiDefinition(value: unknown): value is ComfyuiEndpointDefinition {
  return typeof value === "object" && value !== null && (value as { kind?: unknown }).kind === "comfyui";
}
