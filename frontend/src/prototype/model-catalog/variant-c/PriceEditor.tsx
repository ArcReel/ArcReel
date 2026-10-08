// PROTOTYPE — #3091 变体 C 价目表：正文里只读、清楚；编辑放进 Dialog，按计量项分 Tab，每个 Tab 一张行表。
import { useState } from "react";
import { Plus, Trash2, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogBody, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  DIM_LABEL,
  DIM_VALUES,
  METER_LABEL,
  UNIT_LABEL,
  dimValueLabel,
  money,
  type Currency,
  type DimKey,
  type DimValue,
  type Meter,
  type MeterKey,
  type MeterUnit,
  type PriceTable,
} from "../model";
import { SimpleSelect } from "./shared";

// ---------- 只读视图 ----------

/** 同一组维度的计量项合成一张表；一维时列是维度取值，二维时每个计量项单独透视成「行 × 列」。 */
export function PriceTableView({ price }: { price: PriceTable }) {
  if (!price.meters.length) {
    return (
      <div className="rounded-lg border border-dashed border-border px-4 py-5 text-sm text-muted-foreground">
        这个条目还没有价目表，经它的调用都记为未定价。
      </div>
    );
  }
  const groups = new Map<string, Meter[]>();
  for (const m of price.meters) {
    const key = m.dims.join("+");
    groups.set(key, [...(groups.get(key) ?? []), m]);
  }
  return (
    <div className="flex flex-col gap-4">
      {[...groups.entries()].map(([key, meters]) => {
        const dims = meters[0].dims;
        if (dims.length <= 1) return <FlatTable key={key} meters={meters} dim={dims[0]} currency={price.currency} />;
        return meters.map((m) => <PivotTable key={`${key}:${m.meter}`} meter={m} currency={price.currency} />);
      })}
      <p className="text-xs text-muted-foreground">币种 {price.currency ?? "未设置"}，不做汇率换算。实际费用 = 倍率 × Σ 计量项用量 × 单价。</p>
    </div>
  );
}

/** 列头：有声 / 无声本身就说清了维度，其余维度带上维度名。 */
function dimHeader(dim: DimKey, v: DimValue): string {
  return dim === "audio" ? dimValueLabel(dim, v) : `${DIM_LABEL[dim]} ${dimValueLabel(dim, v)}`;
}

function uniq<T>(xs: T[]): T[] {
  return [...new Map(xs.map((x) => [JSON.stringify(x), x])).values()];
}

