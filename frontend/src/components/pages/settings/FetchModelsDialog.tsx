import { useEffect, useId, useState } from "react";
import { Link2, Loader2, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import { CatalogEntryPicker } from "@/components/shared/CatalogEntryPicker";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type {
  CatalogEntryHit,
  DiscoveryFormat,
  FetchedProviderModel,
  FetchProviderModelsResponse,
  MediaType,
} from "@/types";
import { errMsg, voidCall } from "@/utils/async";
import { EndpointSelect } from "./EndpointSelect";
import { MEDIA_LABEL_KEY } from "./providerModelDisplay";

/** One checked model to add: custom providers include endpoints; unrecognized models may include user-selected matching models. */
export interface FetchSelection {
  model: FetchedProviderModel;
  /** Added media types follow selected matches, otherwise discovery results. */
  mediaType: MediaType;
  endpoint: string | null;
  binding: CatalogEntryHit | null;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  providerName: string;
  /** Call provider model-list APIs. */
  load: (signal: AbortSignal) => Promise<FetchProviderModelsResponse>;
  /** Existing models are counted only, never listed. */
  isAdded: (model: FetchedProviderModel) => boolean;
  /** Custom-provider discovery protocols; supplied values add per-row endpoint dropdowns. */
  protocol?: DiscoveryFormat;
  /** Add checked models, returning failure reasons in order (null on success); close dialogs only if all succeed. */
  onAdd: (selections: FetchSelection[]) => Promise<(string | null)[]>;
}

/**
 * Fetch models dialogs call provider lists, showing unadded models in Recognized (checked by default) and Unrecognized
 * groups. Unrecognized rows default unchecked, permitting matching-model selections stored as entry bindings. Name-matched rows show entry names;
 * only unmatched/unpriced-entry rows warn calls remain unpriced.
 */
export function FetchModelsDialog({ open, onOpenChange, ...rest }: Props) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent size={rest.protocol ? "xl" : "lg"}>
        {open && <FetchModelsBody {...rest} onClose={() => onOpenChange(false)} />}
      </DialogContent>
    </Dialog>
  );
}

type LoadState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "not_applicable"; reason: string | null }
  | { status: "ready"; models: FetchedProviderModel[] };

interface RowState {
  checked: boolean;
  endpoint: string | null;
  binding: CatalogEntryHit | null;
  error: string | null;
}

function rowKey(model: FetchedProviderModel): string {
  return `${model.model_id}\u0000${model.model_key ?? ""}`;
}

function initialRows(models: FetchedProviderModel[]): Record<string, RowState> {
  return Object.fromEntries(
    models.map((m) => [rowKey(m), { checked: m.recognized, endpoint: m.endpoint, binding: null, error: null }]),
  );
}

