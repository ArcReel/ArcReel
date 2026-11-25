import { useEffect, useEffectEvent, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { cn } from "cn";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type { CapabilityKey, CapabilityOverrides, ProviderModelCapabilities } from "@/types";
import { DurationParseError, compactRangeFormat, parseDurationInput } from "@/utils/duration_format";
import {
  BOOL_KEYS,
  ENUM_OPTIONS,
  asConstraints,
  capabilityValueText,
  sameResolution,
  scalarText,
  withoutDroppedResolutions,
  type Constraints,
} from "./capabilityValues";
import { DURATION_ERROR_KEY } from "./durationErrors";

const NUMBER_KEYS: Partial<Record<CapabilityKey, { min: number; nullable: boolean; float?: boolean }>> = {
  max_output_tokens: { min: 1, nullable: false },
  max_reference_images: { min: 0, nullable: false },
  max_reference_audio_count: { min: 0, nullable: false },
  max_reference_audio_total_seconds: { min: 0, nullable: true, float: true },
  max_prompt_chars: { min: 1, nullable: true },
};

function durationText(value: unknown): string {
  return Array.isArray(value) ? compactRangeFormat(value as number[]) : "";
}

interface Props {
  capabilities: ProviderModelCapabilities;
  value: CapabilityOverrides;
  onChange: (next: CapabilityOverrides) => void;
  onValidityChange: (key: string, valid: boolean) => void;
}

export function ProviderCapabilityOverrides({ capabilities, value, onChange, onValidityChange }: Props) {
  const { t } = useTranslation("dashboard");

  const set = (key: CapabilityKey, next: unknown) => {
    const draft: Record<string, unknown> = { ...value };
    if (next === undefined) {
      delete draft[key];
    } else {
      draft[key] = next;
    }
    if (key === "resolutions" && Array.isArray(next) && draft.duration_constraints) {
      draft.duration_constraints = withoutDroppedResolutions(draft.duration_constraints, next as string[]);
    }
    onChange(draft);
  };

  const effectiveResolutions = (() => {
    const resolutions = "resolutions" in value ? value.resolutions : capabilities.base.resolutions;
    if (Array.isArray(resolutions)) return resolutions as string[];
    const constraints = asConstraints(value.duration_constraints ?? capabilities.base.duration_constraints);
    return Object.keys(constraints.by_resolution ?? {});
  })();

  const editable = new Set(capabilities.editable);
  return (
    <ul className="flex flex-col divide-y divide-border/60 rounded-lg border border-border">
      {capabilities.displayed.map((key) => {
        const label = t(`pm_cap_${key}`);
        const protocol = key === "audio_switch_controllable" || key === "reference_route_audio_switch_controllable";
        if (protocol || !editable.has(key)) {
          const effective = key in capabilities.effective ? capabilities.effective[key] : capabilities.base[key];
          const text = protocol
            ? t(effective ? "pm_cap_value_supported" : "pm_cap_value_unsupported")
            : key === "durations" && capabilities.base.duration_fixed
              ? t("supported_durations_fixed_placeholder")
              : capabilityValueText(t, key, effective);
          return (
            <li key={key} className={cn("flex items-center gap-2 px-3 py-2", key === "duration_constraints" && "pl-6")}>
              <span className="w-36 shrink-0 text-sm">{label}</span>
              <span className="min-w-0 flex-1">
                <TruncatedText
                  className="text-sm text-subtle-foreground"
                  text={text}
                />
              </span>
            </li>
          );
        }
        const overridden = key in value;
        const inherits = key === "reference_route_has_audio_track" && !overridden;
        const base = inherits ? value.has_audio_track ?? capabilities.base.has_audio_track : capabilities.base[key];
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
                {inherits ? t("pm_cap_inherit_audio", { value: capabilityValueText(t, key, base) }) : capabilityValueText(t, key, base)}
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
                hint={
                  capabilities.media_type === "image" && key === "max_reference_images"
                    ? t("pm_cap_reference_images_hint")
                    : undefined
                }
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
  hint?: string;
  onChange: (next: unknown) => void;
  onValidityChange: (valid: boolean) => void;
}

export function CapabilityValueEditor({ dimension, label, value, resolutions, hint, onChange, onValidityChange }: EditorProps) {
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
        <ToggleGroupItem value="on">{capabilityValueText(t, dimension, true)}</ToggleGroupItem>
        <ToggleGroupItem value="off">{capabilityValueText(t, dimension, false)}</ToggleGroupItem>
      </ToggleGroup>
    );
  }
  const options = ENUM_OPTIONS[dimension];
  if (options) {
    return (
      <Select<string>
        value={String(value)}
        onValueChange={(next) => next && onChange(next)}
        items={options.map((option) => ({ value: option, label: t(`pm_cap_option_audio_mode_${option}`) }))}
      >
        <SelectTrigger aria-label={editLabel} className="w-56">
          <SelectValue />
        </SelectTrigger>
        <SelectContent>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {t(`pm_cap_option_audio_mode_${option}`)}
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
      hint={hint ?? (numeric.nullable ? t("pm_cap_empty_unrecorded_hint") : undefined)}
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

function ParsedInput({ label, initial, hint, inputMode, parse, onChange, onValidityChange }: ParsedInputProps) {
  const [text, setText] = useState(initial);
  const [error, setError] = useState<string | null>(null);
  const hintId = useId();
  const note = error ?? hint;
  const releaseInvalid = useEffectEvent(() => {
    if (error !== null) onValidityChange(true);
  });
  useEffect(() => () => releaseInvalid(), []);
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

function ConstraintsEditor({ value, resolutions, onChange, onValidityChange }: ConstraintsEditorProps) {
  const { t } = useTranslation("dashboard");
  const invalid = useRef(new Set<string>());
  const markValid = (row: string, valid: boolean) => {
    if (valid) invalid.current.delete(row);
    else invalid.current.add(row);
    onValidityChange(invalid.current.size === 0);
  };
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
      {row("end_frame", t("pm_cap_constraint_end_frame"), value.with_end_frame, (list) => ({
        ...value,
        with_end_frame: list,
      }))}
      <p className="text-xs text-muted-foreground">{t("pm_cap_constraint_row_hint")}</p>
    </div>
  );
}
