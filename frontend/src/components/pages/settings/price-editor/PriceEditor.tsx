import { useId, useState, type ReactNode } from "react";
import { Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupInput, InputGroupText } from "@/components/ui/input-group";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { MediaType } from "@/types/custom-provider";
import {
  conditionText,
  conditionValueText,
  currencySymbol,
  dimensionKind,
  dimensionName,
  meterCountUnit,
  meterName,
  meterUnit,
  priceTableText,
  rowConditionText,
} from "./priceLabels";
import {
  addableDimensions,
  addableMeters,
  enumerableValues,
  isKnownDimension,
  isKnownMeter,
  priceIssues,
  samePrice,
  withCjkDouble,
  withDefault,
  withDimension,
  withFreeAllowance,
  withMeter,
  withMeterChanged,
  withoutMeter,
  withoutRow,
  withRow,
  withRowCondition,
  withRowPrice,
  type DimValue,
  type PriceIssue,
  type PriceMeter,
  type PriceTable,
} from "./priceTable";
import { CJK_DOUBLE_METER, METERS, PRICE_CURRENCIES } from "./priceVocabulary.generated";

export interface PriceEditorProps {
  /** 正在编辑的价目表；没有计费项时就是「不填价格」。 */
  value: PriceTable;
  onChange: (next: PriceTable) => void;
  /** 条目的媒体类型：「添加计费项」与「按条件分价」只列出这个媒体类型可用的选项。 */
  mediaType: MediaType;
  /** 条目声明的分辨率，作为分辨率条件的候选。 */
  resolutions?: readonly string[];
  /** 官方价目表，条目被改过时用来对比；币种与官方不同时不显示对比。 */
  official?: PriceTable | null;
}

/**
 * 价格编辑器：每个计费项一张卡片，不分条件时一行单价，分条件时每档一行「条件 → 单价」。
 * 受控组件，`value` 与 `onChange` 是价目表本身；保存时用 `priceForSave` 转成接口形状，
 * 用 `isPriceTableValid` 判断能否保存（编辑器会在出问题的档上提示原因）。
 */
