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
      <ul className="mt-2 flex flex-col gap-1">
        {references.map((reference) => (
          <li key={`${reference.provider_id}:${reference.model_id}`}>
            {/* 行内链接式动作，长名称需要折行，不用定高的 Button */}
            <button
              type="button"
              onClick={() => onNavigateToModel(reference)}
              className="rounded-sm text-left text-primary underline-offset-4 outline-none hover:underline focus-visible:ring-3 focus-visible:ring-ring/50"
            >
              {reference.provider_display_name} · {reference.model_display_name} — {t("ce_go_to_model")}
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