function FetchModelsBody({
  providerName,
  load,
  isAdded,
  protocol,
  onAdd,
  onClose,
}: Omit<Props, "open" | "onOpenChange"> & { onClose: () => void }) {
  const { t } = useTranslation(["dashboard", "common"]);
  const endpointToMediaType = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const [attempt, setAttempt] = useState(0);
  const [state, setState] = useState<LoadState>({ status: "loading" });
  const [rows, setRows] = useState<Record<string, RowState>>({});
  const [adding, setAdding] = useState(false);
  const [picking, setPicking] = useState<FetchedProviderModel | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: "loading" });
    voidCall(
      load(controller.signal)
        .then((res) => {
          if (controller.signal.aborted) return;
          if (res.not_applicable) {
            setState({ status: "not_applicable", reason: res.reason });
            return;
          }
          setRows(initialRows(res.models));
          setState({ status: "ready", models: res.models });
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setState({ status: "error", message: errMsg(err) });
        }),
    );
    return () => controller.abort();
  }, [attempt, load]);

  const updateRow = (model: FetchedProviderModel, patch: Partial<RowState>) =>
    setRows((prev) => ({ ...prev, [rowKey(model)]: { ...prev[rowKey(model)], ...patch } }));

  const mediaOf = (model: FetchedProviderModel, row: RowState): MediaType => row.binding?.media_type ?? model.media_type;

  // Media mismatches between selected models/endpoints choose default endpoints for the matching media type.
  const bind = (model: FetchedProviderModel, binding: CatalogEntryHit | null) => {
    const row = rows[rowKey(model)];
    const media = binding?.media_type ?? model.media_type;
    let endpoint = row.endpoint;
    if (protocol && endpoint && endpointToMediaType[endpoint] !== media) {
      endpoint = model.endpoint_by_media_type?.[media] ?? endpoint;
    }
    updateRow(model, { binding, endpoint, checked: binding ? true : row.checked });
  };

  // Already-added models derive from current lists; successful rows disappear naturally after parent refreshes.
  const fetched = state.status === "ready" ? state.models : [];
  const models = fetched.filter((m) => !isAdded(m));
  const alreadyAdded = fetched.length - models.length;
  const selected = models.filter((m) => rows[rowKey(m)]?.checked);

  const add = async () => {
    setAdding(true);
    try {
      const selections = selected.map((model) => {
        const row = rows[rowKey(model)];
        return { model, mediaType: mediaOf(model, row), endpoint: row.endpoint, binding: row.binding };
      });
      const errors = await onAdd(selections);
      if (errors.every((e) => e === null)) {
        onClose();
        return;
      }
      // Uncheck successful additions, removed after parent refreshes; retain failed rows with reasons.
      setRows((prev) => {
        const next = { ...prev };
        selections.forEach((s, i) => {
          const error = errors[i] ?? null;
          next[rowKey(s.model)] = { ...next[rowKey(s.model)], checked: error !== null, error };
        });
        return next;
      });
    } catch (err) {
      setRows((prev) => {
        const next = { ...prev };
        for (const model of selected) next[rowKey(model)] = { ...next[rowKey(model)], error: errMsg(err) };
        return next;
      });
    } finally {
      setAdding(false);
    }
  };

  const recognized = models.filter((m) => m.recognized);
  const unrecognized = models.filter((m) => !m.recognized);

  const renderRow = (model: FetchedProviderModel) => {
    const row = rows[rowKey(model)];
    if (!row) return null;
    const media = mediaOf(model, row);
    const entryName = row.binding?.display_name ?? model.matched_entry?.display_name;
    // User-selected matching models suppress hints: search results lack prices, so selection counts as user decisions.
    const unpriced = !model.recognized && !row.binding && !model.matched_entry?.priced;
    return (
      <FetchedRow
        key={rowKey(model)}
        model={model}
        row={row}
        media={media}
        entryName={entryName}
        unpriced={unpriced}
        protocol={protocol}
        disabled={adding}
        onCheck={(checked) => updateRow(model, { checked })}
        onEndpoint={(endpoint) => updateRow(model, { endpoint })}
        onPickEntry={() => setPicking(model)}
        onClearEntry={() => bind(model, null)}
      />
    );
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t("pm_fetch_title", { name: providerName })}</DialogTitle>
        <DialogDescription>
          {models.length > 0 ? t("pm_fetch_description") : t("pm_fetch_description_idle")}
        </DialogDescription>
      </DialogHeader>
      <DialogBody aria-busy={state.status === "loading"}>
        <div className="flex flex-col gap-4">
          {state.status === "loading" && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
              {t("pm_fetch_loading")}
            </p>
          )}
          {state.status === "error" && (
            <div role="alert" className="flex flex-col items-start gap-2 text-sm">
              <p>{t("pm_fetch_failed")}</p>
              <details className="text-xs text-muted-foreground">
                <summary>{t("catalog_picker_error_details")}</summary>
                <span className="wrap-break-word">{state.message}</span>
              </details>
              <Button variant="outline" size="sm" onClick={() => setAttempt((n) => n + 1)}>
                {t("common:retry")}
              </Button>
            </div>
          )}
          {state.status === "not_applicable" && (
            <p className="text-sm text-muted-foreground">{state.reason ?? t("discovery_not_applicable")}</p>
          )}
          {state.status === "ready" && models.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {alreadyAdded > 0 ? t("pm_fetch_all_added") : t("pm_fetch_none")}
            </p>
          )}
          {recognized.length > 0 && (
            <FetchGroup title={t("pm_fetch_recognized")} count={recognized.length}>
              {recognized.map(renderRow)}
            </FetchGroup>
          )}
          {unrecognized.length > 0 && (
            <FetchGroup title={t("pm_fetch_unrecognized")} count={unrecognized.length} hint={t("pm_fetch_unrecognized_hint")}>
              {unrecognized.map(renderRow)}
            </FetchGroup>
          )}
          {models.length > 0 && alreadyAdded > 0 && (
            <p className="text-xs text-muted-foreground">{t("pm_fetch_already_added", { count: alreadyAdded })}</p>
          )}
        </div>
      </DialogBody>
      <DialogFooter>
        <Button variant="outline" disabled={adding} onClick={onClose}>
          {models.length === 0 ? t("common:close") : t("common:cancel")}
        </Button>
        {models.length > 0 && (
          <Button disabled={selected.length === 0 || adding} onClick={() => voidCall(add())}>
            {adding && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {t("pm_fetch_add", { count: selected.length })}
          </Button>
        )}
      </DialogFooter>
      {picking && (
        <CatalogEntryPicker
          open
          onOpenChange={(next) => {
            if (!next) setPicking(null);
          }}
          mediaType={picking.media_type}
          title={t("pm_fetch_match_title", { name: picking.model_id })}
          onSelect={(entry) => bind(picking, entry)}
        />
      )}
    </>
  );
}

