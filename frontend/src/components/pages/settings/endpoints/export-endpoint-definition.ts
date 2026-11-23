import type { AnyEndpointDefinition } from "@/types";
import { downloadBlob } from "@/utils/download";
import { definitionFileName } from "./endpoint-definition-draft";

export function exportEndpointDefinition(definition: AnyEndpointDefinition, installationSlug?: string | null): void {
  const blob = new Blob([JSON.stringify(definition, null, 2)], { type: "application/json" });
  downloadBlob(blob, definitionFileName(definition, installationSlug));
}
