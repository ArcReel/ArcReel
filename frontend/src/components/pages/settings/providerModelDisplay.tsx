import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { cn } from "cn";
import type { PriceSummary, ProviderModelFlag, ProviderModelItem } from "@/types";
import { formatCurrencyAmount } from "@/utils/cost-format";

// 供应商模型列表与模型 Sheet 共用的展示：媒体类型名、状态标记与价格摘要。

/** 媒体类型的界面名。 */
export const MEDIA_LABEL_KEY: Record<ProviderModelItem["media_type"], string> = {
  text: "media_type_text",
  image: "media_type_image",
  video: "media_type_video",
  audio: "media_type_audio",
};

const FLAG_LABEL_KEY: Record<ProviderModelFlag, string> = {
  discontinued: "pm_flag_discontinued",
  unpriced: "pm_flag_unpriced",
  modified: "pm_flag_modified",
};

const FLAG_TONE: Record<ProviderModelFlag, string> = {
  discontinued: "border-destructive/50 text-destructive",
  unpriced: "border-warn/50 text-warn",
  modified: "border-border text-muted-foreground",
};

// 价格摘要里各计费项的写法，如「$0.05/百万 token 输入」；没有登记的计费项不显示。
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

/** 价格摘要的一行文字，如「$0.05/百万 token 输入，$0.4/百万 token 输出」。 */
export function priceSummaryText(t: TFunction, price: PriceSummary): string {
  return price.items
    .filter((item) => PRICE_ITEM_KEY[item.meter])
    .map((item) => t(PRICE_ITEM_KEY[item.meter], { price: priceRange(price.currency, item.min_price, item.max_price) }))
    .join(t("pm_price_separator"));
}

/** 模型的状态标记（已下架、未定价、已修改），列表行与 Sheet 标题共用。 */
export function ProviderModelFlags({ flags }: { flags: ProviderModelFlag[] }) {
  const { t } = useTranslation("dashboard");
  return flags.map((flag) => (
    <span
      key={flag}
      className={cn(
        "inline-flex h-5 shrink-0 items-center rounded-full border px-1.5 text-xs font-normal whitespace-nowrap",
        FLAG_TONE[flag],
      )}
    >
      {t(FLAG_LABEL_KEY[flag])}
    </span>
  ));
}