function FetchGroup({
  title,
  count,
  hint,
  children,
}: {
  title: string;
  count: number;
  hint?: string;
  children: React.ReactNode;
}) {
  const headingId = useId();
  return (
    <section aria-labelledby={headingId} className="flex flex-col gap-1.5">
      <div className="flex flex-col gap-0.5">
        <h3 id={headingId} className="text-sm font-medium">
          {title}
          <span className="ml-1.5 text-muted-foreground tabular-nums">{count}</span>
        </h3>
        {hint && <p className="text-xs text-muted-foreground">{hint}</p>}
      </div>
      <ul className="flex flex-col divide-y divide-border/60 rounded-lg border border-border">{children}</ul>
    </section>
  );
}

function FetchedRow({
  model,
  row,
  media,
  entryName,
  unpriced,
  protocol,
  disabled,
  onCheck,
  onEndpoint,
  onPickEntry,
  onClearEntry,
}: {
  model: FetchedProviderModel;
  row: RowState;
  media: MediaType;
  entryName: string | undefined;
  unpriced: boolean;
  protocol: DiscoveryFormat | undefined;
  disabled: boolean;
  onCheck: (checked: boolean) => void;
  onEndpoint: (endpoint: string) => void;
  onPickEntry: () => void;
  onClearEntry: () => void;
}) {
  const { t } = useTranslation("dashboard");
  const checkboxId = useId();
  return (
    <li className="flex flex-col gap-1 px-3 py-2">
      <div className="flex items-center gap-3">
        <Checkbox id={checkboxId} checked={row.checked} disabled={disabled} onCheckedChange={onCheck} />
        <label htmlFor={checkboxId} className="flex min-w-0 flex-1 flex-col gap-0.5">
          <span className="font-mono text-sm break-all">{model.model_id}</span>
          <TruncatedText
            className="text-xs text-muted-foreground"
            text={
              entryName
                ? t("pm_fetch_entry_media", { name: entryName, media: t(MEDIA_LABEL_KEY[media]) })
                : t(MEDIA_LABEL_KEY[media])
            }
          />
        </label>
        {!model.recognized &&
          (row.binding ? (
            <Button
              variant="ghost"
              size="icon-sm"
              disabled={disabled}
              aria-label={t("pm_fetch_match_clear", { name: model.model_id })}
              onClick={onClearEntry}
            >
              <X aria-hidden />
            </Button>
          ) : (
            <Button
              variant="ghost"
              size="sm"
              disabled={disabled}
              aria-label={t("pm_fetch_match_for", { name: model.model_id })}
              onClick={onPickEntry}
            >
              <Link2 aria-hidden data-icon="inline-start" />
              {t("pm_fetch_match")}
            </Button>
          ))}
        {protocol && row.endpoint !== null && (
          <div className="w-56 shrink-0">
            <EndpointSelect
              compact
              aria-label={t("pm_fetch_endpoint_for", { name: model.model_id })}
              value={row.endpoint}
              onChange={onEndpoint}
              protocol={protocol}
              disabled={disabled}
            />
          </div>
        )}
      </div>
      {unpriced && <p className="pl-7 text-xs text-warn">{t("pm_fetch_row_unpriced")}</p>}
      {row.error && (
        <p role="alert" className="pl-7 text-xs wrap-break-word text-destructive">
          {t("pm_fetch_add_failed", { message: row.error })}
        </p>
      )}
    </li>
  );
}
