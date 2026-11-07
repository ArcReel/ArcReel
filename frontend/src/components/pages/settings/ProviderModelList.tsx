import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, ChevronRight, CloudDownload, Loader2, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type {
  AddProviderModelRequest,
  CatalogEntryHit,
  FetchedProviderModel,
  NewCatalogEntry,
  ProviderModelItem,
} from "@/types";
import { errMsg, voidCall } from "@/utils/async";
import { AddModelDialog } from "./AddModelDialog";
import { FetchModelsDialog, type FetchSelection } from "./FetchModelsDialog";
import { ManualEntryDialog } from "./ManualEntryDialog";
import { ProviderModelFlags } from "./ProviderModelFlags";
import { MEDIA_LABEL_KEY, priceSummaryText } from "./providerModelDisplay";
import { ProviderModelSheet, type CustomProviderContext, type OpenProviderModel } from "./ProviderModelSheet";

// Group order: text, image, video, audio.
const MEDIA_ORDER = ["text", "image", "video", "audio"] as const;

type Filter = "all" | "enabled";

interface Props {
  /** Built-in provider IDs, or custom-provider `custom-<id>`. */
  providerId: string;
  /** Provider names beneath model-Sheet titles and in Remove from X. */
  providerName: string;
  /** Custom-provider API addresses/discovery protocols, for Sheet endpoint/request-address editing. */
  custom?: CustomProviderContext;
  /** Deep-linked model IDs, immediately opening their Sheets. */
  initialModelId?: string;
  /** Called after additions/Sheet saves/removal, refreshing parent model-dependent content. */
  onModelsChanged?: () => void;
  /** Saved provider multipliers; unpriced row indicators depend on zero values, refetching tables after multiplier saves. */
  providerMultiplier?: number;
  /** Supported provider media types, restricting manual entry; omitted means unrestricted. */
  mediaTypes?: readonly string[];
}

/** Add discovered models: built-ins enable matched official models; custom providers include endpoints. */
function addRequestFor({ model, mediaType, endpoint, binding }: FetchSelection, custom: boolean): AddProviderModelRequest {
  return {
    model_id: model.model_id,
    media_type: mediaType,
    ...(binding ? { entry_binding: `${binding.maker}/${binding.name}` } : {}),
    ...(custom && endpoint ? { endpoint } : {}),
  };
}

/**
 * Provider model lists group text/image/video/audio and filter All/Enabled. Enablement applies immediately;
 * disabling also clears defaults. Fetch models (providers with list APIs) and Add model precede lists; additions open new Sheets.
 * Built-in/custom providers share lists; custom rows also show endpoints.
 * Official models requiring newer app versions append last, disabled with version requirements; switches/Sheets unavailable (docs/adr/0103).
 */
