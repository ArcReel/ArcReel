// PROTOTYPE — #3091 变体 A 的价格编辑器：透视矩阵。
// 每个计量项一块；0 维一个单价，1 维横排各取值，2 维为「行 × 列」矩阵，3 维以上平铺为行表。
// 空单元格即未定价（显示「—」），不回落到别的档（ADR 0099）。上下文档只是一个普通维度。
import { useEffect, useState } from "react";
import { Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DIM_LABEL,
  DIM_VALUES,
  METER_LABEL,
  UNIT_LABEL,
  dimValueLabel,
  type Currency,
  type DimKey,
  type DimValue,
  type Meter,
  type MeterKey,
  type MeterUnit,
  type PriceRow,
  type PriceTable,
} from "../model";
import { SimpleSelect } from "./shared";

const UNIT_OF: Record<MeterKey, MeterUnit> = {
  input_tokens: "per_1m",
  cache_read_tokens: "per_1m",
  cache_write_5m_tokens: "per_1m",
  cache_write_1h_tokens: "per_1m",
  output_tokens: "per_1m",
  image_input_tokens: "per_1m",
  image_cache_read_tokens: "per_1m",
  image_output_tokens: "per_1m",
  video_tokens: "per_1m",
  output_images: "per_item",
  input_images: "per_item",
  requests: "per_item",
  output_seconds: "per_second",
  characters: "per_10k",
};

const key = (v: DimValue | undefined) => JSON.stringify(v ?? null);
const sameWhen = (a: PriceRow["when"], b: PriceRow["when"], dims: DimKey[]) => dims.every((d) => key(a[d]) === key(b[d]));

/** 某个维度上出现过的取值，按候选表顺序排列。 */
function valuesOf(meter: Meter, dim: DimKey, extra: DimValue[] = []): DimValue[] {
  const seen = [...meter.rows.map((r) => r.when[dim] ?? null), ...extra];
  const uniq = seen.filter((v, i) => seen.findIndex((x) => key(x) === key(v)) === i);
  const order = DIM_VALUES[dim].map((v) => key(v));
  return uniq.sort((a, b) => {
    const ia = order.indexOf(key(a));
    const ib = order.indexOf(key(b));
    return (ia === -1 ? 99 : ia) - (ib === -1 ? 99 : ib);
  });
}

function PriceCell({ value, onChange, label }: { value: number | undefined; onChange: (v: number | undefined) => void; label: string }) {
  const [text, setText] = useState(value === undefined ? "" : String(value));
  useEffect(() => setText(value === undefined ? "" : String(value)), [value]);
  return (
    <Input
      aria-label={label}
      inputMode="decimal"
      value={text}
      placeholder="—"
      className="h-8 w-24 text-right tabular-nums placeholder:text-subtle-foreground"
      onChange={(e) => {
        const t = e.target.value;
        setText(t);
        if (t.trim() === "") onChange(undefined);
        else if (!Number.isNaN(Number(t))) onChange(Number(t));
      }}
    />
  );
}

interface Props {
  value: PriceTable;
  onChange: (next: PriceTable) => void;
}

export function PriceMatrix({ value, onChange }: Props) {
  const [extra, setExtra] = useState<Record<string, Partial<Record<DimKey, DimValue[]>>>>({});
  const setMeter = (i: number, next: Meter | null) =>
    onChange({ ...value, meters: next ? value.meters.map((m, j) => (j === i ? next : m)) : value.meters.filter((_, j) => j !== i) });
  const unusedMeters = (Object.keys(METER_LABEL) as MeterKey[]).filter((k) => !value.meters.some((m) => m.meter === k));

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-3">
        <span className="text-sm font-medium">币种</span>
        <SimpleSelect
          label="币种"
          value={value.currency ?? "USD"}
          options={[
            { value: "USD", label: "美元 USD" },
            { value: "CNY", label: "人民币 CNY" },
          ]}
          onChange={(v) => onChange({ ...value, currency: v as Currency })}
          className="w-32"
        />
        <span className="text-xs text-subtle-foreground">整张价目表一个币种，不做汇率换算</span>
      </div>

      {value.meters.length === 0 && (
        <p className="rounded-lg border border-dashed border-warn/40 px-4 py-3 text-sm text-warn">
          这个条目还没有价格，经它的调用会记为未定价。添加一个计量项开始定价。
        </p>
      )}

      {value.meters.map((m, i) => (
        <MeterBlock
          key={m.meter}
          meter={m}
          extra={extra[m.meter] ?? {}}
          onExtra={(dim, v) => setExtra((s) => ({ ...s, [m.meter]: { ...s[m.meter], [dim]: [...(s[m.meter]?.[dim] ?? []), v] } }))}
          onChange={(next) => setMeter(i, next)}
        />
      ))}

      {unusedMeters.length > 0 && (
        <div className="flex items-center gap-2">
          <SimpleSelect
            label="添加计量项"
            value=""
            options={[{ value: "", label: "添加计量项…" }, ...unusedMeters.map((k) => ({ value: k, label: METER_LABEL[k] }))]}
            onChange={(k) => {
              if (!k) return;
              const meter = k as MeterKey;
              onChange({ ...value, meters: [...value.meters, { meter, unit: UNIT_OF[meter], dims: [], rows: [] }] });
            }}
            className="w-44"
          />
        </div>
      )}
    </div>
  );
}