export function PriceEditor({ value, onChange, mediaType, resolutions = [], official = null }: PriceEditorProps) {
  const { t } = useTranslation("dashboard");
  const currencyLabelId = useId();
  const issues = priceIssues(value);
  const addable = addableMeters(value, mediaType);
  const showOfficial = official !== null && official.currency === value.currency && !samePrice(value, official);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-3">
        <span id={currencyLabelId} className="text-sm text-muted-foreground">
          {t("pm_pe_currency")}
        </span>
        <ToggleGroup
          aria-labelledby={currencyLabelId}
          variant="outline"
          size="sm"
          value={[value.currency]}
          onValueChange={(next: string[]) => {
            if (next[0]) onChange({ ...value, currency: next[0] });
          }}
        >
          {PRICE_CURRENCIES.map((currency) => (
            <ToggleGroupItem key={currency} value={currency}>
              {t(`pm_pe_currency_${currency}`)}
            </ToggleGroupItem>
          ))}
        </ToggleGroup>
      </div>

      {value.meters.length === 0 && <p className="text-sm text-muted-foreground">{t("pm_pe_unpriced")}</p>}

      {value.meters.map((meter) => (
        <MeterCard
          key={meter.meter}
          meter={meter}
          currency={value.currency}
          mediaType={mediaType}
          resolutions={resolutions}
          issues={issues.filter((issue) => issue.meter === meter.meter)}
          onChange={(next) => onChange(withMeterChanged(value, meter.meter, () => next))}
          onDelete={() => onChange(withoutMeter(value, meter.meter))}
        />
      ))}

      <div>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="outline" size="sm" disabled={addable.length === 0} />}>
            <Plus aria-hidden data-icon="inline-start" />
            {t("pm_pe_add_meter")}
          </DropdownMenuTrigger>
          <DropdownMenuContent className="w-56">
            {addable.map((key) => (
              <DropdownMenuItem key={key} onClick={() => onChange(withMeter(value, key))}>
                {meterName(t, key)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
      </div>

      {showOfficial && (
        <p className="flex flex-wrap items-center gap-x-1 text-xs text-muted-foreground">
          <span className="min-w-0 wrap-break-word">
            {t("pm_pe_official")}
            <span className="text-subtle-foreground">{priceTableText(t, official)}</span>
          </span>
          <Button variant="link" size="xs" onClick={() => onChange(structuredClone(official))}>
            {t("pm_pe_adopt_official")}
          </Button>
        </p>
      )}
    </div>
  );
}

interface MeterCardProps {
  meter: PriceMeter;
  currency: string;
  mediaType: MediaType;
  resolutions: readonly string[];
  issues: PriceIssue[];
  onChange: (next: PriceMeter) => void;
  onDelete: () => void;
}

function MeterCard({ meter, currency, mediaType, resolutions, issues, onChange, onDelete }: MeterCardProps) {
  const { t } = useTranslation("dashboard");
  const name = meterName(t, meter.meter);
  const unit = meterUnit(t, meter.meter);
  const symbol = currencySymbol(t, currency);
  const conditional = meter.dims.length > 0;
  const addableDims = addableDimensions(meter, mediaType);
  const canAddAllowance = isKnownMeter(meter.meter) && METERS[meter.meter].kind === "count" && meter.meter !== "requests";

  return (
    <section aria-label={name} className="flex flex-col gap-3 rounded-lg border border-border bg-background p-3">
      <header className="flex items-center gap-2">
        <h4 className="min-w-0 flex-1 text-sm font-medium wrap-break-word">{name}</h4>
        <Button variant="ghost" size="icon-xs" aria-label={t("pm_pe_delete_meter", { name })} onClick={onDelete}>
          <Trash2 aria-hidden />
        </Button>
      </header>

      <ul className="flex flex-col gap-2">
        {meter.rows.map((row, index) => {
          const condition = rowConditionText(t, meter, row.when);
          const priceLabel = condition
            ? t("pm_pe_price_label_row", { name, condition })
            : t("pm_pe_price_label", { name });
          const rowIssues = issues.filter((issue) => "row" in issue && issue.row === index);
          return (
            <li key={index} className="flex flex-col gap-1">
              <div className="flex flex-wrap items-center gap-2">
                {meter.dims.map((dim) => (
                  <ConditionTag
                    key={dim}
                    meter={meter}
                    dim={dim}
                    value={row.when[dim]}
                    resolutions={resolutions}
                    onChange={(next) => onChange(withRowCondition(meter, index, dim, next))}
                  />
                ))}
                {conditional && (
                  <span aria-hidden className="text-muted-foreground">
                    →
                  </span>
                )}
                <NumberField
                  value={row.price}
                  onChange={(price) => onChange(withRowPrice(meter, index, price))}
                  label={priceLabel}
                  prefix={symbol}
                  suffix={unit}
                  invalid={rowIssues.some((issue) => issue.kind === "missing_price")}
                  className="w-44"
                />
                {conditional && meter.rows.length > 1 && (
                  <Button
                    variant="ghost"
                    size="icon-xs"
                    aria-label={t("pm_pe_delete_row", { condition })}
                    onClick={() => onChange(withoutRow(meter, index))}
                  >
                    <Trash2 aria-hidden />
                  </Button>
                )}
              </div>
              {rowIssues.map((issue) => (
                <p key={issue.kind} role="alert" className="text-xs text-warn">
                  {issue.kind === "overlapping_rows" ? t("pm_pe_rows_overlap") : t("pm_pe_price_missing")}
                </p>
              ))}
            </li>
          );
        })}
      </ul>

      {conditional && (
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1.5 text-xs text-muted-foreground">
          <span>{t("pm_pe_defaults")}</span>
          {meter.dims.map((dim) => (
            <ConditionTag
              key={dim}
              meter={meter}
              dim={dim}
              value={meter.defaults?.[dim]}
              resolutions={resolutions}
              anyKey="pm_pe_condition_unset"
              onChange={(next) => onChange(withDefault(meter, dim, next))}
            />
          ))}
        </div>
      )}

      {meter.meter === CJK_DOUBLE_METER && (
        <SwitchRow
          label={t("pm_pe_cjk_double")}
          checked={meter.cjk_double === true}
          onChange={(on) => onChange(withCjkDouble(meter, on))}
        />
      )}

      {meter.free_allowance !== undefined && (
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm text-muted-foreground">{t("pm_pe_free_allowance")}</span>
          <NumberField
            value={meter.free_allowance}
            onChange={(next) => onChange(withFreeAllowance(meter, next ?? Number.NaN))}
            label={`${name} · ${t("pm_pe_free_allowance")}`}
            suffix={meterCountUnit(t, meter.meter)}
            invalid={issues.some((issue) => issue.kind === "invalid_free_allowance")}
            className="w-36"
          />
          <Button
            variant="ghost"
            size="icon-xs"
            aria-label={t("pm_pe_free_allowance_remove", { name })}
            onClick={() => onChange(withFreeAllowance(meter, undefined))}
          >
            <Trash2 aria-hidden />
          </Button>
          {issues.some((issue) => issue.kind === "invalid_free_allowance") && (
            <p role="alert" className="w-full text-xs text-warn">
              {t("pm_pe_free_allowance_invalid")}
            </p>
          )}
        </div>
      )}

      {issues.map(
        (issue) =>
          (issue.kind === "context_tier_not_from_zero" || issue.kind === "context_tier_mismatch") && (
            <p key={issue.kind} role="alert" className="text-xs text-warn">
              {issue.kind === "context_tier_not_from_zero" ? t("pm_pe_context_tier_from_zero") : t("pm_pe_context_tier_mismatch")}
            </p>
          ),
      )}

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" size="xs" onClick={() => onChange(withRow(meter))} disabled={!conditional}>
          <Plus aria-hidden data-icon="inline-start" />
          {t("pm_pe_add_row")}
        </Button>
        <DropdownMenu>
          <DropdownMenuTrigger render={<Button variant="outline" size="xs" disabled={addableDims.length === 0} />}>
            {t("pm_pe_add_dimension")}
          </DropdownMenuTrigger>
          <DropdownMenuContent className="w-48">
            {addableDims.map((dim) => (
              <DropdownMenuItem key={dim} onClick={() => onChange(withDimension(meter, dim, resolutions))}>
                {dimensionName(t, dim)}
              </DropdownMenuItem>
            ))}
          </DropdownMenuContent>
        </DropdownMenu>
        {canAddAllowance && meter.free_allowance === undefined && (
          <Button variant="ghost" size="xs" onClick={() => onChange(withFreeAllowance(meter, 0))}>
            {t("pm_pe_free_allowance_add")}
          </Button>
        )}
      </div>
      {conditional && <p className="text-xs text-muted-foreground">{t("pm_pe_dimension_locked")}</p>}
    </section>
  );
}

function SwitchRow({ label, checked, onChange }: { label: string; checked: boolean; onChange: (on: boolean) => void }) {
  const id = useId();
  return (
    <div className="flex items-center gap-2">
      <Switch id={id} checked={checked} onCheckedChange={onChange} />
      <label htmlFor={id} className="text-sm">
        {label}
      </label>
    </div>
  );
}

// ---- 条件标签 ----

interface ConditionTagProps {
  meter: PriceMeter;
  dim: string;
  value: DimValue | undefined;
  resolutions: readonly string[];
  onChange: (next: DimValue | undefined) => void;
  /** 值为 `undefined` 时标签上写什么，缺省是「不限」。 */
  anyKey?: string;
}

/** 一个可点选的条件标签：点开选取值；布尔、服务档点选，分辨率可点选已有取值或另填，上下文档与时长填数字。 */
function ConditionTag({ meter, dim, value, resolutions, onChange, anyKey }: ConditionTagProps) {
  const { t } = useTranslation("dashboard");
  const [open, setOpen] = useState(false);
  const pick = (next: DimValue | undefined) => {
    onChange(next);
    setOpen(false);
  };
  const text =
    value === undefined && anyKey
      ? t("pm_pe_condition_pair", { dimension: dimensionName(t, dim), value: t(anyKey) })
      : conditionText(t, dim, value, meter);
  const kind = dimensionKind(dim);
  const fixed = isKnownDimension(dim) ? enumerableValues(dim) : null;
  const anyLabel = t(anyKey ?? "pm_pe_condition_any");

  const suggestions =
    kind === "label"
      ? [
          ...new Set([
            ...resolutions,
            ...meter.rows.map((r) => r.when[dim]).filter((v): v is string => typeof v === "string"),
          ]),
        ]
      : [];

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        render={
          <Button
            variant="outline"
            size="xs"
            aria-label={t("pm_pe_condition_edit", { name: text })}
          />
        }
      >
        {text}
      </PopoverTrigger>
      <PopoverContent align="start" className="w-60">
        <div className="flex flex-col gap-0.5" role="group" aria-label={dimensionName(t, dim)}>
          <OptionButton selected={value === undefined} onClick={() => pick(undefined)}>
            {anyLabel}
          </OptionButton>
          {fixed?.map((option) => (
            <OptionButton key={String(option)} selected={value === option} onClick={() => pick(option)}>
              {conditionValueText(t, dim, option, meter)}
            </OptionButton>
          ))}
          {suggestions.map((option) => (
            <OptionButton key={option} selected={value === option} onClick={() => pick(option)}>
              {option}
            </OptionButton>
          ))}
        </div>
        {(kind === "label" || kind === "tier" || kind === "seconds") && (
          <ValueForm
            // 条件换了值重新挂载，输入框回到当前值
            key={String(value)}
            kind={kind}
            initial={value}
            label={kind === "tier" ? t("pm_pe_context_lower") : kind === "seconds" ? t("pm_pe_seconds_input") : t("pm_pe_condition_custom")}
            onApply={pick}
          />
        )}
      </PopoverContent>
    </Popover>
  );
}

function OptionButton({ selected, onClick, children }: { selected: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <Button
      variant={selected ? "secondary" : "ghost"}
      size="sm"
      className="justify-start"
      aria-pressed={selected}
      onClick={onClick}
    >
      {children}
    </Button>
  );
}

/** 自由取值：分辨率等标签、上下文档的下界、时长秒数；点「确定」或回车应用。 */
function ValueForm({
  kind,
  initial,
  label,
  onApply,
}: {
  kind: "label" | "tier" | "seconds";
  initial: DimValue | undefined;
  label: string;
  onApply: (value: DimValue) => void;
}) {
  const { t } = useTranslation("dashboard");
  const id = useId();
  const [text, setText] = useState(initial === undefined ? "" : String(initial));
  const parsed = parseDimensionText(kind, text);
  return (
    <form
      className="flex flex-col gap-1.5 border-t border-border pt-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (parsed !== null) onApply(parsed);
      }}
    >
      <label htmlFor={id} className="text-xs text-muted-foreground">
        {label}
      </label>
      <div className="flex items-center gap-2">
        <Input
          id={id}
          autoComplete="off"
          inputMode={kind === "label" ? "text" : "numeric"}
          value={text}
          aria-invalid={text !== "" && parsed === null}
          onChange={(event) => setText(event.target.value)}
        />
        <Button type="submit" size="sm" disabled={parsed === null}>
          {t("pm_pe_condition_apply")}
        </Button>
      </div>
    </form>
  );
}

