import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";

import { Button } from "@/components/ui/button";
import {
  Combobox,
  ComboboxCollection,
  ComboboxContent,
  ComboboxEmpty,
  ComboboxGroup,
  ComboboxInput,
  ComboboxItem,
  ComboboxLabel,
  ComboboxList,
  ComboboxTrigger,
} from "@/components/ui/combobox";
import { ProviderIcon } from "./ProviderIcon";

interface ProviderModelSelectProps {
  value: string;
  options: string[];
  providerNames: Record<string, string>;
  modelNames?: Record<string, string>;
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  allowDefault?: boolean;
  defaultLabel?: string;
  defaultHint?: string;
  fallbackValue?: string;
  fallbackLabel?: string;
  autoValue?: string | null;
  "aria-label"?: string;
  searchable?: boolean;
  searchThreshold?: number;
  renderOptionMeta?: (fullValue: string) => React.ReactNode;
  unavailable?: UnavailableOption[];
  unavailableLabel?: string;
}

export interface UnavailableOption {
  value: string;
  reason: string;
}

type OptionGroup = {
  provider: string | null;
  items: string[];
  unavailable?: boolean;
};

function groupByProvider(options: string[]): OptionGroup[] {
  const groups = new Map<string, string[]>();
  for (const opt of options) {
    const slashIdx = opt.indexOf("/");
    if (slashIdx === -1) continue;
    const provider = opt.slice(0, slashIdx);
    groups.set(provider, [...(groups.get(provider) ?? []), opt]);
  }
  return [...groups].map(([provider, items]) => ({ provider, items }));
}

function modelIdOf(fullValue: string): string {
  return fullValue.slice(fullValue.indexOf("/") + 1);
}

function providerOf(fullValue: string): string {
  const idx = fullValue.indexOf("/");
  return idx === -1 ? fullValue : fullValue.slice(0, idx);
}