export function ProviderModelList({
  providerId,
  providerName,
  custom,
  initialModelId,
  onModelsChanged,
  providerMultiplier,
  mediaTypes,
}: Props) {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const endpoints = useEndpointCatalogStore((s) => s.endpoints);
  const [models, setModels] = useState<ProviderModelItem[] | null>(null);
  const [discoverable, setDiscoverable] = useState(false);
  const [addOpen, setAddOpen] = useState(false);
  const [fetchOpen, setFetchOpen] = useState(false);
  // Add model manual entry carries then-current search terms as initial model IDs/names.
  const [manual, setManual] = useState<{ query: string } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const headingId = useId();
  // Open model Sheets use unique keys per opening, rereading even identical models after reopening.
  const sheetKeyRef = useRef(0);
  const [openModel, setOpenModel] = useState<OpenProviderModel | null>(() =>
    initialModelId ? { modelId: initialModelId, key: 0 } : null,
  );
  const openSheet = (modelId: string) => {
    sheetKeyRef.current += 1;
    setOpenModel({ modelId, key: sheetKeyRef.current });
  };

  // Switch toggles during full refetches may postdate returned data; refetch again after toggling so stale rows never restore switches.
  const listLoadingRef = useRef(false);
  useEffect(() => {
    const controller = new AbortController();
    listLoadingRef.current = true;
    voidCall(
      API.listProviderModels(providerId, { signal: controller.signal })
        .then((res) => {
          if (controller.signal.aborted) return;
          setModels(res.models);
          setDiscoverable(res.discoverable);
          setLoadError(null);
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setLoadError(errMsg(err));
        })
        .finally(() => {
          if (!controller.signal.aborted) listLoadingRef.current = false;
        }),
    );
    return () => controller.abort();
  }, [providerId, reloadKey, providerMultiplier]);

  const hasEndpoints = models?.some((m) => m.endpoint) ?? false;
  useEffect(() => {
    if (hasEndpoints) voidCall(useEndpointCatalogStore.getState().fetch());
  }, [hasEndpoints]);

  const setEnabled = useCallback(
    async (model: ProviderModelItem, enabled: boolean) => {
      setPending((prev) => new Set(prev).add(model.model_id));
      try {
        const updated = await API.setProviderModelEnabled(providerId, model.model_id, enabled);
        setModels((prev) => prev?.map((m) => (m.model_id === updated.model_id ? updated : m)) ?? prev);
        if (listLoadingRef.current) setReloadKey((k) => k + 1);
        onModelsChanged?.();
      } catch (err) {
        pushToast(
          t("pm_toggle_failed", { name: model.display_name ?? model.model_id, message: errMsg(err) }),
          "error",
        );
      } finally {
        setPending((prev) => {
          const next = new Set(prev);
          next.delete(model.model_id);
          return next;
        });
      }
    },
    [providerId, pushToast, t, onModelsChanged],
  );

  // Refetch whole lists after Sheet saves: new defaults clear peer defaults; renames change row keys.
  const handleSheetSaved = useCallback(
    (model: ProviderModelItem, previousModelId: string) => {
      setModels((prev) => prev?.map((m) => (m.model_id === previousModelId ? model : m)) ?? prev);
      // Changed model IDs keep Sheets on identical models, without reopening.
      setOpenModel((prev) => (prev && prev.modelId === previousModelId ? { ...prev, modelId: model.model_id } : prev));
      setReloadKey((k) => k + 1);
      onModelsChanged?.();
    },
    [onModelsChanged],
  );
  const handleSheetRemoved = useCallback(
    (modelId: string) => {
      setModels((prev) => prev?.filter((m) => m.model_id !== modelId) ?? prev);
      onModelsChanged?.();
    },
    [onModelsChanged],
  );
  const closeSheet = useCallback(() => setOpenModel(null), []);

  // After addition/enablement, refetch whole lists (new models may default) and open new model Sheets.
  const handleAdded = useCallback(
    (model: ProviderModelItem) => {
      setModels((prev) => {
        if (!prev) return prev;
        return prev.some((m) => m.model_id === model.model_id)
          ? prev.map((m) => (m.model_id === model.model_id ? model : m))
          : [...prev, model];
      });
      setReloadKey((k) => k + 1);
      sheetKeyRef.current += 1;
      setOpenModel({ modelId: model.model_id, key: sheetKeyRef.current });
      onModelsChanged?.();
    },
    [onModelsChanged],
  );

  const loadFetched = useCallback(
    (signal: AbortSignal) => API.discoverProviderModels(providerId, { signal }),
    [providerId],
  );
  // Already added uses model IDs for custom, API model names for built-ins; disabled official models do not count, since addition enables them.
  const isFetchedAdded = useCallback(
    (fetched: FetchedProviderModel) =>
      (models ?? []).some((m) =>
        custom
          ? m.model_id === fetched.model_id
          : m.api_model_name === fetched.model_id &&
            (fetched.model_key === null || m.model_id === fetched.model_key) &&
            (m.enabled || !m.official),
      ),
    [models, custom],
  );
  const addFetched = useCallback(
    async (selections: FetchSelection[]) => {
      const errors: (string | null)[] = [];
      for (const selection of selections) {
        try {
          // react-doctor-disable-next-line react-doctor/async-await-in-loop -- sequential additions: servers default first models per media type; concurrency races defaults and interleaves same-provider writes
          await API.addProviderModel(providerId, addRequestFor(selection, !!custom));
          errors.push(null);
        } catch (err) {
          errors.push(errMsg(err));
        }
      }
      const addedCount = errors.filter((e) => e === null).length;
      if (addedCount > 0) {
        setReloadKey((k) => k + 1);
        onModelsChanged?.();
        pushToast(t("pm_fetch_added", { count: addedCount }), "success");
      }
      return errors;
    },
    [providerId, custom, onModelsChanged, pushToast, t],
  );

  // Manual entry or discovered same-name selection first persists entries, then adds models; IDs use search terms, otherwise model names.
  const addWithEntry = useCallback(
    async (query: string, entry: Pick<NewCatalogEntry, "maker" | "name" | "media_type">, create?: NewCatalogEntry) => {
      try {
        if (create) await API.createCatalogEntry(create);
        const res = await API.addProviderModel(providerId, {
          model_id: query || entry.name,
          media_type: entry.media_type,
          entry_binding: `${entry.maker}/${entry.name}`,
        });
        handleAdded(res.model);
      } catch (err) {
        pushToast(t("pm_add_failed", { message: errMsg(err) }), "error");
      }
    },
    [providerId, handleAdded, pushToast, t],
  );

  const sheet = (
    <>
      <AddModelDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        providerId={providerId}
        providerName={providerName}
        builtin={!custom}
        models={models ?? []}
        onAdded={handleAdded}
        onManualEntry={(query) => {
          setAddOpen(false);
          setManual({ query });
        }}
      />
      {manual && (
        <ManualEntryDialog
          open
          onOpenChange={(open) => !open && setManual(null)}
          mediaTypes={mediaTypes}
          initialName={manual.query}
          onConfirm={(entry) => voidCall(addWithEntry(manual.query, entry, entry))}
          onUseExisting={(hit: CatalogEntryHit) => voidCall(addWithEntry(manual.query, hit))}
        />
      )}
      <FetchModelsDialog
        open={fetchOpen}
        onOpenChange={setFetchOpen}
        providerName={providerName}
        load={loadFetched}
        isAdded={isFetchedAdded}
        protocol={custom?.protocol}
        onAdd={addFetched}
      />
      <ProviderModelSheet
      providerId={providerId}
      providerName={providerName}
      open={openModel}
      custom={custom}
      onClose={closeSheet}
      onSaved={handleSheetSaved}
      onRemoved={handleSheetRemoved}
      />
    </>
  );

  const endpointLabel = (key: string) => {
    const endpoint = endpoints.find((e) => e.key === key);
    if (!endpoint) return key;
    return endpoint.display_name ?? t(endpoint.display_name_key);
  };

  const header = (
    <div className="flex items-center justify-between gap-3">
      <h3 id={headingId} className="text-base font-medium">
        {t("pm_heading")}
      </h3>
      <div className="flex items-center gap-2">
        {discoverable && (
          <Button size="sm" variant="outline" disabled={!models} onClick={() => setFetchOpen(true)}>
            <CloudDownload aria-hidden data-icon="inline-start" />
            {t("pm_fetch_models")}
          </Button>
        )}
        <Button size="sm" variant="outline" disabled={!models} onClick={() => setAddOpen(true)}>
          <Plus aria-hidden data-icon="inline-start" />
          {t("pm_add_model")}
        </Button>
      </div>
    </div>
  );

  // Sheets occupy identical positions in all three states, preventing remounts when lists finish loading.
  const withSheet = (content: ReactNode) => (
    <>
      {content}
      {sheet}
    </>
  );

  if (loadError) {
    return withSheet(
      <section aria-labelledby={headingId} className="flex flex-col gap-3">
        {header}
        <div role="alert" className="flex flex-col items-start gap-2">
          <p className="text-sm text-destructive">{t("pm_load_failed", { message: loadError })}</p>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setLoadError(null);
              setReloadKey((k) => k + 1);
            }}
          >
            {t("common:retry")}
          </Button>
        </div>
      </section>
    );
  }

  if (!models) {
    return withSheet(
      <section aria-labelledby={headingId} className="flex flex-col gap-3">
        {header}
        <p className="flex items-center gap-2 text-sm text-muted-foreground">
          <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
          {t("common:loading")}
        </p>
      </section>
    );
  }

  const enabledCount = models.filter((m) => m.enabled).length;
  const visible = filter === "enabled" ? models.filter((m) => m.enabled) : models;

  return withSheet(
    <section aria-labelledby={headingId} className="flex flex-col gap-3">
      {header}
      {models.length > 0 && (
        <ToggleGroup
          aria-label={t("pm_filter_label")}
          variant="outline"
          size="sm"
          value={[filter]}
          onValueChange={(next: string[]) => {
            if (next[0]) setFilter(next[0] as Filter);
          }}
        >
          <ToggleGroupItem value="all">
            {t("pm_filter_all")}
            <span className="text-muted-foreground tabular-nums">{models.length}</span>
          </ToggleGroupItem>
          <ToggleGroupItem value="enabled">
            {t("pm_filter_enabled")}
            <span className="text-muted-foreground tabular-nums">{enabledCount}</span>
          </ToggleGroupItem>
        </ToggleGroup>
      )}

      {visible.length === 0 && (
        <p className="rounded-lg border border-dashed border-input px-4 py-6 text-center text-sm text-muted-foreground">
          {models.length === 0 ? t("pm_empty") : t("pm_empty_enabled")}
        </p>
      )}

      {MEDIA_ORDER.map((media) => {
        const group = visible.filter((m) => m.media_type === media);
        if (group.length === 0) return null;
        const groupHeadingId = `${headingId}-${media}`;
        return (
          <div key={media} className="flex flex-col gap-1">
            <h4 id={groupHeadingId} className="px-1 text-xs text-muted-foreground">
              {t(MEDIA_LABEL_KEY[media])}
              <span className="ml-1.5 tabular-nums">{group.length}</span>
            </h4>
            <ul aria-labelledby={groupHeadingId} className="flex flex-col divide-y divide-border/60 rounded-lg border border-border">
              {group.map((model) => {
                const name = model.display_name ?? model.model_id;
                const price = model.price ? priceSummaryText(t, model.price) : "";
                const versionUnmet = !model.min_app_version_satisfied;
                const details = (
                  <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                    <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                      <TruncatedText
                        text={name}
                        focusable={versionUnmet}
                        className={cn("text-sm", !model.enabled && "text-muted-foreground")}
                      />
                      {model.is_default && (
                        <span className="inline-flex h-5 shrink-0 items-center rounded-full bg-primary/15 px-1.5 text-xs text-primary">
                          {t("pm_default_badge")}
                        </span>
                      )}
                      {versionUnmet && (
                        <span className="inline-flex h-5 shrink-0 items-center gap-1 rounded-full border border-warn/50 px-1.5 text-xs whitespace-nowrap text-warn">
                          <AlertTriangle aria-hidden className="size-3" />
                          {t("pm_requires_app", { version: model.min_app_version })}
                        </span>
                      )}
                      <ProviderModelFlags flags={model.flags} shutdownDate={model.lifecycle.shutdown_date} />
                    </span>
                    <span className="flex min-w-0 flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
                      <TruncatedText text={model.model_id} focusable={versionUnmet} className="font-mono" />
                      {price && <span>{price}</span>}
                      {model.endpoint && <span>{endpointLabel(model.endpoint)}</span>}
                    </span>
                  </span>
                );
                return (
                  <li key={model.model_id} className="flex items-center gap-3 py-1 pr-1 pl-3">
                    <Switch
                      size="sm"
                      checked={model.enabled}
                      disabled={versionUnmet || pending.has(model.model_id)}
                      aria-label={t("pm_enable_model", { name })}
                      onCheckedChange={(checked) => voidCall(setEnabled(model, checked))}
                    />
                    {versionUnmet ? (
                      <div className="flex min-w-0 flex-1 items-center gap-2 px-2 py-1">{details}</div>
                    ) : (
                      <button
                        type="button"
                        aria-haspopup="dialog"
                        onClick={() => openSheet(model.model_id)}
                        className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1 text-left focus-ring hover:bg-muted/60"
                      >
                        {details}
                        <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                      </button>
                    )}
                  </li>
                );
              })}
            </ul>
          </div>
        );
      })}
    </section>
  );
}
