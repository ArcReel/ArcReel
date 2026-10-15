import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { ChevronRight, Loader2, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { cn } from "cn";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { ProviderModelItem } from "@/types";
import { errMsg, voidCall } from "@/utils/async";
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
  /** 「添加模型」入口；没有传入时按钮不可用。 */
  onAddModel?: () => void;
  /** 模型 Sheet 保存或移除之后调用，供上层刷新依赖模型的内容。 */
  onModelsChanged?: () => void;
}

/**
 * 供应商详情的模型列表：按文本、图片、视频、音频分组，筛选「全部 / 已启用」。行上的启用开关点击即生效，
 * 停用时同时取消默认。内置与自定义供应商共用；自定义供应商的行另显示调用端点。
 */
export function ProviderModelList({ providerId, providerName, custom, initialModelId, onAddModel, onModelsChanged }: Props) {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const endpoints = useEndpointCatalogStore((s) => s.endpoints);
  const [models, setModels] = useState<ProviderModelItem[] | null>(null);
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

  useEffect(() => {
    const controller = new AbortController();
    voidCall(
      API.listProviderModels(providerId, { signal: controller.signal })
        .then((res) => {
          if (controller.signal.aborted) return;
          setModels(res.models);
          setLoadError(null);
        })
        .catch((err: unknown) => {
          if (!controller.signal.aborted) setLoadError(errMsg(err));
        }),
    );
    return () => controller.abort();
  }, [providerId, reloadKey]);

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
    [providerId, pushToast, t],
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

  const sheet = (
    <ProviderModelSheet
      providerId={providerId}
      providerName={providerName}
      open={openModel}
      custom={custom}
      onClose={closeSheet}
      onSaved={handleSheetSaved}
      onRemoved={handleSheetRemoved}
    />
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
      <Button size="sm" variant="outline" disabled={!onAddModel} onClick={onAddModel}>
        <Plus aria-hidden data-icon="inline-start" />
        {t("pm_add_model")}
      </Button>
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
          <p className="text-sm text-warn">{t("pm_load_failed", { message: loadError })}</p>
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
                      className="flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1 text-left outline-none hover:bg-muted/60 focus-visible:ring-3 focus-visible:ring-ring/50"
                    >
                      <span className={cn("flex min-w-0 flex-1 flex-col gap-0.5", !model.enabled && "opacity-60")}>
                        <span className="flex min-w-0 flex-wrap items-center gap-1.5">
                          <span className="truncate text-sm">{name}</span>
                          {model.is_default && (
                            <span className="inline-flex h-5 shrink-0 items-center rounded-full bg-primary/15 px-1.5 text-xs text-primary">
                              {t("pm_default_badge")}
                            </span>
                          )}
                          <ProviderModelFlags flags={model.flags} />
                        </span>
                        <span className="flex min-w-0 flex-wrap items-center gap-x-3 text-xs text-muted-foreground">
                          <span className="truncate font-mono">{model.model_id}</span>
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