export function ProviderModelSelect({
  value,
  options,
  providerNames,
  modelNames,
  onChange,
  placeholder,
  className,
  allowDefault,
  defaultLabel,
  defaultHint,
  fallbackValue,
  fallbackLabel,
  autoValue,
  "aria-label": ariaLabel,
  searchable = true,
  searchThreshold = 6,
  renderOptionMeta,
  unavailable,
  unavailableLabel,
}: ProviderModelSelectProps) {
  const { t } = useTranslation("dashboard");
  const [query, setQuery] = useState("");

  const showSearch = searchable && options.length + (unavailable?.length ?? 0) >= searchThreshold;
  const activeQuery = showSearch ? query.trim().toLowerCase() : "";

  const groups = useMemo(() => groupByProvider(options), [options]);
  const unavailableReasons = useMemo(
    () => new Map((unavailable ?? []).map((item) => [item.value, item.reason])),
    [unavailable],
  );
  const unavailableGroup = useMemo<OptionGroup[]>(
    () => (unavailableReasons.size > 0 ? [{ provider: null, items: [...unavailableReasons.keys()], unavailable: true }] : []),
    [unavailableReasons],
  );

  const filteredGroups = useMemo(() => {
    const defaultGroup: OptionGroup[] = allowDefault && !activeQuery ? [{ provider: null, items: [""] }] : [];
    if (!activeQuery) return [...defaultGroup, ...groups, ...unavailableGroup];
    const providerMatches = (provider: string) => (providerNames[provider] || provider).toLowerCase().includes(activeQuery);
    const modelMatches = (item: string) => {
      if (modelIdOf(item).toLowerCase().includes(activeQuery)) return true;
      const label = modelNames?.[item];
      return !!label && label.toLowerCase().includes(activeQuery);
    };
    const out: OptionGroup[] = [];
    for (const group of groups) {
      if (providerMatches(group.provider!)) {
        out.push(group);
        continue;
      }
      const items = group.items.filter(modelMatches);
      if (items.length > 0) out.push({ ...group, items });
    }
    for (const group of unavailableGroup) {
      const items = group.items.filter((item) => providerMatches(providerOf(item)) || modelMatches(item));
      if (items.length > 0) out.push({ ...group, items });
    }
    return out;
  }, [allowDefault, activeQuery, groups, unavailableGroup, providerNames, modelNames]);

  const allGroups = useMemo<OptionGroup[]>(
    () => [...(allowDefault ? [{ provider: null, items: [""] }] : []), ...groups, ...unavailableGroup],
    [allowDefault, groups, unavailableGroup],
  );

  const describe = (fullValue: string) => {
    const idx = fullValue.indexOf("/");
    if (idx === -1) return providerNames[fullValue] || fullValue;
    const provider = fullValue.slice(0, idx);
    return `${providerNames[provider] || provider} · ${modelNames?.[fullValue] || fullValue.slice(idx + 1)}`;
  };

  const showFallback = !value && !!fallbackValue;
  const autoText =
    autoValue === null
      ? t("auto_select_unavailable")
      : autoValue
        ? t("auto_select_current", { model: describe(autoValue) })
        : undefined;
  const displayText = value
    ? describe(value)
    : showFallback
      ? `${fallbackLabel ?? t("follow_global_default")} · ${describe(fallbackValue)}`
      : (autoText ?? placeholder ?? t("select_model_placeholder"));

  return (
    <Combobox<string>
      items={allGroups}
      filteredItems={filteredGroups}
      value={value || allowDefault ? value : null}
      onValueChange={(next) => {
        if (next !== null) onChange(next);
      }}
      inputValue={query}
      onInputValueChange={setQuery}
      itemToStringLabel={(item) => (item ? describe(item) : (defaultLabel ?? t("follow_global_default")))}
    >
      <ComboboxTrigger
        aria-label={ariaLabel}
        render={<Button variant="outline" className={cn("w-full justify-between", className)} />}
      >
        <span className={cn("min-w-0 flex-1 truncate text-left", showFallback && "text-muted-foreground")}>
          {displayText}
        </span>
      </ComboboxTrigger>
      <ComboboxContent aria-label={t("select_model_aria")}>
        {showSearch && (
          <ComboboxInput
            showTrigger={false}
            placeholder={t("search_model_placeholder")}
            aria-label={t("search_model_aria")}
            autoComplete="off"
            spellCheck={false}
          />
        )}
        <ComboboxEmpty>{t("no_models_match")}</ComboboxEmpty>
        <ComboboxList>
          {(group: OptionGroup) => (
            <ComboboxGroup key={group.unavailable ? "\0unavailable" : (group.provider ?? "")} items={group.items}>
              {group.unavailable && unavailableLabel && <ComboboxLabel>{unavailableLabel}</ComboboxLabel>}
              {group.provider !== null && (
                <ComboboxLabel>
                  <span className="flex items-center gap-1.5">
                    <ProviderIcon providerId={group.provider} className="size-3.5" />
                    {providerNames[group.provider] || group.provider}
                  </span>
                </ComboboxLabel>
              )}
              <ComboboxCollection>
                {(item: string) =>
                  group.unavailable ? (
                    <ModelOption
                      key={item}
                      value={item}
                      label={describe(item)}
                      meta={unavailableReasons.get(item)}
                      disabled
                    />
                  ) : item === "" ? (
                    <ComboboxItem key="" value="">
                      <span className="min-w-0 flex-1 truncate">{defaultLabel ?? t("follow_global_default")}</span>
                      {defaultHint && <span className="shrink-0 text-xs text-muted-foreground">{defaultHint}</span>}
                    </ComboboxItem>
                  ) : (
                    <ModelOption
                      key={item}
                      value={item}
                      label={modelNames?.[item] || modelIdOf(item)}
                      meta={renderOptionMeta?.(item)}
                    />
                  )
                }
              </ComboboxCollection>
            </ComboboxGroup>
          )}
        </ComboboxList>
      </ComboboxContent>
    </Combobox>
  );
}

function ModelOption({
  value,
  label,
  meta,
  disabled,
}: {
  value: string;
  label: string;
  meta: React.ReactNode;
  disabled?: boolean;
}) {
  const modelId = modelIdOf(value);
  return (
    <ComboboxItem value={value} disabled={disabled} className="items-start">
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate">{label}</span>
        {label !== modelId && <span className="truncate font-mono text-xs text-muted-foreground">{modelId}</span>}
        {meta && <span className="truncate text-xs text-muted-foreground tabular-nums">{meta}</span>}
      </span>
    </ComboboxItem>
  );
}
