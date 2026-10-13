// PROTOTYPE — #3091 变体 B 的价格编辑器：每个计量项一张卡，卡内每档价格一行「条件 chips → 单价」。
// 上下文档、服务档、分辨率都只是条件 chip，不做二维矩阵。

import { Plus, Trash2, X } from "lucide-react";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import {
  DIM_LABEL,
  DIM_VALUES,
  METER_LABEL,
  UNIT_LABEL,
  UNIT_SHORT,
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
import { OfficialHint } from "./bits";

const METER_UNIT: Record<MeterKey, MeterUnit> = {
  input_tokens: "per_1m",
  cache_read_tokens: "per_1m",
  cache_write_5m_tokens: "per_1m",
  cache_write_1h_tokens: "per_1m",
  output_tokens: "per_1m",
  image_input_tokens: "per_1m",
  image_cache_read_tokens: "per_1m",
  image_output_tokens: "per_1m",
  output_images: "per_item",
  input_images: "per_item",
  output_seconds: "per_second",
  video_tokens: "per_1m",
  characters: "per_10k",
  requests: "per_item",
};

function meterText(m: Meter | undefined, cur: Currency | null): string {
  if (!m) return "没有这一计量项";
  return m.rows
    .map((r) => {
      const when = m.dims.map((d) => dimValueLabel(d, r.when[d])).join(" ");
      return `${when ? when + " " : ""}${money(r.price, cur)}${UNIT_SHORT[m.unit]}`;
    })
    .join("；");
}

interface Props {
  value: PriceTable;
  onChange: (next: PriceTable) => void;
  /** 有官方值且与当前值不同时，在卡片下方就地标注。 */
  official?: PriceTable | null;
  readOnly?: boolean;
}

export function PriceCards({ value, onChange, official, readOnly }: Props) {
  const cur = value.currency;
  const setMeter = (i: number, next: Meter | null) => {
    const meters = [...value.meters];
    if (next) meters[i] = next;
    else meters.splice(i, 1);
    onChange({ ...value, meters });
  };
  const missing = (Object.keys(METER_LABEL) as MeterKey[]).filter((k) => !value.meters.some((m) => m.meter === k));
  const officialMissing = official ? official.meters.filter((m) => !value.meters.some((x) => x.meter === m.meter)) : [];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2 text-sm">
        <span className="text-muted-foreground">币种</span>
        {(["USD", "CNY"] as Currency[]).map((c) => (
          <button
            key={c}
            type="button"
            disabled={readOnly}
            onClick={() => onChange({ ...value, currency: c })}
            className={cn(
              "h-7 rounded-md border px-2 text-xs",
              cur === c ? "border-primary/50 bg-primary/15" : "border-border text-subtle-foreground hover:bg-muted/50",
            )}
          >
            {c === "USD" ? "美元（$）" : "人民币（¥）"}
          </button>
        ))}
        {official && official.currency !== value.currency && (
          <span className="text-xs text-muted-foreground">官方币种：{official.currency === "CNY" ? "人民币" : official.currency === "USD" ? "美元" : "无"}</span>
        )}
      </div>

      {value.meters.length === 0 && (
        <p className="rounded-lg border border-dashed border-warn/50 px-3 py-3 text-sm text-warn">
          尚未设置价格。调用费用记为未定价，在使用记录中单独列出。
        </p>
      )}

      {value.meters.map((m, i) => {
        const om = official?.meters.find((x) => x.meter === m.meter);
        const differs = official && meterText(om, official.currency) !== meterText(m, cur);
        return (
          <div key={m.meter}>
            <MeterCard meter={m} currency={cur} readOnly={readOnly} onChange={(next) => setMeter(i, next)} />
            {differs && (
              <OfficialHint
                value={meterText(om, official.currency)}
                onAdopt={readOnly ? undefined : () => setMeter(i, om ? structuredClone(om) : null)}
              />
            )}
          </div>
        );
      })}

      {officialMissing.map((om) => (
        <div key={om.meter} className="rounded-lg border border-dashed border-border px-3 py-2">
          <p className="text-sm text-subtle-foreground">{METER_LABEL[om.meter]}：当前条目没有这一计量项</p>
          <OfficialHint
            value={meterText(om, official!.currency)}
            onAdopt={readOnly ? undefined : () => onChange({ ...value, meters: [...value.meters, structuredClone(om)] })}
          />
        </div>
      ))}

      {!readOnly && missing.length > 0 && (
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="outline" size="sm" className="self-start" />}>
            <Plus aria-hidden />
            添加计量项
          </DropdownMenuTrigger>
          <DropdownMenuContent>
            {missing.map((k) => (
              <DropdownMenuItem
                key={k}
                onClick={() => onChange({ ...value, meters: [...value.meters, { meter: k, unit: METER_UNIT[k], dims: [], rows: [{ when: {}, price: 0 }] }] })}
              >
                {METER_LABEL[k]}
                <span className="ml-auto text-xs text-muted-foreground">{UNIT_LABEL[METER_UNIT[k]]}</span>
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );
}

function MeterCard({ meter, currency, onChange, readOnly }: { meter: Meter; currency: Currency | null; onChange: (m: Meter | null) => void; readOnly?: boolean }) {
  const symbol = currency === "CNY" ? "¥" : currency === "USD" ? "$" : "";
  const unusedDims = (Object.keys(DIM_LABEL) as DimKey[]).filter((d) => !meter.dims.includes(d));
  const setRow = (i: number, patch: Partial<Meter["rows"][number]> | null) => {
    const rows = [...meter.rows];
    if (patch) rows[i] = { ...rows[i], ...patch };
    else rows.splice(i, 1);
    onChange({ ...meter, rows });
  };

  return (
    <section className="rounded-lg border border-border bg-card/40">
      <header className="flex items-center gap-2 border-b border-border px-3 py-2">
        <h4 className="text-sm font-medium">{METER_LABEL[meter.meter]}</h4>
        <span className="text-xs text-muted-foreground">{UNIT_LABEL[meter.unit]}</span>
        {meter.dims.length > 0 && (
          <span className="text-xs text-muted-foreground">按{meter.dims.map((d) => DIM_LABEL[d]).join("、")}分档</span>
        )}
        {!readOnly && (
          <Button variant="ghost" size="icon-xs" className="ml-auto" aria-label={`删除计量项 ${METER_LABEL[meter.meter]}`} onClick={() => onChange(null)}>
            <Trash2 aria-hidden />
          </Button>
        )}
      </header>
      <ul className="flex flex-col divide-y divide-border/60">
        {meter.rows.map((row, i) => (
          <li key={i} className="flex flex-wrap items-center gap-1.5 px-3 py-1.5">
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-1.5">
            {meter.dims.length === 0 && <span className="text-xs text-muted-foreground">不分档</span>}
            {meter.dims.map((d) => (
              <DimChip
                key={d}
                dim={d}
                value={row.when[d] ?? null}
                readOnly={readOnly}
                onChange={(v) => setRow(i, { when: { ...row.when, [d]: v } })}
              />
            ))}
            </div>
            <span aria-hidden className="px-1 text-muted-foreground">→</span>
            <div className="flex items-center gap-1">
              <span className="text-sm text-muted-foreground">{symbol}</span>
              <Input
                type="number"
                step="any"
                min={0}
                readOnly={readOnly}
                aria-label="单价"
                className="h-7 w-24 tabular-nums"
                value={row.price}
                onChange={(e) => setRow(i, { price: Number(e.target.value) })}
              />
              <span className="text-xs text-muted-foreground">{UNIT_SHORT[meter.unit]}</span>
            </div>
            {!readOnly && meter.rows.length > 1 && (
              <Button variant="ghost" size="icon-xs" aria-label="删除这一价格档" onClick={() => setRow(i, null)}>
                <X aria-hidden />
              </Button>
            )}
          </li>
        ))}
      </ul>
      {meter.freeAllowance !== undefined && (
        <div className="flex items-center gap-2 border-t border-border/60 px-3 py-1.5 text-sm">
          <span className="text-muted-foreground">每次请求的免费额度</span>
          <Input
            type="number"
            min={0}
            readOnly={readOnly}
            aria-label="单请求免费额度"
            className="h-7 w-24 tabular-nums"
            value={meter.freeAllowance}
            onChange={(e) => onChange({ ...meter, freeAllowance: Number(e.target.value) })}
          />
          <span className="text-xs text-muted-foreground">{UNIT_LABEL[meter.unit].replace("每", "")}</span>
          {!readOnly && (
            <Button variant="ghost" size="icon-xs" className="ml-auto" aria-label="删除免费额度" onClick={() => onChange({ ...meter, freeAllowance: undefined })}>
              <X aria-hidden />
            </Button>
          )}
        </div>
      )}
      {!readOnly && (
        <footer className="flex flex-wrap items-center gap-1 border-t border-border/60 px-2 py-1.5">
          <Button
            variant="ghost"
            size="xs"
            onClick={() => onChange({ ...meter, rows: [...meter.rows, structuredClone(meter.rows[meter.rows.length - 1] ?? { when: {}, price: 0 })] })}
          >
            <Plus aria-hidden />
            添加价格档
          </Button>
          {unusedDims.length > 0 && (
            <DropdownMenu>
              <DropdownMenuTrigger render={<Button variant="ghost" size="xs" />}>
                <Plus aria-hidden />
                添加分档条件
              </DropdownMenuTrigger>
              <DropdownMenuContent>
                {unusedDims.map((d) => (
                  <DropdownMenuItem
                    key={d}
                    onClick={() =>
                      onChange({ ...meter, dims: [...meter.dims, d], rows: meter.rows.map((r) => ({ ...r, when: { ...r.when, [d]: DIM_VALUES[d][0] } })) })
                    }
                  >
                    {DIM_LABEL[d]}
                  </DropdownMenuItem>
                ))}
              </DropdownMenuContent>
            </DropdownMenu>
          )}
          {meter.freeAllowance === undefined && (
            <Button variant="ghost" size="xs" onClick={() => onChange({ ...meter, freeAllowance: 0 })}>
              <Plus aria-hidden />
              添加免费额度
            </Button>
          )}
        </footer>
      )}
    </section>
  );
}

function DimChip({ dim, value, onChange, readOnly }: { dim: DimKey; value: DimValue; onChange: (v: DimValue) => void; readOnly?: boolean }) {
  const label = (
    <>
      <span className="text-muted-foreground">{DIM_LABEL[dim]}</span>
      <span>{dimValueLabel(dim, value)}</span>
    </>
  );
  const cls = "inline-flex h-6 items-center gap-1 rounded-md border border-border bg-muted/40 px-1.5 text-xs";
  if (readOnly) return <span className={cls}>{label}</span>;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger className={cn(cls, "hover:border-primary/50 hover:bg-primary/10")}>{label}</DropdownMenuTrigger>
      <DropdownMenuContent>
        {DIM_VALUES[dim].map((v) => (
          <DropdownMenuItem key={String(v)} onClick={() => onChange(v)}>
            {dimValueLabel(dim, v)}
          </DropdownMenuItem>
        ))}
        <DropdownMenuItem onClick={() => onChange(null)}>任意</DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
