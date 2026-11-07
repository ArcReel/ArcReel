import type { MediaType } from "@/types/custom-provider";
import type { CatalogPriceTable, PriceDimensionValue } from "@/types/provider";
import { DIMENSIONS, METERS, PRICE_CURRENCIES, PRICE_SERVICE_TIERS } from "./priceVocabulary.generated";

// Price-table shapes (catalog format, see backend arcreel_catalog_core) and pure editing functions.

export type MeterKey = keyof typeof METERS;
export type DimKey = keyof typeof DIMENSIONS;
export type DimValue = PriceDimensionValue;
export type Currency = (typeof PRICE_CURRENCIES)[number];

/** Condition → unit-price rows; absent `when` dimensions match any value. Unfilled editing prices are `null`. */
export interface PriceRow {
  when: Record<string, DimValue>;
  price: number | null;
}

export interface PriceMeter {
  meter: string;
  dims: string[];
  rows: PriceRow[];
  /** Values used when requests lack dimension values. */
  defaults?: Record<string, DimValue>;
  /** Per-request free allowances in meter counting units. */
  free_allowance?: number;
  /** Character counts only: Han characters count as 2. */
  cjk_double?: boolean;
}

/** Price tables; editing `meters` may be empty (currency only), converted to no price table by {@link priceForSave}. */
export interface PriceTable {
  currency: string;
  meters: PriceMeter[];
}

export const DEFAULT_CURRENCY: Currency = "USD";

export function emptyPriceTable(currency: string = DEFAULT_CURRENCY): PriceTable {
  return { currency, meters: [] };
}

export function isKnownMeter(key: string): key is MeterKey {
  return key in METERS;
}

export function isKnownDimension(key: string): key is DimKey {
  return key in DIMENSIONS;
}

/** Addable meters: vocabulary entries applicable to media types, absent from tables. */
export function addableMeters(table: PriceTable, mediaType: MediaType): MeterKey[] {
  const used = new Set(table.meters.map((m) => m.meter));
  return (Object.keys(METERS) as MeterKey[]).filter(
    (key) => (METERS[key].mediaTypes as readonly string[]).includes(mediaType) && !used.has(key),
  );
}

/** Dimensions still available for conditional pricing: applicable to media types and not yet added. */
export function addableDimensions(meter: PriceMeter, mediaType: MediaType): DimKey[] {
  const used = new Set(meter.dims);
  return (Object.keys(DIMENSIONS) as DimKey[]).filter(
    (key) => (DIMENSIONS[key].mediaTypes as readonly string[]).includes(mediaType) && !used.has(key),
  );
}

/** Initial new-dimension values: context tiers start at 0, service tiers at standard; other dimensions lack reasonable defaults and require user selection. */
function initialDimensionValue(dim: DimKey, resolutions: readonly string[]): DimValue | undefined {
  switch (DIMENSIONS[dim].kind) {
    case "tier":
      return 0;
    case "service_tier":
      return PRICE_SERVICE_TIERS[0];
    case "label":
      return dim === "resolution" ? resolutions[0] : undefined;
    case "flag":
      return false;
    case "seconds":
      return undefined;
  }
}

/** Add meters: unconditional single price rows awaiting input. */
export function withMeter(table: PriceTable, key: MeterKey): PriceTable {
  const meter: PriceMeter = { meter: key, dims: [], rows: [{ when: {}, price: null }] };
  return { ...table, meters: [...table.meters, meter] };
}

export function withoutMeter(table: PriceTable, key: string): PriceTable {
  return { ...table, meters: table.meters.filter((m) => m.meter !== key) };
}

export function withMeterChanged(table: PriceTable, key: string, change: (meter: PriceMeter) => PriceMeter): PriceTable {
  return { ...table, meters: table.meters.map((m) => (m.meter === key ? change(m) : m)) };
}

/**
 * Conditional pricing adds selection dimensions to meters. Existing rows use initial dimension values, also pricing requests missing that dimension;
 * dimensions cannot later be removed; delete/readd whole cards to change conditions.
 */
export function withDimension(meter: PriceMeter, dim: DimKey, resolutions: readonly string[] = []): PriceMeter {
  const initial = initialDimensionValue(dim, resolutions);
  const rows = meter.rows.map((row) => (initial === undefined ? row : { ...row, when: { ...row.when, [dim]: initial } }));
  const defaults = initial === undefined ? meter.defaults : { ...meter.defaults, [dim]: initial };
  return { ...meter, dims: [...meter.dims, dim], rows, ...(defaults ? { defaults } : {}) };
}

/** Selectable dimension values; free-valued dimensions such as labels/context tiers return `null`. */
export function enumerableValues(dim: DimKey): DimValue[] | null {
  switch (DIMENSIONS[dim].kind) {
    case "flag":
      return [true, false];
    case "service_tier":
      return [...PRICE_SERVICE_TIERS];
    default:
      return null;
  }
}

function sameCondition(a: PriceRow["when"], b: PriceRow["when"]): boolean {
  const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
  return [...keys].every((key) => a[key] === b[key]);
}

/**
 * Add tiers inheriting last conditions, choosing unused selectable values (audio/service tiers, etc.), with context tiers using lower bounds greater than existing ones.
 * Label dimensions such as resolutions lack candidates, retaining last values for users to change.
 */
