import { useState } from "react";
import { Loader2, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { CatalogEntryLabel, CatalogMediaFilter, type MediaFilter } from "@/components/shared/CatalogEntryPicker";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { useCatalogEntrySearch } from "@/components/shared/useCatalogEntrySearch";
import { Button } from "@/components/ui/button";
import {
  Command,
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from "@/components/ui/command";
import type { AddProviderModelRequest, CatalogEntryHit, ProviderModelItem } from "@/types";
import { errMsg, voidCall } from "@/utils/async";
import { MEDIA_LABEL_KEY } from "./providerModelDisplay";

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 内置供应商 ID，或自定义供应商的 `custom-<id>`。 */
  providerId: string;
  providerName: string;
  /** 内置供应商：「未启用的模型」一组只有内置供应商有，选中即启用。 */
  builtin: boolean;
  /** 供应商当前的模型列表：取「未启用的模型」，并把已添加的目录条目标出来。 */
  models: ProviderModelItem[];
  /** 添加或启用成功后调用，带新模型的列表行。 */
  onAdded: (model: ProviderModelItem) => void;
  /** 「目录中找不到？手动填写」：带当前搜索词；不给时不显示这一入口。 */
  onManualEntry?: (query: string) => void;
}

/**
 * 「添加模型」对话框：搜索模型名称或输入模型 ID，可按媒体类型筛选。分组为「未启用的模型」（仅内置供应商，选中即启用）、
 * 「按模型 ID 添加」与「模型目录」。模型 ID 与名字的对应规则由服务端执行（docs/adr/0105）。
 */
export function AddModelDialog({ open, onOpenChange, providerName, ...rest }: Props) {
  const { t } = useTranslation("dashboard");
  return (
    <CommandDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("pm_add_title", { name: providerName })}
      description={t("pm_add_description")}
    >
      {open && <AddModelBody {...rest} onClose={() => onOpenChange(false)} />}
    </CommandDialog>
  );
}

function AddModelBody({
  providerId,
  builtin,
  models,
  onAdded,
  onManualEntry,
  onClose,
}: Omit<Props, "open" | "onOpenChange" | "providerName"> & { onClose: () => void }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<MediaFilter>("all");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const search = useCatalogEntrySearch(query, filter === "all" ? undefined : filter);
  const trimmed = query.trim();
  const needle = trimmed.toLowerCase();

  const run = async (action: () => Promise<ProviderModelItem>) => {
    setBusy(true);
    setError(null);
    try {
      const model = await action();
      onClose();
      onAdded(model);
    } catch (err) {
      setError(errMsg(err));
    } finally {
      setBusy(false);
    }
  };
  const add = (body: AddProviderModelRequest) =>
    voidCall(run(async () => (await API.addProviderModel(providerId, body)).model));

  const disabledModels = builtin
    ? models.filter(
        (m) =>
          m.official &&
          !m.enabled &&
          (filter === "all" || m.media_type === filter) &&
          (!needle ||
            m.api_model_name.toLowerCase().includes(needle) ||
            (m.display_name ?? "").toLowerCase().includes(needle)),
      )
    : [];
  const added = (entry: CatalogEntryHit) =>
    models.some(
      (m) =>
        m.api_model_name === entry.name && m.media_type === entry.media_type && (m.enabled || !m.official),
    );

  return (
    <Command shouldFilter={false} loop>
      <CommandInput
        value={query}
        onValueChange={(next) => {
          setQuery(next);
          setError(null);
        }}
        placeholder={t("pm_add_search")}
        aria-label={t("pm_add_search")}
      />
      <CatalogMediaFilter value={filter} onChange={setFilter} />
      {error && (
        <p role="alert" className="px-3 pb-2 text-sm wrap-break-word text-destructive">
          {t("pm_add_failed", { message: error })}
        </p>
      )}
      <CommandList label={t("pm_add_title_short")} className="max-h-[50dvh]" aria-busy={busy || !search.settled}>
        {search.settled && search.error === null && <CommandEmpty>{t("pm_add_empty")}</CommandEmpty>}
        {disabledModels.length > 0 && (
          <CommandGroup heading={t("pm_add_group_disabled")}>
            {disabledModels.map((model) => (
              <CommandItem
                key={model.model_id}
                value={`disabled:${model.model_id}`}
                disabled={busy}
                onSelect={() =>
                  voidCall(run(() => API.setProviderModelEnabled(providerId, model.model_id, true)))
                }
              >
                <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                  <TruncatedText text={model.display_name ?? model.model_id} focusable={false} />
                  <span className="flex min-w-0 gap-1 text-xs text-muted-foreground">
                    <TruncatedText text={model.api_model_name} focusable={false} className="font-mono" />
                    <span className="shrink-0">· {t(MEDIA_LABEL_KEY[model.media_type])}</span>
                  </span>
                </span>
                <span className="shrink-0 text-xs text-muted-foreground">{t("pm_add_enable")}</span>
              </CommandItem>
            ))}
          </CommandGroup>
        )}
        {trimmed && (
          <CommandGroup heading={t("pm_add_group_by_id")}>
            <CommandItem
              value={`id:${trimmed}`}
              disabled={busy}
              onSelect={() => add({ model_id: trimmed, ...(filter === "all" ? {} : { media_type: filter }) })}
            >
              <Plus aria-hidden />
              <TruncatedText text={t("pm_add_by_id", { id: trimmed })} focusable={false} />
            </CommandItem>
          </CommandGroup>
        )}
        {search.error === null &&
          search.entries !== null &&
          search.entries.length > 0 && (
            <CommandGroup heading={t("pm_add_group_catalog")}>
              {search.entries.map((entry) => {
                const already = added(entry);
                return (
                  <CommandItem
                    key={`${entry.maker}/${entry.name}/${entry.media_type}`}
                    value={`entry:${entry.maker}/${entry.name}/${entry.media_type}`}
                    // 结果就绪前，上一次的结果仍显示但不可选
                    disabled={busy || already || !search.settled}
                    onSelect={() =>
                      add({
                        model_id: entry.name,
                        media_type: entry.media_type,
                        entry_binding: `${entry.maker}/${entry.name}`,
                      })
                    }
                  >
                    <CatalogEntryLabel entry={entry} />
                    {already && <span className="shrink-0 text-xs text-muted-foreground">{t("pm_add_added")}</span>}
                  </CommandItem>
                );
              })}
            </CommandGroup>
          )}
      </CommandList>
      {/* 列表只放选项（listbox 的子元素要求），目录读取失败的说明与重试放在列表之外 */}
      {search.error !== null && (
        <div role="alert" className="flex flex-col items-start gap-2 border-t border-border px-3 py-3 text-sm">
          <p>{t("catalog_picker_load_failed")}</p>
          <details className="text-xs text-muted-foreground">
            <summary>{t("catalog_picker_error_details")}</summary>
            {search.error}
          </details>
          <Button variant="outline" size="sm" onClick={search.retry}>
            {t("common:retry")}
          </Button>
        </div>
      )}
      {busy && (
        <p className="flex items-center gap-2 border-t border-border px-3 py-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
          {t("pm_add_adding")}
        </p>
      )}
      {onManualEntry && (
        <div className="flex items-center gap-2 border-t border-border px-3 py-2 text-sm">
          <span className="text-muted-foreground">{t("pm_add_manual_prompt")}</span>
          <Button variant="ghost" size="sm" className="ml-auto" disabled={busy} onClick={() => onManualEntry(trimmed)}>
            <Plus aria-hidden data-icon="inline-start" />
            {t("pm_add_manual")}
          </Button>
        </div>
      )}
    </Command>
  );
}
