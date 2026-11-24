import type { MediaType } from "@/types/custom-provider";
import type { CatalogPriceTable, PriceDimensionValue } from "@/types/provider";
import { DIMENSIONS, METERS, PRICE_CURRENCIES, PRICE_SERVICE_TIERS } from "./priceVocabulary.generated";


export type MeterKey = keyof typeof METERS;
export type DimKey = keyof typeof DIMENSIONS;
export type DimValue = PriceDimensionValue;
export type Currency = (typeof PRICE_CURRENCIES)[number];

export interface PriceRow {
  when: Record<string, DimValue>;
  price: number | null;
}

export interface PriceMeter {
  meter: string;
  dims: string[];
  rows: PriceRow[];
  defaults?: Record<string, DimValue>;
  free_allowance?: number;
  cjk_double?: boolean;
}

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

export function addableMeters(table: PriceTable, mediaType: MediaType): MeterKey[] {
  const used = new Set(table.meters.map((m) => m.meter));
  return (Object.keys(METERS) as MeterKey[]).filter(
    (key) => (METERS[key].mediaTypes as readonly string[]).includes(mediaType) && !used.has(key),
  );
}

export function addableDimensions(meter: PriceMeter, mediaType: MediaType): DimKey[] {
  const used = new Set(meter.dims);
  return (Object.keys(DIMENSIONS) as DimKey[]).filter(
    (key) => (DIMENSIONS[key].mediaTypes as readonly string[]).includes(mediaType) && !used.has(key),
  );
}

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

export function withDimension(meter: PriceMeter, dim: DimKey, resolutions: readonly string[] = []): PriceMeter {
  const initial = initialDimensionValue(dim, resolutions);
  const rows = meter.rows.map((row) => (initial === undefined ? row : { ...row, when: { ...row.when, [dim]: initial } }));
  const defaults = initial === undefined ? meter.defaults : { ...meter.defaults, [dim]: initial };
  return { ...meter, dims: [...meter.dims, dim], rows, ...(defaults ? { defaults } : {}) };
}

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


const CONTEXT_TIER: DimKey = "context_tier";

export type PriceIssue =
  | { kind: "missing_price"; meter: string; row: number }
  | { kind: "overlapping_rows"; meter: string; row: number }
  | { kind: "invalid_free_allowance"; meter: string }
  | { kind: "context_tier_not_from_zero"; meter: string }
  | { kind: "context_tier_mismatch"; meter: string };

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

export function priceForSave(table: PriceTable): CatalogPriceTable | null {
  return table.meters.length === 0 ? null : (table as CatalogPriceTable);
}

export function priceTableOf(saved: CatalogPriceTable | null | undefined): PriceTable {
  return saved ? structuredClone(saved) : emptyPriceTable();
}

export function samePrice(a: PriceTable | null | undefined, b: PriceTable | null | undefined): boolean {
  const normalize = (table: PriceTable | null | undefined) => (table && table.meters.length > 0 ? table : null);
  return JSON.stringify(normalize(a)) === JSON.stringify(normalize(b));
}
