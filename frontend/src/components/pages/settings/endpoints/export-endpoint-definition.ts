import type { EndpointDefinition } from "@/types";
import { downloadBlob } from "@/utils/download";
import { definitionFileName } from "./endpoint-definition-draft";

/** 把定义原样下载为 JSON 文件；定义本身不含凭证。有安装记录时文件名取记录里的 slug。 */
export function exportEndpointDefinition(definition: EndpointDefinition, installationSlug?: string | null): void {
  const blob = new Blob([JSON.stringify(definition, null, 2)], { type: "application/json" });
  downloadBlob(blob, definitionFileName(definition, installationSlug));
}