function FlatTable({ meters, dim, currency }: { meters: Meter[]; dim?: DimKey; currency: Currency | null }) {
  const cols: DimValue[] = dim ? uniq(meters.flatMap((m) => m.rows.map((r) => r.when[dim] ?? null))) : [null];
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="w-44">计量项</TableHead>
            {cols.map((c) => (
              <TableHead key={String(c)} className="text-right">
                {dim ? dimHeader(dim, c) : "单价"}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {meters.map((m) => (
            <TableRow key={m.meter} className="hover:bg-transparent">
              <TableCell>
                <span className="flex flex-col">
                  <span>{METER_LABEL[m.meter]}</span>
                  <span className="text-xs text-muted-foreground">
                    {UNIT_LABEL[m.unit]}
                    {m.freeAllowance ? `，每次免费 ${m.freeAllowance}` : ""}
                  </span>
                </span>
              </TableCell>
              {cols.map((c) => {
                const row = m.rows.find((r) => (dim ? (r.when[dim] ?? null) === c : true));
                return (
                  <TableCell key={String(c)} className="text-right tabular-nums">
                    {row ? money(row.price, currency) : <span className="text-muted-foreground">—</span>}
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

function PivotTable({ meter, currency }: { meter: Meter; currency: Currency | null }) {
  const [d0, d1, ...rest] = meter.dims;
  const rowVals = uniq(meter.rows.map((r) => r.when[d0] ?? null));
  const colVals = uniq(meter.rows.map((r) => r.when[d1] ?? null));
  return (
    <div className="overflow-hidden rounded-lg border border-border">
      <div className="flex items-baseline gap-2 border-b border-border bg-muted/30 px-3 py-2">
        <span className="text-sm">{METER_LABEL[meter.meter]}</span>
        <span className="text-xs text-muted-foreground">{UNIT_LABEL[meter.unit]}</span>
        {rest.length > 0 && <span className="text-xs text-warn">另有 {rest.length} 个维度未展开</span>}
      </div>
      <Table>
        <TableHeader>
          <TableRow className="hover:bg-transparent">
            <TableHead className="w-44">{DIM_LABEL[d0]}</TableHead>
            {colVals.map((c) => (
              <TableHead key={String(c)} className="text-right">
                {dimHeader(d1, c)}
              </TableHead>
            ))}
          </TableRow>
        </TableHeader>
        <TableBody>
          {rowVals.map((rv) => (
            <TableRow key={String(rv)} className="hover:bg-transparent">
              <TableCell>{dimValueLabel(d0, rv)}</TableCell>
              {colVals.map((cv) => {
                const row = meter.rows.find((r) => (r.when[d0] ?? null) === rv && (r.when[d1] ?? null) === cv);
                return (
                  <TableCell key={String(cv)} className="text-right tabular-nums">
                    {row ? money(row.price, currency) : <span className="text-muted-foreground">—</span>}
                  </TableCell>
                );
              })}
            </TableRow>
          ))}
        </TableBody>
      </Table>
    </div>
  );
}

// ---------- 编辑 Dialog ----------

const ALL_METERS = Object.keys(METER_LABEL) as MeterKey[];
const ALL_DIMS = Object.keys(DIM_LABEL) as DimKey[];
const DEFAULT_UNIT: Partial<Record<MeterKey, MeterUnit>> = {
  output_images: "per_item",
  input_images: "per_item",
  requests: "per_item",
  output_seconds: "per_second",
  characters: "per_10k",
};

const encode = (v: DimValue) => (v === null || v === undefined ? "__any" : `${typeof v}:${String(v)}`);
const decode = (s: string): DimValue => {
  if (s === "__any") return null;
  const [t, ...rest] = s.split(":");
  const raw = rest.join(":");
  if (t === "boolean") return raw === "true";
  if (t === "number") return Number(raw);
  return raw;
};

export function PriceDialog({ open, onOpenChange, value, onApply, title }: { open: boolean; onOpenChange: (v: boolean) => void; value: PriceTable; onApply: (p: PriceTable) => void; title: string }) {
  const [draft, setDraft] = useState<PriceTable>(value);
  const [tab, setTab] = useState<string>(value.meters[0]?.meter ?? "");
  const [lastOpen, setLastOpen] = useState(open);
  if (open !== lastOpen) {
    setLastOpen(open);
    if (open) {
      setDraft(structuredClone(value));
      setTab(value.meters[0]?.meter ?? "");
    }
  }

  const setMeter = (key: MeterKey, fn: (m: Meter) => Meter) => setDraft((d) => ({ ...d, meters: d.meters.map((m) => (m.meter === key ? fn(m) : m)) }));
  const unused = ALL_METERS.filter((k) => !draft.meters.some((m) => m.meter === k));

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size="xl">
        <DialogHeader>
          <DialogTitle>编辑价格 · {title}</DialogTitle>
          <DialogDescription>每个计量项按选择维度给单价；同一请求只命中一行。改完点「完成」，再用页面底部的保存栏保存。</DialogDescription>
        </DialogHeader>
        <DialogBody className="flex flex-col gap-4">
          <div className="flex items-center gap-3">
            <span className="text-sm text-subtle-foreground">币种</span>
            <SimpleSelect<Currency>
              className="w-40"
              value={draft.currency}
              onChange={(c) => setDraft((d) => ({ ...d, currency: c }))}
              items={[
                { value: "USD", label: "USD 美元" },
                { value: "CNY", label: "CNY 人民币" },
              ]}
            />
            <span className="text-xs text-muted-foreground">取模型厂商主站的币种，不做换算。</span>
          </div>

          <Tabs value={tab} onValueChange={(v: string) => setTab(v)}>
            <div className="flex items-center gap-2">
              <TabsList className="min-w-0 flex-wrap justify-start">
                {draft.meters.map((m) => (
                  <TabsTrigger key={m.meter} value={m.meter}>
                    {METER_LABEL[m.meter]}
                  </TabsTrigger>
                ))}
              </TabsList>
              {unused.length > 0 && (
                <SimpleSelect<MeterKey>
                  className="w-40 shrink-0"
                  size="sm"
                  value={null}
                  placeholder="添加计量项"
                  onChange={(k) => {
                    setDraft((d) => ({ ...d, meters: [...d.meters, { meter: k, unit: DEFAULT_UNIT[k] ?? "per_1m", dims: [], rows: [{ when: {}, price: 0 }] }] }));
                    setTab(k);
                  }}
                  items={unused.map((k) => ({ value: k, label: METER_LABEL[k] }))}
                />
              )}
            </div>
            {draft.meters.length === 0 && <p className="py-6 text-sm text-muted-foreground">还没有计量项。先添加一个，例如「未缓存输入」或「输出秒数」。</p>}
            {draft.meters.map((m) => (
              <TabsContent key={m.meter} value={m.meter} className="pt-3">
                <MeterEditor
                  meter={m}
                  onChange={(fn) => setMeter(m.meter, fn)}
                  onRemove={() => {
                    setDraft((d) => ({ ...d, meters: d.meters.filter((x) => x.meter !== m.meter) }));
                    setTab(draft.meters.find((x) => x.meter !== m.meter)?.meter ?? "");
                  }}
                />
              </TabsContent>
            ))}
          </Tabs>
        </DialogBody>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={() => {
              onApply(draft);
              onOpenChange(false);
            }}
          >
            完成
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function MeterEditor({ meter, onChange, onRemove }: { meter: Meter; onChange: (fn: (m: Meter) => Meter) => void; onRemove: () => void }) {
  const unusedDims = ALL_DIMS.filter((d) => !meter.dims.includes(d));
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
        <label className="flex items-center gap-2 text-sm">
          <span className="text-subtle-foreground">计价单位</span>
          <SimpleSelect<MeterUnit>
            className="w-36"
            size="sm"
            value={meter.unit}
            onChange={(u) => onChange((m) => ({ ...m, unit: u }))}
            items={(Object.keys(UNIT_LABEL) as MeterUnit[]).map((u) => ({ value: u, label: UNIT_LABEL[u] }))}
          />
        </label>
        <label className="flex items-center gap-2 text-sm">
          <span className="text-subtle-foreground">每次免费额度</span>
          <Input
            type="number"
            className="h-7 w-24"
            value={meter.freeAllowance ?? ""}
            placeholder="0"
            onChange={(e) => onChange((m) => ({ ...m, freeAllowance: e.target.value ? Number(e.target.value) : undefined }))}
          />
        </label>
        <span className="flex-1" />
        <Button variant="ghost" size="sm" className="text-destructive" onClick={onRemove}>
          <Trash2 aria-hidden />
          删除这个计量项
        </Button>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm">
        <span className="text-subtle-foreground">选择维度</span>
        {meter.dims.length === 0 && <span className="text-muted-foreground">无，单一价格</span>}
        {meter.dims.map((d) => (
          <span key={d} className="inline-flex items-center gap-1 rounded-md bg-muted py-0.5 pr-1 pl-2 text-xs">
            {DIM_LABEL[d]}
            <Button
              variant="ghost"
              size="icon-xs"
              aria-label={`移除维度${DIM_LABEL[d]}`}
              onClick={() =>
                onChange((m) => ({
                  ...m,
                  dims: m.dims.filter((x) => x !== d),
                  rows: uniqRows(m.rows.map((r) => ({ ...r, when: Object.fromEntries(Object.entries(r.when).filter(([k]) => k !== d)) }))),
                }))
              }
            >
              <X aria-hidden />
            </Button>
          </span>
        ))}
        {unusedDims.length > 0 && (
          <SimpleSelect<DimKey>
            className="w-32"
            size="sm"
            value={null}
            placeholder="添加维度"
            onChange={(d) => onChange((m) => ({ ...m, dims: [...m.dims, d], rows: m.rows.map((r) => ({ ...r, when: { ...r.when, [d]: DIM_VALUES[d][0] } })) }))}
            items={unusedDims.map((d) => ({ value: d, label: DIM_LABEL[d] }))}
          />
        )}
      </div>

      <div className="overflow-hidden rounded-lg border border-border">
        <Table>
          <TableHeader>
            <TableRow className="hover:bg-transparent">
              {meter.dims.map((d) => (
                <TableHead key={d}>{DIM_LABEL[d]}</TableHead>
              ))}
              <TableHead className="w-40">单价（{UNIT_LABEL[meter.unit]}）</TableHead>
              <TableHead className="w-10" />
            </TableRow>
          </TableHeader>
          <TableBody>
            {meter.rows.map((row, i) => (
              <TableRow key={i} className="hover:bg-transparent">
                {meter.dims.map((d) => (
                  <TableCell key={d}>
                    <SimpleSelect<string>
                      size="sm"
                      className="w-32"
                      value={encode(row.when[d] ?? null)}
                      onChange={(v) => onChange((m) => ({ ...m, rows: m.rows.map((r, j) => (j === i ? { ...r, when: { ...r.when, [d]: decode(v) } } : r)) }))}
                      items={[{ value: "__any", label: "任意" }, ...uniq([...DIM_VALUES[d], ...(row.when[d] != null ? [row.when[d]] : [])]).map((v) => ({ value: encode(v), label: dimValueLabel(d, v) }))]}
                    />
                  </TableCell>
                ))}
                <TableCell>
                  <Input
                    type="number"
                    step="any"
                    className="h-7 w-32 tabular-nums"
                    aria-label="单价"
                    value={row.price}
                    onChange={(e) => onChange((m) => ({ ...m, rows: m.rows.map((r, j) => (j === i ? { ...r, price: Number(e.target.value) } : r)) }))}
                  />
                </TableCell>
                <TableCell>
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label="删除这一行"
                    disabled={meter.rows.length <= 1}
                    onClick={() => onChange((m) => ({ ...m, rows: m.rows.filter((_, j) => j !== i) }))}
                  >
                    <Trash2 aria-hidden />
                  </Button>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
      {meter.dims.length > 0 && (
        <Button variant="outline" size="sm" className="self-start" onClick={() => onChange((m) => ({ ...m, rows: [...m.rows, { when: { ...m.rows[m.rows.length - 1]?.when }, price: 0 }] }))}>
          <Plus aria-hidden />
          添加一行
        </Button>
      )}
    </div>
  );
}

function uniqRows(rows: Meter["rows"]): Meter["rows"] {
  const seen = new Set<string>();
  return rows.filter((r) => {
    const k = JSON.stringify(r.when);
    if (seen.has(k)) return false;
    seen.add(k);
    return true;
  });
}