function parseDimensionText(kind: "label" | "tier" | "seconds", text: string): DimValue | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  if (kind === "label") return trimmed;
  const parsed = Number(trimmed);
  if (!Number.isInteger(parsed)) return null;
  return kind === "tier" ? (parsed >= 0 ? parsed : null) : parsed > 0 ? parsed : null;
}

// ---- 数字输入 ----

interface NumberFieldProps {
  value: number | null;
  onChange: (next: number | null) => void;
  label: string;
  prefix?: string;
  suffix?: string;
  invalid?: boolean;
  className?: string;
}

function parseAmount(text: string): number | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  const parsed = Number(trimmed);
  return Number.isFinite(parsed) && parsed >= 0 ? parsed : null;
}

function amountText(value: number | null): string {
  return value === null || Number.isNaN(value) ? "" : String(value);
}

/**
 * 不小于 0 的数字输入。文本只在输入时改变；外部把取值换掉（采用官方值、删除一档后后面的档上移）时跟着换，
 * 输入到一半的文字（如「0.」）不会被覆盖。取值为空或不合法时 `onChange(null)`。
 */
function NumberField({ value, onChange, label, prefix, suffix, invalid, className }: NumberFieldProps) {
  const [text, setText] = useState(amountText(value));
  const [seen, setSeen] = useState(value);
  if (!Object.is(value, seen)) {
    setSeen(value);
    if (!Object.is(parseAmount(text), value)) setText(amountText(value));
  }
  return (
    <InputGroup className={cn(className)}>
      {prefix && (
        <InputGroupAddon>
          <InputGroupText>{prefix}</InputGroupText>
        </InputGroupAddon>
      )}
      <InputGroupInput
        inputMode="decimal"
        autoComplete="off"
        aria-label={label}
        aria-invalid={invalid}
        value={text}
        onChange={(event) => {
          const next = parseAmount(event.target.value);
          setText(event.target.value);
          setSeen(next);
          onChange(next);
        }}
      />
      {suffix && (
        <InputGroupAddon align="inline-end">
          <InputGroupText>{suffix}</InputGroupText>
        </InputGroupAddon>
      )}
    </InputGroup>
  );
}
