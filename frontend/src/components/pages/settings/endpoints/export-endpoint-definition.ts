import type { AnyEndpointDefinition } from "@/types";
import { downloadBlob } from "@/utils/download";
import { definitionFileName } from "./endpoint-definition-draft";

/** Download the definition unchanged as JSON; definitions contain no credentials. Use the installation-record slug for the filename when present. */
export function exportEndpointDefinition(definition: AnyEndpointDefinition, installationSlug?: string | null): void {
  const blob = new Blob([JSON.stringify(definition, null, 2)], { type: "application/json" });
  downloadBlob(blob, definitionFileName(definition, installationSlug));
}
