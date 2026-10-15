import type { TFunction } from "i18next";
import { DIMENSIONS, METERS } from "./priceVocabulary.generated";
import {
  isKnownDimension,
  isKnownMeter,
  type DimKey,
  type DimValue,
  type MeterKey,
  type PriceMeter,
  type PriceTable,
} from "./priceTable";

// 价目表的界面文字：计费项名称、单位、选择维度与条件。计费项名称与使用记录共用同一批 key。

/** 使用记录里已有的计费项名称（输入、缓存命中、输出）。 */
const USAGE_NAME_KEY: Partial<Record<MeterKey, string>> = {
  uncached_input_tokens: "usage_field_input_tokens",
  cache_read_tokens: "usage_field_cache_read_tokens",
  output_tokens: "usage_field_output_tokens",
};

/** 日常说法的计费项名称。 */
const PLAIN_NAME_KEY: Partial<Record<MeterKey, string>> = {
  output_images: "pm_pe_meter_output_images",
  input_images: "pm_pe_meter_input_images",
  output_seconds: "pm_pe_meter_output_seconds",
  video_tokens: "pm_pe_meter_video_tokens",
  input_video_seconds: "pm_pe_meter_input_video_seconds",
  characters: "pm_pe_meter_characters",
  requests: "pm_pe_meter_requests",
};

/** 在使用记录的名称后加限定词的计费项，如「输入（图片）」「缓存写入（5 分钟）」。 */
const QUALIFIED_NAME: Partial<Record<MeterKey, { base: string; qualifier: string }>> = {
  cache_write_5m_tokens: { base: "usage_field_cache_write_tokens", qualifier: "usage_field_cache_write_5m_tokens" },
  cache_write_1h_tokens: { base: "usage_field_cache_write_tokens", qualifier: "usage_field_cache_write_1h_tokens" },
  audio_input_tokens: { base: "usage_field_input_tokens", qualifier: "pm_pe_qualifier_audio" },
  uncached_image_input_tokens: { base: "usage_field_input_tokens", qualifier: "pm_pe_qualifier_image" },
  image_cache_read_tokens: { base: "usage_field_cache_read_tokens", qualifier: "pm_pe_qualifier_image" },
  image_output_tokens: { base: "usage_field_output_tokens", qualifier: "pm_pe_qualifier_image" },
  // 音频的两个 token 计费项名称待维护者确定：输入沿用「输入」，输出音频暂名「输出（音频）」
  audio_output_tokens: { base: "usage_field_output_tokens", qualifier: "pm_pe_qualifier_audio" },
};

/** 计费项的界面名；词表里没有的计费项原样显示键。 */
export function meterName(t: TFunction, key: string): string {
  if (!isKnownMeter(key)) return key;
  const usage = USAGE_NAME_KEY[key];
  if (usage) return t(usage);
  const plain = PLAIN_NAME_KEY[key];
  if (plain) return t(plain);
  const qualified = QUALIFIED_NAME[key];
  if (qualified) return t("pm_pe_meter_qualified", { name: t(qualified.base), qualifier: t(qualified.qualifier) });
  return key;
}

/** 单价对应的计价单位，如「/百万 token」「/秒」。 */
export function meterUnit(t: TFunction, key: string): string {
  if (!isKnownMeter(key)) return "";
  if (key === "characters") return t("pm_pe_unit_per_10k_characters");
  if (key === "output_images" || key === "input_images") return t("pm_pe_unit_per_image");
  if (key === "output_seconds" || key === "input_video_seconds") return t("pm_pe_unit_per_second");
  if (key === "requests") return t("pm_pe_unit_per_request");
  return METERS[key].per === 1_000_000 ? t("pm_pe_unit_per_million_tokens") : "";
}

/** 免费额度的计数单位（按计费项的计数单位，不是单价的单位）。 */
export function meterCountUnit(t: TFunction, key: string): string {
  if (key === "output_images" || key === "input_images") return t("pm_pe_count_image");
  if (key === "output_seconds" || key === "input_video_seconds") return t("pm_pe_count_second");
  if (key === "characters") return t("pm_pe_count_character");
  if (key === "requests") return t("pm_pe_count_request");
  return t("pm_pe_count_token");
}