export function withRow(meter: PriceMeter): PriceMeter {
  const last = meter.rows[meter.rows.length - 1];
  const when: PriceRow["when"] = { ...last?.when };
  const taken = (candidate: PriceRow["when"]) => meter.rows.some((row) => sameCondition(row.when, candidate));
  for (const dim of [...meter.dims].reverse()) {
    if (!isKnownDimension(dim) || !taken(when)) continue;
    const values = enumerableValues(dim);
    if (values) {
      const free = values.find((value) => !taken({ ...when, [dim]: value }));
      if (free !== undefined) {
        when[dim] = free;
        break;
      }
    } else if (DIMENSIONS[dim].kind === "tier") {
      const tiers = meter.rows.map((row) => row.when[dim]).filter((v): v is number => typeof v === "number");
      when[dim] = Math.max(0, ...tiers) + 100_000;
      break;
    }
  }
  return { ...meter, rows: [...meter.rows, { when, price: null }] };
}

export function withoutRow(meter: PriceMeter, index: number): PriceMeter {
  return { ...meter, rows: meter.rows.filter((_, i) => i !== index) };
}

/** Change tier dimension values; `undefined` matches any. */
export function withRowCondition(meter: PriceMeter, index: number, dim: string, value: DimValue | undefined): PriceMeter {
  const rows = meter.rows.map((row, i) => {
    if (i !== index) return row;
    const when = { ...row.when };
    if (value === undefined) delete when[dim];
    else when[dim] = value;
    return { ...row, when };
  });
  return { ...meter, rows };
}

export function withRowPrice(meter: PriceMeter, index: number, price: number | null): PriceMeter {
  return { ...meter, rows: meter.rows.map((row, i) => (i === index ? { ...row, price } : row)) };
}

/** Change fallback values for request-missing dimensions; `undefined` means unset. */
export function withDefault(meter: PriceMeter, dim: string, value: DimValue | undefined): PriceMeter {
  const defaults = { ...meter.defaults };
  if (value === undefined) delete defaults[dim];
  else defaults[dim] = value;
  const { defaults: _previous, ...rest } = meter;
  return Object.keys(defaults).length ? { ...rest, defaults } : rest;
}

export function withFreeAllowance(meter: PriceMeter, allowance: number | undefined): PriceMeter {
  const { free_allowance: _previous, ...rest } = meter;
  return allowance === undefined ? rest : { ...rest, free_allowance: allowance };
}

export function withCjkDouble(meter: PriceMeter, on: boolean): PriceMeter {
  const { cjk_double: _previous, ...rest } = meter;
  return on ? { ...rest, cjk_double: true } : rest;
}

// ---- Validation matches backend check_entry requirements, blocking invalid saves ----

const CONTEXT_TIER: DimKey = "context_tier";

export type PriceIssue =
  | { kind: "missing_price"; meter: string; row: number }
  | { kind: "overlapping_rows"; meter: string; row: number }
  | { kind: "invalid_free_allowance"; meter: string }
  | { kind: "context_tier_not_from_zero"; meter: string }
  | { kind: "context_tier_mismatch"; meter: string };

/** Whether tier conditions can both match requests: all shared dimension values equal, matching backend rules. */
function overlaps(a: PriceRow["when"], b: PriceRow["when"]): boolean {
  return Object.keys(a)
    .filter((dim) => dim in b)
    .every((dim) => a[dim] === b[dim]);
}

export function priceIssues(table: PriceTable): PriceIssue[] {
  const issues: PriceIssue[] = [];
  for (const meter of table.meters) {
    meter.rows.forEach((row, index) => {
      if (row.price === null || !Number.isFinite(row.price) || row.price < 0) {
        issues.push({ kind: "missing_price", meter: meter.meter, row: index });
      }
      if (meter.rows.slice(0, index).some((earlier) => overlaps(row.when, earlier.when))) {
        issues.push({ kind: "overlapping_rows", meter: meter.meter, row: index });
      }
    });
    if (meter.free_allowance !== undefined && !(Number.isFinite(meter.free_allowance) && meter.free_allowance >= 0)) {
      issues.push({ kind: "invalid_free_allowance", meter: meter.meter });
    }
  }
  issues.push(...contextTierIssues(table));
  return issues;
}

/** Context tiers start at 0 with equal boundaries across meters; whole requests belong to one tier, pricing every meter there. */
function contextTierIssues(table: PriceTable): PriceIssue[] {
  const issues: PriceIssue[] = [];
  let grid: string | null = null;
  for (const meter of table.meters) {
    const tiers = [
      ...new Set(
        meter.rows.map((row) => row.when[CONTEXT_TIER]).filter((v): v is number => typeof v === "number" && v >= 0),
      ),
    ].sort((a, b) => a - b);
    if (tiers.length === 0) continue;
    if (tiers[0] !== 0) issues.push({ kind: "context_tier_not_from_zero", meter: meter.meter });
    const key = tiers.join(",");
    if (grid === null) grid = key;
    else if (key !== grid) issues.push({ kind: "context_tier_mismatch", meter: meter.meter });
  }
  return issues;
}

export function isPriceTableValid(table: PriceTable): boolean {
  return priceIssues(table).length === 0;
}

/**
 * Saved tables: no meters means `null` (no prices, calls remain unpriced).
 * Require successful {@link isPriceTableValid} first.
 */
export function priceForSave(table: PriceTable): CatalogPriceTable | null {
  return table.meters.length === 0 ? null : (table as CatalogPriceTable);
}

/** Convert API-read tables (possibly empty) to editor tables. */
export function priceTableOf(saved: CatalogPriceTable | null | undefined): PriceTable {
  return saved ? structuredClone(saved) : emptyPriceTable();
}

/** Save-time equivalence: tables without meters equal absent tables. */
export function samePrice(a: PriceTable | null | undefined, b: PriceTable | null | undefined): boolean {
  const normalize = (table: PriceTable | null | undefined) => (table && table.meters.length > 0 ? table : null);
  return JSON.stringify(normalize(a)) === JSON.stringify(normalize(b));
}
