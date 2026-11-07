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
  /** Built-in provider IDs, or custom-provider `custom-<id>`. */
  providerId: string;
  providerName: string;
  /** Built-in providers: Disabled models groups exist only for built-ins; selection enables them. */
  builtin: boolean;
  /** Current provider model lists, supplying disabled models and marking already-added catalog entries. */
  models: ProviderModelItem[];
  /** Called after successful addition/enablement, carrying new model rows. */
  onAdded: (model: ProviderModelItem) => void;
  /** Manual-entry fallback with current search terms; omitted callbacks hide this entry. */
  onManualEntry?: (query: string) => void;
}

/**
 * Add model dialogs: search model names or enter IDs, filter media types. Groups are Disabled models (built-ins only, enabling on selection),
 * Add by model ID and Model catalog. Servers match model IDs/names (docs/adr/0105).
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
          m.min_app_version_satisfied &&
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
                    // Previous results remain visible but unselectable until replacements are ready.
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
      {/* Lists contain options only as listbox requires; place catalog errors/retries outside lists. */}
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
