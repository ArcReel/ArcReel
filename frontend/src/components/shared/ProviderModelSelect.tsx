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
  value: string; // "gemini-aistudio/veo-3.1-generate-001"
  options: string[]; // ["gemini-aistudio/veo-3.1-generate-001", ...]
  providerNames: Record<string, string>; // {"gemini-aistudio": "AI Studio", ...}
  /**
   * "provider/model" → 按当前语言成文的模型名，与 `providerNames` 同一次目录拉取。缺键的
   * 条目（自定义供应商、后端未覆盖的组合）退回 model id，故本项可省略。
   */
  modelNames?: Record<string, string>; // {"gemini-aistudio/veo-3.1-generate-001": "Veo 3.1", ...}
  onChange: (value: string) => void;
  placeholder?: string;
  className?: string;
  /** If true, adds a default option that returns empty string */
  allowDefault?: boolean;
  /** Label for the default option */
  defaultLabel?: string;
  defaultHint?: string; // "当前: gemini-aistudio/veo-3.1-generate-001"
  /** When value is empty, show this "provider/model" as the effective fallback in the trigger */
  fallbackValue?: string;
  /**
   * 触发按钮上生效值的前缀措辞（已 t()）。默认「跟随全局默认」；任务类型桶细分项回退的是同层
   * 默认模型而非全局层，由调用方改写为「跟随默认」。
   */
  fallbackLabel?: string;
  /** Accessible label for the trigger button */
  "aria-label"?: string;
  /** Enable in-dropdown search input. Defaults to true. */
  searchable?: boolean;
  /** Minimum option count to actually render the search input. Defaults to 6. */
  searchThreshold?: number;
  /**
   * 每个选项行下方的补充信息（如视频模型的能力线：时长/分辨率/音轨）。
   * 传入 `fullValue`（"provider/model"），返回值为 null 时不渲染该行——组件本身对内容
   * 不作视频/图像/文本假设，具体展示逻辑由调用方决定。
   */
  renderOptionMeta?: (fullValue: string) => React.ReactNode;
}

/** 下拉里的一组：默认项单独成组（没有组标题），其余按供应商分组。 */
// 用 type 而非 interface：Combobox 的分组类型带索引签名，interface 不能赋给它。
type OptionGroup = {
  /** 供应商 id；默认项组为 null。 */
  provider: string | null;
  items: string[];
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
  "aria-label": ariaLabel,
  searchable = true,
  searchThreshold = 6,
  renderOptionMeta,
}: ProviderModelSelectProps) {
  const { t } = useTranslation("dashboard");
  const [query, setQuery] = useState("");

  const showSearch = searchable && options.length >= searchThreshold;
  // 搜索框隐藏时不让残留的查询继续过滤，否则用户会看到一个被「隐形」过滤的列表。
  const activeQuery = showSearch ? query.trim().toLowerCase() : "";

  const groups = useMemo(() => groupByProvider(options), [options]);

  // 供应商名命中时保留该供应商的全部模型；否则按 model id 与译名逐项匹配——译名生效后按 id
  // 搜索仍要命中（id 是用户在文档 / 供应商控制台里见到的那一串），反之按中文名搜也要能找到。
  // 有查询时不显示默认项：它不是一个模型，混在结果里会被误当成匹配项。
  const filteredGroups = useMemo(() => {
    const defaultGroup: OptionGroup[] = allowDefault && !activeQuery ? [{ provider: null, items: [""] }] : [];
    if (!activeQuery) return [...defaultGroup, ...groups];
    const out: OptionGroup[] = [];
    for (const group of groups) {
      const providerLabel = (providerNames[group.provider!] || group.provider!).toLowerCase();
      if (providerLabel.includes(activeQuery)) {
        out.push(group);
        continue;
      }
      const items = group.items.filter((item) => {
        if (modelIdOf(item).toLowerCase().includes(activeQuery)) return true;
        const label = modelNames?.[item];
        return !!label && label.toLowerCase().includes(activeQuery);
      });
      if (items.length > 0) out.push({ ...group, items });
    }
    return out;
  }, [allowDefault, activeQuery, groups, providerNames, modelNames]);

  const allGroups = useMemo<OptionGroup[]>(
    () => (allowDefault ? [{ provider: null, items: [""] }, ...groups] : groups),
    [allowDefault, groups],
  );

  // 配置值也可以是不带 model 的裸 provider id（下游按该供应商默认模型执行）。按 "provider/model"
  // 硬拆会让它显示成空的「 · 」，故拆不出 model 时整串当作 provider 名呈现。
  const describe = (fullValue: string) => {
    const idx = fullValue.indexOf("/");
    if (idx === -1) return providerNames[fullValue] || fullValue;
    const provider = fullValue.slice(0, idx);
    return `${providerNames[provider] || provider} · ${modelNames?.[fullValue] || fullValue.slice(idx + 1)}`;
  };

  const showFallback = !value && !!fallbackValue;
  const displayText = value
    ? describe(value)
    : showFallback
      ? `${fallbackLabel ?? t("follow_global_default")} · ${describe(fallbackValue)}`
      : (placeholder ?? t("select_model_placeholder"));

  return (
    <Combobox<string>
      items={allGroups}
      filteredItems={filteredGroups}
      // 默认项的值是空串；没有默认项时空串表示「未选择」，交给 null 显示占位文案。
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
            <ComboboxGroup key={group.provider ?? ""} items={group.items}>
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
                  item === "" ? (
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

function ModelOption({ value, label, meta }: { value: string; label: string; meta: React.ReactNode }) {
  const modelId = modelIdOf(value);
  return (
    <ComboboxItem value={value} className="items-start">
      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
        <span className="truncate">{label}</span>
        {/* 译名与 id 不同才补 id 行：品牌名类模型（译名 = id）补一行等于重复。 */}
        {label !== modelId && <span className="truncate font-mono text-xs text-muted-foreground">{modelId}</span>}
        {meta && <span className="truncate text-xs text-muted-foreground tabular-nums">{meta}</span>}
      </span>
    </ComboboxItem>
  );
}