function MeterBlock({
  meter,
  extra,
  onExtra,
  onChange,
}: {
  meter: Meter;
  extra: Partial<Record<DimKey, DimValue[]>>;
  onExtra: (dim: DimKey, v: DimValue) => void;
  onChange: (next: Meter | null) => void;
}) {
  const priceAt = (when: PriceRow["when"]) => meter.rows.find((r) => sameWhen(r.when, when, meter.dims))?.price;
  const setPrice = (when: PriceRow["when"], price: number | undefined) => {
    const rest = meter.rows.filter((r) => !sameWhen(r.when, when, meter.dims));
    onChange({ ...meter, rows: price === undefined ? rest : [...rest, { when, price }] });
  };
  const unusedDims = (Object.keys(DIM_LABEL) as DimKey[]).filter((d) => !meter.dims.includes(d));

  const addDim = (dim: DimKey) => {
    const first = DIM_VALUES[dim][0];
    onChange({ ...meter, dims: [...meter.dims, dim], rows: meter.rows.map((r) => ({ ...r, when: { ...r.when, [dim]: first } })) });
  };
  const removeDim = (dim: DimKey) => {
    const dims = meter.dims.filter((d) => d !== dim);
    const rows: PriceRow[] = [];
    for (const r of meter.rows) {
      const when = { ...r.when };
      delete when[dim];
      if (!rows.some((x) => sameWhen(x.when, when, dims))) rows.push({ when, price: r.price });
    }
    onChange({ ...meter, dims, rows });
  };

  const [d0, d1] = meter.dims;
  const v0 = d0 ? valuesOf(meter, d0, extra[d0]) : [];
  const v1 = d1 ? valuesOf(meter, d1, extra[d1]) : [];
  const unitLabel = UNIT_LABEL[meter.unit];
  const cellLabel = (when: PriceRow["when"]) =>
    `${METER_LABEL[meter.meter]} ${meter.dims.map((d) => dimValueLabel(d, when[d])).join(" ")} 单价`;

  return (
    <div className="flex flex-col gap-3 rounded-lg border border-border p-4">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-medium">{METER_LABEL[meter.meter]}</span>
        <span className="text-xs text-subtle-foreground">{unitLabel}</span>
        <span className="flex-1" />
        {meter.dims.map((d) => (
          <span key={d} className="inline-flex h-6 items-center gap-1 rounded-md bg-muted px-2 text-xs text-muted-foreground">
            按{DIM_LABEL[d]}
            <button type="button" aria-label={`移除维度 ${DIM_LABEL[d]}`} className="hover:text-foreground" onClick={() => removeDim(d)}>
              <X className="size-3" />
            </button>
          </span>
        ))}
        {unusedDims.length > 0 && (
          <SimpleSelect
            label="添加维度"
            value=""
            options={[{ value: "", label: "添加维度…" }, ...unusedDims.map((d) => ({ value: d, label: DIM_LABEL[d] }))]}
            onChange={(d) => d && addDim(d as DimKey)}
            className="w-32"
          />
        )}
        <Button variant="ghost" size="icon-sm" aria-label={`删除计量项 ${METER_LABEL[meter.meter]}`} onClick={() => onChange(null)}>
          <Trash2 />
        </Button>
      </div>

      {meter.dims.length === 0 && (
        <div className="flex items-center gap-2">
          <PriceCell label={cellLabel({})} value={priceAt({})} onChange={(p) => setPrice({}, p)} />
          <span className="text-xs text-subtle-foreground">{unitLabel}</span>
        </div>
      )}

      {meter.dims.length === 1 && (
        <div className="overflow-x-auto">
          <table className="text-sm">
            <thead>
              <tr>
                {v0.map((v) => (
                  <th key={key(v)} className="px-1 pb-1.5 text-left text-xs font-normal text-muted-foreground">
                    {dimValueLabel(d0, v)}
                  </th>
                ))}
                <th className="px-1 pb-1.5">
                  <AddValue dim={d0} present={v0} onAdd={(v) => onExtra(d0, v)} />
                </th>
              </tr>
            </thead>
            <tbody>
              <tr>
                {v0.map((v) => {
                  const when = { [d0]: v };
                  return (
                    <td key={key(v)} className="px-1">
                      <PriceCell label={cellLabel(when)} value={priceAt(when)} onChange={(p) => setPrice(when, p)} />
                    </td>
                  );
                })}
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {meter.dims.length === 2 && (
        <div className="overflow-x-auto">
          <table className="text-sm">
            <thead>
              <tr>
                <th className="pr-3 pb-1.5 text-left text-xs font-normal text-subtle-foreground">
                  {DIM_LABEL[d0]} ＼ {DIM_LABEL[d1]}
                </th>
                {v1.map((v) => (
                  <th key={key(v)} className="px-1 pb-1.5 text-left text-xs font-normal text-muted-foreground">
                    {dimValueLabel(d1, v)}
                  </th>
                ))}
                <th className="px-1 pb-1.5">
                  <AddValue dim={d1} present={v1} onAdd={(v) => onExtra(d1, v)} />
                </th>
              </tr>
            </thead>
            <tbody>
              {v0.map((r) => (
                <tr key={key(r)}>
                  <th className="pr-3 text-left text-xs font-normal whitespace-nowrap text-muted-foreground">{dimValueLabel(d0, r)}</th>
                  {v1.map((c) => {
                    const when = { [d0]: r, [d1]: c };
                    return (
                      <td key={key(c)} className="px-1 py-0.5">
                        <PriceCell label={cellLabel(when)} value={priceAt(when)} onChange={(p) => setPrice(when, p)} />
                      </td>
                    );
                  })}
                </tr>
              ))}
              <tr>
                <td className="pt-1.5">
                  <AddValue dim={d0} present={v0} onAdd={(v) => onExtra(d0, v)} />
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      )}

      {meter.dims.length >= 3 && (
        <table className="text-sm">
          <thead>
            <tr>
              {meter.dims.map((d) => (
                <th key={d} className="pr-3 pb-1.5 text-left text-xs font-normal text-muted-foreground">
                  {DIM_LABEL[d]}
                </th>
              ))}
              <th className="pb-1.5 text-left text-xs font-normal text-muted-foreground">单价</th>
            </tr>
          </thead>
          <tbody>
            {meter.rows.map((row, i) => (
              <tr key={i}>
                {meter.dims.map((d) => (
                  <td key={d} className="pr-3 text-muted-foreground">
                    {dimValueLabel(d, row.when[d])}
                  </td>
                ))}
                <td className="py-0.5">
                  <PriceCell label={cellLabel(row.when)} value={row.price} onChange={(p) => setPrice(row.when, p)} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      <div className="flex items-center gap-2 text-xs text-muted-foreground">
        <span>单请求免费额度</span>
        <Input
          aria-label={`${METER_LABEL[meter.meter]} 单请求免费额度`}
          inputMode="numeric"
          className="h-7 w-24 text-right tabular-nums"
          placeholder="0"
          value={meter.freeAllowance ? String(meter.freeAllowance) : ""}
          onChange={(e) => {
            const n = Number(e.target.value);
            onChange({ ...meter, freeAllowance: e.target.value && !Number.isNaN(n) ? n : undefined });
          }}
        />
        {meter.dims.length > 0 && <span className="text-subtle-foreground">留空的单元格表示该组合未定价</span>}
      </div>
    </div>
  );
}

function AddValue({ dim, present, onAdd }: { dim: DimKey; present: DimValue[]; onAdd: (v: DimValue) => void }) {
  const candidates = DIM_VALUES[dim].filter((v) => !present.some((p) => key(p) === key(v)));
  if (!candidates.length) return null;
  return (
    <SimpleSelect
      label={`添加${DIM_LABEL[dim]}取值`}
      value=""
      options={[{ value: "", label: `+ ${DIM_LABEL[dim]}` }, ...candidates.map((v) => ({ value: key(v), label: dimValueLabel(dim, v) }))]}
      onChange={(v) => v && onAdd(JSON.parse(v) as DimValue)}
      className="w-28"
    />
  );
}

