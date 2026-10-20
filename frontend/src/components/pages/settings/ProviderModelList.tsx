import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronRight, CloudDownload, Loader2, Plus } from "lucide-react";
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
import { MEDIA_LABEL_KEY, ProviderModelFlags, priceSummaryText } from "./providerModelDisplay";
import { ProviderModelSheet, type CustomProviderContext, type OpenProviderModel } from "./ProviderModelSheet";

// 分组顺序：文本、图片、视频、音频。
const MEDIA_ORDER = ["text", "image", "video", "audio"] as const;

type Filter = "all" | "enabled";

interface Props {
  /** 内置供应商 ID，或自定义供应商的 `custom-<id>`。 */
  providerId: string;
  /** 供应商名称，显示在模型 Sheet 的标题下与「从『X』移除」里。 */
  providerName: string;
  /** 自定义供应商的接口地址与模型发现协议：模型 Sheet 据此编辑调用端点与请求地址。 */
  custom?: CustomProviderContext;
  /** 深链里的模型 ID：打开时直接打开它的 Sheet。 */
  initialModelId?: string;
  /** 添加模型、模型 Sheet 保存或移除之后调用，供上层刷新依赖模型的内容。 */
  onModelsChanged?: () => void;
  /** 已保存的供应商倍率。行上的「未定价」标记随倍率是否为 0 变化，倍率保存后整表重取。 */
  providerMultiplier?: number;
  /** 供应商支持的媒体类型：手动填写模型时只提供这些类型；不给则不限。 */
  mediaTypes?: readonly string[];
}

/** 获取到的模型添加时的请求：内置供应商对上官方收录的模型即启用它，自定义供应商带调用端点。 */
function addRequestFor({ model, mediaType, endpoint, binding }: FetchSelection, custom: boolean): AddProviderModelRequest {
  return {
    model_id: model.model_id,
    media_type: mediaType,
    ...(binding ? { entry_binding: `${binding.maker}/${binding.name}` } : {}),
    ...(custom && endpoint ? { endpoint } : {}),
  };
}

/**
 * 供应商详情的模型列表：按文本、图片、视频、音频分组，筛选「全部 / 已启用」。行上的启用开关点击即生效，
 * 停用时同时取消默认。上方是「获取模型」（有模型列表接口的供应商）与「添加模型」，添加后打开新模型的 Sheet。
 * 内置与自定义供应商共用；自定义供应商的行另显示调用端点。
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
  // 「添加模型」里的「手动填写」：带当时的搜索词，作为新模型的 ID 与模型名称的初值
  const [manual, setManual] = useState<{ query: string } | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [filter, setFilter] = useState<Filter>("all");
  const [pending, setPending] = useState<ReadonlySet<string>>(new Set());
  const headingId = useId();
  // 打开的模型 Sheet；每次打开换一个 key，关掉再打开同一个模型时重新读取
  const sheetKeyRef = useRef(0);
  const [openModel, setOpenModel] = useState<OpenProviderModel | null>(() =>
    initialModelId ? { modelId: initialModelId, key: 0 } : null,
  );
  const openSheet = (modelId: string) => {
    sheetKeyRef.current += 1;
    setOpenModel({ modelId, key: sheetKeyRef.current });
  };

  // 整表重取在途时切换了开关：重取的结果可能早于这次切换，切换完成后再取一次，不让旧行盖回开关
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

  // Sheet 保存后整表重取：设为默认会取消同组其他模型的默认，改名会换掉行的键
  const handleSheetSaved = useCallback(
    (model: ProviderModelItem, previousModelId: string) => {
      setModels((prev) => prev?.map((m) => (m.model_id === previousModelId ? model : m)) ?? prev);
      // 改了模型 ID：Sheet 继续显示同一个模型，不重新打开
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

  // 添加或启用之后：整表重取（新模型可能成为默认），并打开新模型的 Sheet
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
  // 已添加：自定义供应商按模型 ID；内置供应商按 API 模型名，停用的官方收录模型不算（添加即启用它）
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

  // 手动填写（或选中填写时发现的同名条目）：先写入条目，再按它添加模型；模型 ID 取搜索词，没有时取模型名称
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

  // Sheet 在三种状态里处于同一位置：列表加载完成时不会被重新挂载
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
                return (
                  <li key={model.model_id} className="flex items-center gap-3 py-1 pr-1 pl-3">
                    <Switch
                      size="sm"
                      checked={model.enabled}
                      disabled={pending.has(model.model_id)}
                      aria-label={t("pm_enable_model", { name })}
                      onCheckedChange={(checked) => voidCall(setEnabled(model, checked))}
                    />
                    <button
                      type="button"
                      aria-haspopup="dialog"
                      onClick={() => openSheet(model.model_id)}
                      className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1 text-left focus-ring hover:bg-muted/60"
                    >
                      <span className="flex min-w-0 flex-1 flex-col gap-0.5">
                        <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                          <TruncatedText
                            text={name}
                            focusable={false}
                            className={cn("text-sm", !model.enabled && "text-muted-foreground")}
                          />
                          {model.is_default && (
                            <span className="inline-flex h-5 shrink-0 items-center rounded-full bg-primary/15 px-1.5 text-xs text-primary">
                              {t("pm_default_badge")}
                            </span>
                          )}
                          <ProviderModelFlags flags={model.flags} />
                        </span>
                        <span className="flex min-w-0 flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
                          <TruncatedText text={model.model_id} focusable={false} className="font-mono" />
                          {price && <span>{price}</span>}
                          {model.endpoint && <span>{endpointLabel(model.endpoint)}</span>}
                        </span>
                      </span>
                      <ChevronRight aria-hidden className="size-4 shrink-0 text-muted-foreground" />
                    </button>
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
