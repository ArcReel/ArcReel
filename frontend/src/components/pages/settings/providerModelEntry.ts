import type { TFunction } from "i18next";
import type { CapabilityKey, ProviderModelEntryInfo, ProviderModelItem } from "@/types";
import { priceTableOf, type PriceTable } from "./price-editor/priceTable";

/** Editable portion of model information. */
export interface EntryDraft {
  display_name: string;
  facts: Record<string, unknown>;
  /** Price list being edited; no billing items means no price entered. */
  price: PriceTable;
}

export function entryDraftOf(entry: ProviderModelEntryInfo | null): EntryDraft | null {
  return entry ? { display_name: entry.display_name, facts: entry.facts, price: priceTableOf(entry.price) } : null;
}

/** Editable model facts by media type in UI order; duration constraints immediately follow available durations. */
export const EDITABLE_FACTS: Record<ProviderModelItem["media_type"], CapabilityKey[]> = {
  text: ["image_input", "structured_output", "max_output_tokens"],
  image: ["text_to_image", "image_to_image", "resolutions"],
  video: ["durations", "duration_constraints", "resolutions"],
  audio: [],
};

/** Provider-name list, such as A and B. */
export function providerNames(t: TFunction, entry: ProviderModelEntryInfo): string {
  return entry.shared_with
    .map((p) => t("pm_entry_provider_name", { name: p.display_name }))
    .join(t("pm_entry_provider_separator"));
}
