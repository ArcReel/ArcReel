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


const USAGE_NAME_KEY: Partial<Record<MeterKey, string>> = {
  uncached_input_tokens: "usage_field_input_tokens",
  cache_read_tokens: "usage_field_cache_read_tokens",
  output_tokens: "usage_field_output_tokens",
};

const PLAIN_NAME_KEY: Partial<Record<MeterKey, string>> = {
  output_images: "pm_pe_meter_output_images",
  input_images: "pm_pe_meter_input_images",
  output_seconds: "pm_pe_meter_output_seconds",
  video_tokens: "pm_pe_meter_video_tokens",
  input_video_seconds: "pm_pe_meter_input_video_seconds",
  characters: "pm_pe_meter_characters",
  requests: "pm_pe_meter_requests",
};

const QUALIFIED_NAME: Partial<Record<MeterKey, { base: string; qualifier: string }>> = {
  cache_write_5m_tokens: { base: "usage_field_cache_write_tokens", qualifier: "usage_field_cache_write_5m_tokens" },
  cache_write_1h_tokens: { base: "usage_field_cache_write_tokens", qualifier: "usage_field_cache_write_1h_tokens" },
  audio_input_tokens: { base: "usage_field_input_tokens", qualifier: "pm_pe_qualifier_audio" },
  uncached_image_input_tokens: { base: "usage_field_input_tokens", qualifier: "pm_pe_qualifier_image" },
  image_cache_read_tokens: { base: "usage_field_cache_read_tokens", qualifier: "pm_pe_qualifier_image" },
  image_output_tokens: { base: "usage_field_output_tokens", qualifier: "pm_pe_qualifier_image" },
  audio_output_tokens: { base: "usage_field_output_tokens", qualifier: "pm_pe_qualifier_audio" },
};

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

export function meterUnit(t: TFunction, key: string): string {
  if (!isKnownMeter(key)) return "";
  if (key === "characters") return t("pm_pe_unit_per_10k_characters");
  if (key === "output_images" || key === "input_images") return t("pm_pe_unit_per_image");
  if (key === "output_seconds" || key === "input_video_seconds") return t("pm_pe_unit_per_second");
  if (key === "requests") return t("pm_pe_unit_per_request");
  return METERS[key].per === 1_000_000 ? t("pm_pe_unit_per_million_tokens") : "";
}

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

export function conditionText(t: TFunction, dim: string, value: DimValue | undefined, meter: PriceMeter): string {
  const text = conditionValueText(t, dim, value, meter);
  if (isKnownDimension(dim) && DIMENSIONS[dim].kind === "flag" && value !== undefined) return text;
  return t("pm_pe_condition_pair", { dimension: dimensionName(t, dim), value: text });
}

export function rowConditionText(t: TFunction, meter: PriceMeter, when: Record<string, DimValue>): string {
  return meter.dims.map((dim) => conditionText(t, dim, when[dim], meter)).join(t("pm_price_separator"));
}

const PRICE_DIGITS = { minimumFractionDigits: 0, maximumFractionDigits: 6 };

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

export function dimensionKind(dim: string): (typeof DIMENSIONS)[DimKey]["kind"] | null {
  return isKnownDimension(dim) ? DIMENSIONS[dim].kind : null;
}
