import { ApiRequestError } from "@/api";
import type { EndpointReference } from "@/types";

/** 删除端点被拒（409）时，响应里列出的仍在使用它的模型；不是这类错误时为 null。 */
export function endpointReferences(error: unknown): EndpointReference[] | null {
  if (!(error instanceof ApiRequestError) || error.status !== 409) return null;
  const references =
    typeof error.diagnostic === "object" && error.diagnostic !== null
      ? (error.diagnostic as { references?: unknown }).references
      : undefined;
  if (!Array.isArray(references)) return null;
  return references.filter(
    (reference): reference is EndpointReference =>
      typeof reference === "object" &&
      reference !== null &&
      typeof (reference as EndpointReference).provider_id === "number" &&
      typeof (reference as EndpointReference).provider_display_name === "string" &&
      typeof (reference as EndpointReference).model_id === "string" &&
      typeof (reference as EndpointReference).model_display_name === "string",
  );
}
