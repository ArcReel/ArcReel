import { useTranslation } from "react-i18next";
import { ApiRequestError } from "@/api";
import type { EndpointReference } from "@/types";

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

export function EndpointReferenceList({
  references,
  onNavigateToModel,
}: {
  references: EndpointReference[];
  onNavigateToModel: (reference: EndpointReference) => void;
}) {
  const { t } = useTranslation("dashboard");
  return (
    <div>
      <p>{t("ce_delete_blocked")}</p>
      <ul className="mt-2 space-y-1">
        {references.map((reference) => (
          <li key={`${reference.provider_id}:${reference.model_id}`}>
            <button
              type="button"
              onClick={() => onNavigateToModel(reference)}
              className="text-left text-accent-2 underline decoration-accent/40 underline-offset-2 hover:text-accent"
            >
              {reference.provider_display_name} · {reference.model_display_name} — {t("ce_go_to_model")}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
