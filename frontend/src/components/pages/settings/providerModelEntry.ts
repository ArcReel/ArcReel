import type { TFunction } from "i18next";
import type { CapabilityKey, ProviderModelEntryInfo, ProviderModelItem } from "@/types";
import { priceTableOf, type PriceTable } from "./price-editor/priceTable";

export interface EntryDraft {
  display_name: string;
  facts: Record<string, unknown>;
  /** 编辑中的价目表；没有计费项就是「不填价格」。 */
  price: PriceTable;
}

export function entryDraftOf(entry: ProviderModelEntryInfo | null): EntryDraft | null {
  return entry ? { display_name: entry.display_name, facts: entry.facts, price: priceTableOf(entry.price) } : null;
}

/** 各媒体类型可以修改的模型事实，按界面顺序；时长联动约束紧跟可选时长。 */
export const EDITABLE_FACTS: Record<ProviderModelItem["media_type"], CapabilityKey[]> = {
  text: ["image_input", "structured_output", "max_output_tokens"],
  image: ["text_to_image", "image_to_image", "resolutions"],
  video: ["durations", "duration_constraints", "resolutions"],
  audio: [],
};

export function providerNames(t: TFunction, entry: ProviderModelEntryInfo): string {
  return entry.shared_with
    .map((p) => t("pm_entry_provider_name", { name: p.display_name }))
    .join(t("pm_entry_provider_separator"));
}
