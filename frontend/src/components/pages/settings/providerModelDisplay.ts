import type { TFunction } from "i18next";
import type { PriceSummary, ProviderModelItem } from "@/types";
import { formatCurrencyAmount } from "@/utils/cost-format";

// Shared display copy for provider-model lists and model Sheets: media-type names and price summaries.

/** Media type's UI name. */
export const MEDIA_LABEL_KEY: Record<ProviderModelItem["media_type"], string> = {
  text: "media_type_text",
  image: "media_type_image",
  video: "media_type_video",
  audio: "media_type_audio",
};

// Price-summary billing-item wording, such as $0.05 per million input tokens; omit unregistered billing items.
const PRICE_ITEM_KEY: Record<string, string> = {
  uncached_input_tokens: "pm_price_input_tokens",
  output_tokens: "pm_price_output_tokens",
  output_images: "pm_price_output_images",
  image_output_tokens: "pm_price_image_output_tokens",
  output_seconds: "pm_price_output_seconds",
  video_tokens: "pm_price_video_tokens",
  characters: "pm_price_characters",
  audio_output_tokens: "pm_price_audio_output_tokens",
  requests: "pm_price_requests",
};

const PRICE_DIGITS = { minimumFractionDigits: 0, maximumFractionDigits: 4 };

function priceRange(currency: string, min: number, max: number): string {
  const low = formatCurrencyAmount(currency, min, PRICE_DIGITS);
  if (min === max) return low;
  return `${low}–${max.toLocaleString("en", PRICE_DIGITS)}`;
}

/** One price-summary line, such as $0.05 per million input tokens and $0.4 per million output tokens. */
export function priceSummaryText(t: TFunction, price: PriceSummary): string {
  return price.items
    .filter((item) => PRICE_ITEM_KEY[item.meter])
    .map((item) => t(PRICE_ITEM_KEY[item.meter], { price: priceRange(price.currency, item.min_price, item.max_price) }))
    .join(t("pm_price_separator"));
}