export function dimensionName(t: TFunction, dim: string): string {
  return isKnownDimension(dim) ? t(`pm_pe_dim_${dim}`) : dim;
}

export function currencySymbol(t: TFunction, currency: string): string {
  return currency === "USD" || currency === "CNY" ? t(`pm_pe_currency_symbol_${currency}`) : currency;
}

/** 上下文档的下界，按从小到大排列：一档覆盖（下界, 下一档下界]。 */
export function tierBounds(meter: PriceMeter, dim: string): number[] {
  const values = [...meter.rows.map((r) => r.when[dim]), meter.defaults?.[dim]].filter(
    (v): v is number => typeof v === "number",
  );
  return [...new Set(values)].sort((a, b) => a - b);
}

function number(value: number): string {
  return value.toLocaleString("en");
}

function contextTierText(t: TFunction, lower: number, bounds: number[]): string {
  const upper = bounds.find((bound) => bound > lower);
  if (lower === 0) {
    return upper === undefined ? t("pm_pe_context_only") : t("pm_pe_context_first", { upper: number(upper) });
  }
  return upper === undefined
    ? t("pm_pe_context_last", { lower: number(lower) })
    : t("pm_pe_context_middle", { lower: number(lower), upper: number(upper) });
}

/** 一个条件取值的文字，如「720p」「有声」「标准」「≤ 200,000 token」；`undefined` 是「不限」。 */
export function conditionValueText(t: TFunction, dim: string, value: DimValue | undefined, meter: PriceMeter): string {
  if (value === undefined) return t("pm_pe_condition_any");
  if (!isKnownDimension(dim)) return String(value);
  switch (DIMENSIONS[dim].kind) {
    case "flag":
      return t(`pm_pe_value_${dim}_${value ? "true" : "false"}`);
    case "service_tier":
      return t(`pm_pe_tier_${String(value)}`, { defaultValue: String(value) });
    case "seconds":
      return t("pm_pe_value_seconds", { value });
    case "tier":
      return typeof value === "number" ? contextTierText(t, value, tierBounds(meter, dim)) : String(value);
    case "label":
      return String(value);
  }
}

/** 条件标签的文字：布尔维度直接写取值（「有声」），其余写成「维度：取值」。 */
export function conditionText(t: TFunction, dim: string, value: DimValue | undefined, meter: PriceMeter): string {
  const text = conditionValueText(t, dim, value, meter);
  if (isKnownDimension(dim) && DIMENSIONS[dim].kind === "flag" && value !== undefined) return text;
  return t("pm_pe_condition_pair", { dimension: dimensionName(t, dim), value: text });
}

/** 一档的条件文字，如「分辨率：720p，有声」；不分条件的计费项为空串。 */
export function rowConditionText(t: TFunction, meter: PriceMeter, when: Record<string, DimValue>): string {
  return meter.dims.map((dim) => conditionText(t, dim, when[dim], meter)).join(t("pm_price_separator"));
}

const PRICE_DIGITS = { minimumFractionDigits: 0, maximumFractionDigits: 6 };

/** 价目表的文字概览（官方价格对比用），如「输入 $2/百万 token（≤ 200,000 token）；输出 $12/百万 token」。 */
export function priceTableText(t: TFunction, table: PriceTable): string {
  const symbol = currencySymbol(t, table.currency);
  return table.meters
    .map((meter) => {
      const rows = meter.rows.map((row) => {
        const price = `${symbol}${(row.price ?? 0).toLocaleString("en", PRICE_DIGITS)}`;
        const condition = rowConditionText(t, meter, row.when);
        return condition ? `${condition} ${price}` : price;
      });
      return `${meterName(t, meter.meter)} ${rows.join(t("pm_price_separator"))}${meterUnit(t, meter.meter)}`;
    })
    .join(t("pm_pe_meter_separator"));
}

/** 维度取值的候选（标签与上下文档没有固定候选，由用户填写）。 */
export function dimensionKind(dim: string): (typeof DIMENSIONS)[DimKey]["kind"] | null {
  return isKnownDimension(dim) ? DIMENSIONS[dim].kind : null;
}
