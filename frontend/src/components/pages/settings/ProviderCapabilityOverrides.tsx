import { useEffect, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { cn } from "cn";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { CapabilityKey, CapabilityOverrides, ProviderModelCapabilities } from "@/types";
import { DurationParseError, compactRangeFormat, parseDurationInput } from "@/utils/duration_format";
import { DURATION_ERROR_KEY } from "./CustomProviderModelTable";

type Constraints = NonNullable<CapabilityOverrides["duration_constraints"]>;

const BOOL_KEYS = new Set<CapabilityKey>([
  "image_input",
  "structured_output",
  "text_to_image",
  "image_to_image",
  "text_to_video",
  "first_frame",
  "last_frame",
]);
/** 数值维度：`nullable` 的留空表示不加约束，`min` 是允许的最小值。 */
const NUMBER_KEYS: Partial<Record<CapabilityKey, { min: number; nullable: boolean; float?: boolean }>> = {
  max_output_tokens: { min: 1, nullable: false },
  max_reference_images: { min: 0, nullable: false },
  max_reference_audio_count: { min: 0, nullable: false },
  max_reference_audio_total_seconds: { min: 0, nullable: true, float: true },
  max_prompt_chars: { min: 1, nullable: true },
};
const ENUM_OPTIONS: Partial<Record<CapabilityKey, string[]>> = {
  reference_audio_mode: ["none", "direct"],
  audio_track: ["controllable", "always_on", "always_off"],
  reference_route_audio_track: ["controllable", "always_on", "always_off"],
};

function durationText(value: unknown): string {
  return Array.isArray(value) ? compactRangeFormat(value as number[]) : "";
}

function asConstraints(value: unknown): Constraints {
  return value && typeof value === "object" ? (value) : {};
}

/** 时长联动约束的分辨率键与可选分辨率按小写比较（与服务端一致）。 */
function sameResolution(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

/** 去掉时长联动约束里已不在可选分辨率中的分辨率。 */
export function withoutDroppedResolutions(constraints: unknown, resolutions: string[]): Constraints {
  const value = asConstraints(constraints);
  const byResolution = Object.fromEntries(
    Object.entries(value.by_resolution ?? {}).filter(([res]) => resolutions.some((kept) => sameResolution(kept, res))),
  );
  return { ...value, by_resolution: byResolution };
}

function scalarText(value: unknown): string {
  return typeof value === "string" || typeof value === "number" || typeof value === "boolean"
    ? String(value)
    : JSON.stringify(value);
}

/** 维度取值的文字：原值与修改后的值共用。`null` 表示不加约束。 */
export function capabilityValueText(t: TFunction, key: CapabilityKey, value: unknown): string {
  if (value === null || value === undefined) return t("pm_cap_value_unlimited");
  if (BOOL_KEYS.has(key)) return t(value ? "pm_cap_value_supported" : "pm_cap_value_unsupported");
  if (key === "durations") {
    const list = value as number[];
    return list.length ? t("pm_cap_value_seconds", { list: compactRangeFormat(list) }) : t("pm_cap_value_none");
  }
  if (key === "resolutions") {
    const list = value as string[];
    return list.length ? list.join(t("pm_cap_value_list_separator")) : t("pm_cap_value_none");
  }
  if (key === "duration_constraints") {
    const constraints = asConstraints(value);
    const parts = Object.entries(constraints.by_resolution ?? {}).map(([res, allowed]) =>
      t("pm_cap_constraint_resolution_item", { resolution: res, list: compactRangeFormat(allowed) }),
    );
    if (constraints.with_reference_images?.length) {
      parts.push(t("pm_cap_constraint_reference_item", { list: compactRangeFormat(constraints.with_reference_images) }));
    }
    return parts.length ? parts.join(t("pm_cap_value_group_separator")) : t("pm_cap_value_none");
  }
  if (ENUM_OPTIONS[key]) return t(`pm_cap_option_${key === "reference_audio_mode" ? "audio_mode" : "audio_track"}_${scalarText(value)}`);
  if (key === "max_reference_audio_total_seconds") return t("pm_cap_value_seconds", { list: scalarText(value) });
  return scalarText(value);
}

interface Props {
  capabilities: ProviderModelCapabilities;
  value: CapabilityOverrides;
  onChange: (next: CapabilityOverrides) => void;
  /** 某个维度的输入是否可以保存；输入不合法时保存栏拦下保存。 */
  onValidityChange: (key: string, valid: boolean) => void;
}

/**
 * 「这个供应商支持的参数」：每个维度一行原值与「修改」，修改后整维替换并可编辑，「恢复」即去掉这一维的覆盖。
 * 原值是没有修改时运行时采用的值（对应条目的值，对应不上条目时为回落的结果）。可选时长下方紧跟时长联动约束。
 */
export function ProviderCapabilityOverrides({ capabilities, value, onChange, onValidityChange }: Props) {
  const { t } = useTranslation("dashboard");

  const set = (key: CapabilityKey, next: unknown) => {
    const draft: Record<string, unknown> = { ...value };
    if (next === undefined) {
      delete draft[key];
      onValidityChange(key, true);
    } else {
      draft[key] = next;
    }
    // 去掉的分辨率不再出现在时长联动约束里
    if (key === "resolutions" && Array.isArray(next) && draft.duration_constraints) {
      draft.duration_constraints = withoutDroppedResolutions(draft.duration_constraints, next as string[]);
    }
    onChange(draft);
  };

  // 时长联动约束按分辨率填写：分辨率取修改后的值，没有约束时取约束里已有的分辨率
  const effectiveResolutions = (() => {
    const resolutions = "resolutions" in value ? value.resolutions : capabilities.base.resolutions;
    if (Array.isArray(resolutions)) return resolutions as string[];
    const constraints = asConstraints(value.duration_constraints ?? capabilities.base.duration_constraints);
    return Object.keys(constraints.by_resolution ?? {});
  })();

  return (
    <ul className="flex flex-col divide-y divide-border/60 rounded-lg border border-border">
      {capabilities.editable.map((key) => {
        const overridden = key in value;
        const label = t(`pm_cap_${key}`);
        const base = capabilities.base[key];
        return (
          <li
            key={key}
            className={cn("flex flex-col gap-2 px-3 py-2", key === "duration_constraints" && "pl-6", overridden && "bg-primary/7")}
          >
            <div className="flex items-center gap-2">
              <span className="w-36 shrink-0 text-sm">{label}</span>
              <span
                className={cn(
                  "min-w-0 flex-1 truncate text-sm",
                  overridden ? "text-muted-foreground line-through" : "text-subtle-foreground",
                )}
              >
                {capabilityValueText(t, key, base)}
              </span>
              {overridden ? (
                <Button variant="ghost" size="xs" aria-label={t("pm_cap_restore_named", { name: label })} onClick={() => set(key, undefined)}>
                  {t("pm_cap_restore")}
                </Button>
              ) : (
                <Button
                  variant="outline"
                  size="xs"
                  aria-label={t("pm_cap_modify_named", { name: label })}
                  onClick={() => set(key, initialOverride(key, base))}
                >
                  {t("pm_cap_modify")}
                </Button>
              )}
            </div>
            {overridden && (
              <CapabilityValueEditor
                dimension={key}
                label={label}
                value={value[key]}
                resolutions={effectiveResolutions}
                onChange={(next) => set(key, next)}
                onValidityChange={(valid) => onValidityChange(key, valid)}
              />
            )}
          </li>
        );
      })}
    </ul>
  );
}

/** 点「修改」时的起始值：原值；不加约束的维度给一个可直接编辑的起点。 */
function initialOverride(key: CapabilityKey, base: unknown): unknown {
  if (base !== null && base !== undefined) return structuredClone(base);
  if (BOOL_KEYS.has(key)) return true;
  if (key === "resolutions") return [];
  if (key === "duration_constraints") return { by_resolution: {}, with_reference_images: [] };
  const numeric = NUMBER_KEYS[key];
  if (numeric) return numeric.nullable ? null : numeric.min;
  return ENUM_OPTIONS[key]?.[0];
}

interface EditorProps {
  dimension: CapabilityKey;
  label: string;
  value: unknown;
  resolutions: string[];
  onChange: (next: unknown) => void;
  onValidityChange: (valid: boolean) => void;
}

/**
 * 一个能力维度的取值编辑器，「这个供应商支持的参数」与「模型信息」共用。文本输入只在挂载时取 `value` 作初值，
 * 外部改了取值（放弃修改、采用官方值）时由调用方换 `key` 重新挂载。
 */
export function CapabilityValueEditor({ dimension, label, value, resolutions, onChange, onValidityChange }: EditorProps) {
  const { t } = useTranslation("dashboard");
  const editLabel = t("pm_cap_edit_named", { name: label });

  if (BOOL_KEYS.has(dimension)) {
    return (
      <ToggleGroup
        aria-label={editLabel}
        variant="outline"
        size="sm"
        value={[value ? "on" : "off"]}
        onValueChange={(next: string[]) => {
          if (next[0]) onChange(next[0] === "on");
        }}
      >
        <ToggleGroupItem value="on">{t("pm_cap_value_supported")}</ToggleGroupItem>
        <ToggleGroupItem value="off">{t("pm_cap_value_unsupported")}</ToggleGroupItem>
      </ToggleGroup>
    );
  }
  const options = ENUM_OPTIONS[dimension];
  if (options) {
    const prefix = dimension === "reference_audio_mode" ? "audio_mode" : "audio_track";
    return (
      <Select<string>
        value={String(value)}
        onValueChange={(next) => next && onChange(next)}
        items={options.map((option) => ({ value: option, label: t(`pm_cap_option_${prefix}_${option}`) }))}
      >
        <SelectTrigger aria-label={editLabel} className="w-56">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {t(`pm_cap_option_${prefix}_${option}`)}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    );
  }
  if (dimension === "durations") {
    return (
      <ParsedInput
        label={editLabel}
        initial={durationText(value)}
        hint={t("pm_cap_durations_hint")}
        parse={(text) => parseDurations(t, text, true)}
        onChange={onChange}
        onValidityChange={onValidityChange}
      />
    );
  }
  if (dimension === "resolutions") {
    return (
      <ParsedInput
        label={editLabel}
        initial={Array.isArray(value) ? (value as string[]).join(", ") : ""}
        hint={t("pm_cap_list_hint")}
        parse={(text) => {
          const list = [...new Set(text.split(/[,，\s]+/).map((item) => item.trim()).filter(Boolean))];
          return list.length ? { value: list } : { error: t("pm_cap_error_required") };
        }}
        onChange={onChange}
        onValidityChange={onValidityChange}
      />
    );
  }
  if (dimension === "duration_constraints") {
    return (
      <ConstraintsEditor
        value={asConstraints(value)}
        resolutions={resolutions}
        onChange={onChange}
        onValidityChange={onValidityChange}
      />
    );
  }
  const numeric = NUMBER_KEYS[dimension];
  if (!numeric) return null;
  return (
    <ParsedInput
      label={editLabel}
      initial={value === null || value === undefined ? "" : scalarText(value)}
      inputMode={numeric.float ? "decimal" : "numeric"}
      hint={numeric.nullable ? t("pm_cap_empty_unlimited_hint") : undefined}
      parse={(text) => {
        const trimmed = text.trim();
        if (!trimmed) return numeric.nullable ? { value: null } : { error: t("pm_cap_error_required") };
        const parsed = Number(trimmed);
        const valid = Number.isFinite(parsed) && parsed >= numeric.min && (numeric.float || Number.isInteger(parsed));
        return valid ? { value: parsed } : { error: t("pm_cap_error_number", { min: numeric.min }) };
      }}
      onChange={onChange}
      onValidityChange={onValidityChange}
    />
  );
}

type ParseResult = { value: unknown; error?: undefined } | { error: string; value?: undefined };

function parseDurations(t: TFunction, text: string, required: boolean): ParseResult {
  try {
    const list = parseDurationInput(text);
    if (list === null) return required ? { error: t("pm_cap_error_required") } : { value: [] };
    return { value: list };
  } catch (e) {
    if (e instanceof DurationParseError) return { error: t(DURATION_ERROR_KEY[e.code], e.params) };
    throw e;
  }
}

interface ParsedInputProps {
  label: string;
  initial: string;
  hint?: string;
  inputMode?: "numeric" | "decimal";
  parse: (text: string) => ParseResult;
  onChange: (next: unknown) => void;
  onValidityChange: (valid: boolean) => void;
}

/** 文本输入：每次输入都解析，合法时写回修改，不合法时就地提示并让保存栏拦下保存。 */
function ParsedInput({ label, initial, hint, inputMode, parse, onChange, onValidityChange }: ParsedInputProps) {
  const [text, setText] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const hintId = useId();
  const note = error ?? hint;
  return (
    <div className="flex flex-col gap-1">
      <Input
        aria-label={label}
        aria-invalid={error !== null}
        aria-describedby={note ? hintId : undefined}
        inputMode={inputMode}
        autoComplete="off"
        className="h-8 w-64"
        value={text}
        onChange={(e) => {
          const next = e.target.value;
          setText(next);
          const result = parse(next);
          setError(result.error ?? null);
          onValidityChange(result.error === undefined);
          if (result.error === undefined) onChange(result.value);
        }}
      />
      {note && (
        <p id={hintId} className={cn("text-xs", error ? "text-warn" : "text-muted-foreground")}>
          {note}
        </p>
      )}
    </div>
  );
}

interface ConstraintsEditorProps {
  value: Constraints;
  resolutions: string[];
  onChange: (next: Constraints) => void;
  onValidityChange: (valid: boolean) => void;
}

/** 时长联动约束：每个分辨率一行可选时长，另有「使用参考图时」一行；留空表示这一行不加约束。 */
function ConstraintsEditor({ value, resolutions, onChange, onValidityChange }: ConstraintsEditorProps) {
  const { t } = useTranslation("dashboard");
  // 各行输入是否合法只用来汇总成整维的合法性，不参与渲染
  const invalid = useRef(new Set<string>());
  const markValid = (row: string, valid: boolean) => {
    if (valid) invalid.current.delete(row);
    else invalid.current.add(row);
    onValidityChange(invalid.current.size === 0);
  };
  // 删掉分辨率时它那一行随之消失：行里没改对的输入不再拦着保存
  const rowKeys = resolutions.map((resolution) => `res:${resolution}`).join("\n");
  useEffect(() => {
    const kept = new Set(rowKeys.split("\n"));
    let pruned = false;
    for (const key of invalid.current) {
      if (key !== "reference" && !kept.has(key)) {
        invalid.current.delete(key);
        pruned = true;
      }
    }
    if (pruned) onValidityChange(invalid.current.size === 0);
  }, [rowKeys, onValidityChange]);
  const byResolution = value.by_resolution ?? {};
  const allowedFor = (resolution: string) =>
    Object.entries(byResolution).find(([res]) => sameResolution(res, resolution))?.[1];

  const row = (key: string, rowLabel: string, initial: number[] | undefined, apply: (list: number[]) => Constraints) => (
    <div key={key} className="grid grid-cols-[7rem_minmax(0,1fr)] items-start gap-x-3">
      <span className="pt-1.5 text-xs text-muted-foreground">{rowLabel}</span>
      <ParsedInput
        label={t("pm_cap_constraint_row_named", { name: rowLabel })}
        initial={initial ? compactRangeFormat(initial) : ""}
        parse={(text) => parseDurations(t, text, false)}
        onChange={(list) => onChange(apply(list as number[]))}
        onValidityChange={(valid) => markValid(key, valid)}
      />
    </div>
  );

  return (
    <div className="flex flex-col gap-2">
      {resolutions.length === 0 && <p className="text-xs text-muted-foreground">{t("pm_cap_constraint_no_resolutions")}</p>}
      {resolutions.map((resolution) =>
        row(`res:${resolution}`, resolution, allowedFor(resolution), (list) => {
          const kept = Object.fromEntries(Object.entries(byResolution).filter(([res]) => !sameResolution(res, resolution)));
          return { ...value, by_resolution: list.length ? { ...kept, [resolution]: list } : kept };
        }),
      )}
      {row("reference", t("pm_cap_constraint_reference"), value.with_reference_images, (list) => ({
        ...value,
        with_reference_images: list,
      }))}
      <p className="text-xs text-muted-foreground">{t("pm_cap_constraint_row_hint")}</p>
    </div>
  );
}
