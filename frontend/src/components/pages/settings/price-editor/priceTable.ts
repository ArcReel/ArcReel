import type { MediaType } from "@/types/custom-provider";
import type { CatalogPriceTable, PriceDimensionValue } from "@/types/provider";
import { DIMENSIONS, METERS, PRICE_CURRENCIES, PRICE_SERVICE_TIERS } from "./priceVocabulary.generated";

// 价目表的数据形状（目录数据的格式，见后端 arcreel_catalog_core）与编辑它的纯函数。

export type MeterKey = keyof typeof METERS;
export type DimKey = keyof typeof DIMENSIONS;
export type DimValue = PriceDimensionValue;
export type Currency = (typeof PRICE_CURRENCIES)[number];

/** 一行「条件 → 单价」；`when` 缺省的维度表示任意取值。编辑中尚未填写的单价为 `null`。 */
export interface PriceRow {
  when: Record<string, DimValue>;
  price: number | null;
}

export interface PriceMeter {
  meter: string;
  dims: string[];
  rows: PriceRow[];
  /** 请求缺少某个维度取值时使用的取值。 */
  defaults?: Record<string, DimValue>;
  /** 每次请求的免费额度，按计量项的计数单位计。 */
  free_allowance?: number;
  /** 只用于字符数：汉字计 2 个字符。 */
  cjk_double?: boolean;
}

/** 价目表。编辑过程中 `meters` 可以为空（只选了币种）；保存时用 {@link priceForSave} 转成「没有价目表」。 */
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

/** 这个媒体类型可以添加的计费项：词表里适用于它的，且价目表里还没有。 */
export function addableMeters(table: PriceTable, mediaType: MediaType): MeterKey[] {
  const used = new Set(table.meters.map((m) => m.meter));
  return (Object.keys(METERS) as MeterKey[]).filter(
    (key) => (METERS[key].mediaTypes as readonly string[]).includes(mediaType) && !used.has(key),
  );
}

/** 这个计费项还能「按条件分价」的选择维度：适用于该媒体类型，且没有加过。 */
export function addableDimensions(meter: PriceMeter, mediaType: MediaType): DimKey[] {
  return (Object.keys(DIMENSIONS) as DimKey[]).filter(
    (key) => (DIMENSIONS[key].mediaTypes as readonly string[]).includes(mediaType) && !meter.dims.includes(key),
  );
}

/** 新加的维度先取的值：上下文档从首档（0）起，服务档先取标准；其余没有合理的起点，由用户点选。 */
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

/** 添加一个计费项：不分条件，一行单价（待填写）。 */
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
 * 按条件分价：给计费项加一个选择维度。已有的行都取这个维度的起始值，请求缺少这个维度时也按它计价；
 * 加上之后不能再移除，要换条件就删除整张卡片重新添加。
 */
export function withDimension(meter: PriceMeter, dim: DimKey, resolutions: readonly string[] = []): PriceMeter {
  const initial = initialDimensionValue(dim, resolutions);
  const rows = meter.rows.map((row) => (initial === undefined ? row : { ...row, when: { ...row.when, [dim]: initial } }));
  const defaults = initial === undefined ? meter.defaults : { ...meter.defaults, [dim]: initial };
  return { ...meter, dims: [...meter.dims, dim], rows, ...(defaults ? { defaults } : {}) };
}

/** 一个维度可点选的取值；标签和上下文档这类自由取值的维度返回 `null`。 */
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
 * 添加一档：沿用最后一档的条件，再在可点选的维度（有声、服务档等）上换一个还没出现过的取值，上下文档取比已有更大的下界。
 * 标签类维度（分辨率等）没有候选，沿用最后一档的取值，由用户点选修改。
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

/** 修改某一档某个维度的取值；`undefined` 表示任意。 */
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

/** 修改请求缺少某个维度取值时使用的取值；`undefined` 表示不设。 */
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

// ---- 校验：与后端 check_entry 对价目表的要求一致，保存前拦下 ----

export type PriceIssue =
  | { kind: "missing_price"; meter: string; row: number }
  | { kind: "overlapping_rows"; meter: string; row: number }
  | { kind: "invalid_free_allowance"; meter: string };

/** 两档的条件能否同时命中一个请求：共同列出的维度取值都相同（后端同一规则）。 */
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
  return issues;
}

export function isPriceTableValid(table: PriceTable): boolean {
  return priceIssues(table).length === 0;
}

/**
 * 保存用的价目表：没有任何计费项时是 `null`（不填价格，调用费用记为未定价）。
 * 价目表必须先通过 {@link isPriceTableValid}。
 */
export function priceForSave(table: PriceTable): CatalogPriceTable | null {
  return table.meters.length === 0 ? null : (table as CatalogPriceTable);
}

/** 从接口读到的价目表（可能为空）到编辑用的价目表。 */
export function priceTableOf(saved: CatalogPriceTable | null | undefined): PriceTable {
  return saved ? structuredClone(saved) : emptyPriceTable();
}

/** 两份价目表在保存时是否等价：没有计费项的价目表与「没有价目表」等价。 */
export function samePrice(a: PriceTable | null | undefined, b: PriceTable | null | undefined): boolean {
  const normalize = (table: PriceTable | null | undefined) => (table && table.meters.length > 0 ? table : null);
  return JSON.stringify(normalize(a)) === JSON.stringify(normalize(b));
}
