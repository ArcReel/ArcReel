import { ApiRequestError } from "@/api";
import type { EndpointReference } from "@/types";

/** Models still using an endpoint, listed in a deletion-rejected 409 response; null for other errors. */
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
