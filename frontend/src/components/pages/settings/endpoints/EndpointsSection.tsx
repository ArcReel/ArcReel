import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useLocation, useSearch } from "wouter";
import { FileJson2, Loader2, Lock, Plus, Workflow } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { errMsg, voidCall } from "@/utils/async";
import { useAppStore } from "@/stores/app-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import { endpointSettingsPath, providerSettingsPath } from "@/app-routes";
import { SecondaryRail, type SecondaryRailGroup, type SecondaryRailItem } from "@/components/shared/master-detail/SecondaryRail";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { Button } from "@/components/ui/button";
import type {
  AnyEndpointDefinition,
  ComfyuiEndpointDefinition,
  ComfyuiMediaType,
  CustomEndpointInfo,
  CustomProviderInfo,
  EndpointDefinition,
  EndpointDescriptor,
  EndpointInstallation,
  EndpointReference,
  EndpointValidateResponse,
  MarketEntry,
  MarketSubmission,
} from "@/types";
import { MarketInstallDialog } from "../market/MarketInstallDialog";
import { isDeclarativeDefinition, newEndpointDefinition } from "./endpoint-definition-draft";
import { isComfyuiDefinition, reimportedDefinition, type ComfyuiImportDraft } from "./comfyui-import";
import { EndpointDetail, type EndpointSelection } from "./EndpointDetail";
import type { EndpointBackTarget } from "./EndpointHeader";
import { EndpointImportDialog } from "./EndpointImportDialog";
import { groupUsagesByEndpoint } from "./EndpointUsageList";

/** 有 kind 即是一份此刻就能显示的端点定义；其余形状的身份由服务端的分流结果给出。 */
function hasKind(value: unknown): value is AnyEndpointDefinition {
  return typeof value === "object" && value !== null && "kind" in value;
}

/** 刚导入、还没保存的 ComfyUI 端点在 URL 里的占位键。 */
const COMFYUI_DRAFT_KEY = "comfyui-new";
/** 新建端点在 URL 里的占位键。 */
const NEW_KEY = "new";

/**
 * 详情组件的实例身份。定义、绑定与推断结果都是详情里的 state，只在挂载那一刻取自 props，
 * 换一份定义就得换一个实例，否则屏幕上还是旧的那份、保存下去的也是旧的。
 *
 * 端点键之外还有三处会在键不变的情况下换定义：市场更新原地替换（安装时间随之变化），重新导入
 * 产生一份新的未保存定义（自带一次性 token），放弃 ComfyUI 端点的修改（递增 `resetEpoch`）。
 */
function detailInstanceKey(selectedKey: string | null, selection: EndpointSelection, resetEpoch: number): string {
  const key = `${selectedKey ?? ""}:${resetEpoch}`;
  if (selection.mode === "custom" || selection.mode === "comfyui") {
    return `${key}:${selection.record.installation?.installed_at ?? ""}`;
  }
  if (selection.mode === "comfyui-draft") return `${key}:draft-${selection.draft.token}`;
  return `${key}:`;
}

interface MarketUpdateTarget {
  entry: MarketEntry;
  currentDefinition: EndpointDefinition;
  hasUnsavedChanges: boolean;
}

/**
 * 调用端点分区（全出血档）：左侧二级栏按「我的端点 | 内置」分组，右侧端点详情。
 * 选中项写进 URL 的 endpoint 参数，切换经路由，刷新与外部跳转都能落回同一个端点；
 * 从自定义供应商跳来时带 from 参数，详情顶部显示「返回『供应商名』」，切换端点时保留。
 */
export function EndpointsSection() {
  const { t } = useTranslation(["dashboard", "common"]);
  const [, navigate] = useLocation();
  const search = useSearch();
  const pushToast = useAppStore((s) => s.pushToast);

  const catalog = useEndpointCatalogStore((s) => s.endpoints);
  const refreshCatalog = useEndpointCatalogStore((s) => s.refresh);

  const [customEndpoints, setCustomEndpoints] = useState<CustomEndpointInfo[]>([]);
  const [providers, setProviders] = useState<CustomProviderInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [resetEpoch, setResetEpoch] = useState(0);

  const [importFileName, setImportFileName] = useState("");
  const [importDefinition, setImportDefinition] = useState<AnyEndpointDefinition | null>(null);
  const [importValidation, setImportValidation] = useState<EndpointValidateResponse | null>(null);
  // 导入弹窗在途的请求：推断节点绑定（取消即作废）或落盘（已发出就收不回，期间不能取消）。
  const [importBusy, setImportBusy] = useState<"infer" | "save" | null>(null);
  const [importOpen, setImportOpen] = useState(false);
  const [importPending, setImportPending] = useState(false);
  // 粘进来的原始载荷：换媒体类型时要拿它重跑一次校验，包装结果随之更新。
  const [importPayload, setImportPayload] = useState<unknown>(null);
  const [importMediaType, setImportMediaType] = useState<ComfyuiMediaType>("video");
  // 重新导入的落点：新 workflow 接到这份定义上，身份与已确认的节点绑定沿用它。
  const [reimportBase, setReimportBase] = useState<{
    record: CustomEndpointInfo | null;
    definition: ComfyuiEndpointDefinition;
  } | null>(null);
  const [comfyuiDraft, setComfyuiDraft] = useState<ComfyuiImportDraft | null>(null);
  // 未保存定义的一次性身份发号器；只进详情的 React key，不参与渲染。
  const draftSeq = useRef(0);

  const [marketUpdateTarget, setMarketUpdateTarget] = useState<MarketUpdateTarget | null>(null);
  const [marketUpdatePending, setMarketUpdatePending] = useState(false);
  const marketUpdateRef = useRef<AbortController | null>(null);

  const params = new URLSearchParams(search);
  const selectedKey = params.get("endpoint");
  const fromParam = params.get("from");
  const fromProviderId = fromParam !== null && /^\d+$/.test(fromParam) ? Number(fromParam) : undefined;

  // 官方服务开启时才有值：各端点最近一次分享提交，进入页面时刷新一次；关闭或取不回时不展示分享入口与状态。
  const [submissions, setSubmissions] = useState<Map<number, MarketSubmission> | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    voidCall(
      (async () => {
        const official = await API.getOfficialService({ signal: controller.signal });
        if (!official.enabled) return;
        const { submissions: listed } = await API.listMarketSubmissions({ signal: controller.signal });
        if (!controller.signal.aborted) {
          setSubmissions(new Map(listed.map((submission) => [submission.endpoint_id, submission])));
        }
      })(),
      () => {
        // 官方服务不可用时端点页照常工作，只是不提供分享入口。
      },
    );
    return () => controller.abort();
  }, []);

  /** 端点地址：保留「从哪个供应商来」，切到别的端点后仍能返回。 */
  const hrefOf = useCallback(
    (key: string | null) => endpointSettingsPath(key ?? undefined, { fromCustomProvider: fromProviderId }),
    [fromProviderId],
  );

  const select = useCallback(
    (key: string | null) => navigate(hrefOf(key), { replace: true }),
    [navigate, hrefOf],
  );

  const reload = useCallback(async (signal?: AbortSignal) => {
    const [endpointsRes, providersRes] = await Promise.all([
      API.listCustomEndpoints({ signal }),
      API.listCustomProviders({ signal }),
    ]);
    if (signal?.aborted) return;
    setCustomEndpoints(endpointsRes.endpoints);
    setProviders(providersRes.providers);
    await refreshCatalog();
  }, [refreshCatalog]);

  useEffect(() => {
    const controller = new AbortController();
    // eslint-disable-next-line react-hooks/set-state-in-effect -- reloadKey 变化时点亮加载态并重新拉取，是动作驱动重置
    setLoading(true);
    setLoadError(null);
    voidCall(
      reload(controller.signal)
        .catch((e) => {
          if (!controller.signal.aborted) setLoadError(errMsg(e));
        })
        .finally(() => {
          if (!controller.signal.aborted) setLoading(false);
        }),
    );
    return () => {
      controller.abort();
    };
  }, [reload, reloadKey]);

  /** 使用各端点的模型行：只有自定义供应商的模型行能指定端点。 */
  const usagesByEndpoint = useMemo(() => groupUsagesByEndpoint(providers), [providers]);

  // 本节管的是自定义端点，内置端点只作参照。自定义端点的媒体类型由定义自己声明（一份
  // ComfyUI workflow 可以产图），因此自定义端点不按 video 过滤——否则导进来的图像端点在设置页
  // 里既看不到也删不掉。内置端点默认只列视频；来自模型行的深链也展示其选中的非视频端点。
  const sectionCatalog = useMemo(
    () => catalog.filter((endpoint) => endpoint.media_type === "video" || endpoint.source === "custom" || endpoint.key === selectedKey),
    [catalog, selectedKey],
  );

  const railGroups = useMemo<SecondaryRailGroup[]>(() => {
    const usageText = (key: string) => {
      const count = usagesByEndpoint.get(key)?.length ?? 0;
      return count > 0 ? t("ce_rail_usage", { count }) : t("ce_rail_unused");
    };
    const toItem = (descriptor: EndpointDescriptor): SecondaryRailItem => {
      const Icon = descriptor.kind === "python" ? Lock : descriptor.kind === "comfyui" ? Workflow : FileJson2;
      return {
        id: descriptor.key,
        label: descriptor.display_name ?? t(descriptor.display_name_key),
        description: usageText(descriptor.key),
        icon: <Icon className="size-4" />,
        href: hrefOf(descriptor.key),
      };
    };
    // 刚导入还没保存的 ComfyUI 端点只在选中时列出：离开即放弃（离开前由拦截询问）。
    const draftItem: SecondaryRailItem[] =
      comfyuiDraft && comfyuiDraft.record === null && selectedKey === COMFYUI_DRAFT_KEY
        ? [
            {
              id: COMFYUI_DRAFT_KEY,
              label: comfyuiDraft.definition.meta.name || t("ce_cf_draft_entry"),
              description: t("ce_rail_unsaved"),
              icon: <Workflow className="size-4" />,
              href: hrefOf(COMFYUI_DRAFT_KEY),
            },
          ]
        : [];
    return [
      {
        id: "mine",
        label: t("ce_group_mine"),
        items: [...draftItem, ...sectionCatalog.filter((e) => e.source === "custom").map(toItem)],
        action: {
          id: NEW_KEY,
          label: t("ce_new_endpoint"),
          icon: <Plus className="size-4" />,
          href: hrefOf(NEW_KEY),
        },
        emptyText: t("ce_rail_mine_empty"),
      },
      {
        id: "builtin",
        label: t("ce_group_builtin"),
        items: sectionCatalog.filter((e) => e.source === "builtin").map(toItem),
      },
    ];
  }, [sectionCatalog, usagesByEndpoint, comfyuiDraft, selectedKey, hrefOf, t]);

  const selection = useMemo((): EndpointSelection | null => {
    // 刚导入还没保存的那份压过同一个键上的已保存定义——不然重新导入一回来就看不见了。
    if (comfyuiDraft && (comfyuiDraft.record?.key ?? COMFYUI_DRAFT_KEY) === selectedKey) {
      return { mode: "comfyui-draft", draft: comfyuiDraft };
    }
    if (selectedKey === NEW_KEY) {
      return { mode: "new", definition: newEndpointDefinition("") };
    }
    const record = customEndpoints.find((e) => e.key === selectedKey);
    if (record) {
      // 详情表单只吃声明式定义；ComfyUI 端点走它自己那一路，否则表单会解引用它没有的 submit / poll。
      return isDeclarativeDefinition(record.definition)
        ? { mode: "custom", record, definition: record.definition }
        : { mode: "comfyui", record, definition: record.definition };
    }
    const descriptor = sectionCatalog.find((e) => e.key === selectedKey);
    if (!descriptor) return null;
    return descriptor.kind === "python"
      ? { mode: "python", descriptor }
      : { mode: "builtin", descriptor };
  }, [selectedKey, customEndpoints, sectionCatalog, comfyuiDraft]);

  // 地址里没有选中项、或指向已不存在的新导入草稿时，选中第一个端点（我的端点优先）；与供应商分区的兜底一致，用 replace。
  const firstKey = railGroups.find((group) => group.items.length > 0)?.items[0]?.id ?? null;
  const needsFallback = selectedKey === null || (selectedKey === COMFYUI_DRAFT_KEY && selection === null);
  useEffect(() => {
    if (loading || loadError || !needsFallback || firstKey === null) return;
    select(firstKey);
  }, [loading, loadError, needsFallback, firstKey, select]);

  const back = useMemo((): EndpointBackTarget | null => {
    if (fromProviderId === undefined) return null;
    const provider = providers.find((p) => p.id === fromProviderId);
    return provider ? { providerId: provider.id, providerName: provider.display_name } : null;
  }, [fromProviderId, providers]);

  // --- 导入 ---

  // 连续选文件时后一次接管：作废在途的读取与校验，避免第二个文件的定义配上
  // 第一个文件的校验结果。
  const importRunRef = useRef(0);

  /** 校验一份载荷并接手结果；换媒体类型时拿同一份载荷再跑一次。 */
  const validatePayload = useCallback(
    async (payload: unknown, mediaType: ComfyuiMediaType, run: number) => {
      // 不带 kind 的载荷此刻还没有定义身份：服务端按 workflow 收下时，包装结果随校验结果回来。
      const picked = hasKind(payload) ? payload : null;
      setImportDefinition(picked);
      setImportPending(true);
      try {
        const result = await API.validateCustomEndpoint(payload, {
          mediaType,
          excludeId: reimportBase?.record?.id,
        });
        if (importRunRef.current !== run) return;
        setImportDefinition(result.wrapped_definition ?? picked);
        setImportValidation(result);
      } finally {
        if (importRunRef.current === run) setImportPending(false);
      }
    },
    [reimportBase],
  );

  /** 清掉上一次交出去的载荷与它的结果；弹窗开着时用户可以接着再交一份。 */
  const resetImportSource = useCallback(() => {
    importRunRef.current += 1;
    setImportFileName("");
    setImportDefinition(null);
    setImportValidation(null);
    setImportPayload(null);
    setImportPending(false);
  }, []);

  /** 接下弹窗交出来的一份载荷：上传的文件与粘贴的文本走同一条分流。 */
  const takeImportSource = useCallback(
    async (text: string, fileName: string) => {
      const run = ++importRunRef.current;
      setImportFileName(fileName);
      setImportValidation(null);
      setImportDefinition(null);
      setImportPayload(null);
      let parsed: unknown;
      try {
        parsed = JSON.parse(text);
      } catch {
        // `JSON.parse` 抛的是英文语法错误，对着它用户也不知道要改什么；弹窗留在原处让他改完再交一次。
        setImportPending(false);
        pushToast(t("ce_import_read_failed"), "error");
        return;
      }
      setImportPayload(parsed);
      try {
        await validatePayload(parsed, importMediaType, run);
      } catch (e) {
        if (importRunRef.current !== run) return;
        pushToast(errMsg(e, t("ce_import_read_failed")), "error");
      }
    },
    [importMediaType, validatePayload, pushToast, t],
  );

  /** 换媒体类型：原始 workflow 按哪一种包装由它决定，包装结果与诊断都要重来一遍。 */
  const handleImportMediaTypeChange = useCallback(
    (mediaType: ComfyuiMediaType) => {
      setImportMediaType(mediaType);
      if (importPayload === null) return;
      const run = ++importRunRef.current;
      setImportValidation(null);
      voidCall(
        validatePayload(importPayload, mediaType, run).catch((e) => {
          if (importRunRef.current === run) pushToast(errMsg(e), "error");
        }),
      );
    },
    [importPayload, validatePayload, pushToast],
  );

  const finishImport = useCallback(
    async (saved: CustomEndpointInfo) => {
      setImportOpen(false);
      await reload();
      select(saved.key);
    },
    [reload, select],
  );

  /**
   * ComfyUI 的两种形状都不在弹窗里直接落盘，而是先进绑定编辑器：包装出来的定义节点绑定是空的，
   * 带 `kind` 的定义也要让用户把沿用 / 已重匹配 / 需确认再过一遍（`docs/adr/0082`）。
   */
  const handleImportBindings = useCallback(async () => {
    if (!importDefinition || !isComfyuiDefinition(importDefinition) || !importValidation) return;
    // 推断期间用户可以取消弹窗，也可以再交一份载荷；两者都递增这个号，回来发现号变了就整份丢弃
    // ——迟到的那一份会把一个已经被放弃的 workflow 装进详情并跳过去。
    const run = importRunRef.current;
    setImportBusy("infer");
    try {
      const base = reimportBase
        ? reimportedDefinition(
            reimportBase.definition,
            importDefinition,
            importValidation.import_shape === "endpoint_definition",
          )
        : importDefinition;
      const inference = await API.inferComfyuiBindings(base, { mediaType: base.media_type });
      if (importRunRef.current !== run) return;
      const record = reimportBase?.record ?? null;
      draftSeq.current += 1;
      setComfyuiDraft({
        token: String(draftSeq.current),
        record,
        definition: base,
        fileName: importFileName,
        inference,
      });
      setImportOpen(false);
      select(record ? record.key : COMFYUI_DRAFT_KEY);
    } catch (e) {
      if (importRunRef.current === run) pushToast(errMsg(e, t("ce_import_failed")), "error");
    } finally {
      if (importRunRef.current === run) setImportBusy(null);
    }
  }, [importDefinition, importValidation, importFileName, reimportBase, select, pushToast, t]);

  /** 关掉导入弹窗：在途的识别与推断一并作废，回来的那一份不再装进详情。 */
  const closeImport = useCallback(() => {
    importRunRef.current += 1;
    setImportBusy(null);
    setImportPending(false);
    setImportOpen(false);
  }, []);

  const openImport = useCallback(() => {
    setReimportBase(null);
    setImportMediaType("video");
    resetImportSource();
    setImportOpen(true);
  }, [resetImportSource]);

  /** 为当前这个 ComfyUI 端点换一份 workflow：身份与已确认的节点绑定沿用手上这一份。 */
  const startComfyuiReimport = useCallback(
    (current: ComfyuiEndpointDefinition) => {
      // 草稿的身份只在它就是当前选中的那一个时才算数：手上留着端点 A 的未保存草稿、人却走到
      // 端点 B 上点了重新导入时，沿用 A 的 record 会把 B 的 workflow 存到 A 身上。
      const draftRecord = comfyuiDraft?.record?.key === selectedKey ? comfyuiDraft.record : null;
      const record = draftRecord ?? customEndpoints.find((endpoint) => endpoint.key === selectedKey) ?? null;
      setReimportBase({ record, definition: current });
      setImportMediaType(current.media_type);
      resetImportSource();
      setImportOpen(true);
    },
    [comfyuiDraft, customEndpoints, selectedKey, resetImportSource],
  );

  const handleImportCreate = useCallback(async () => {
    if (!importDefinition) return;
    setImportBusy("save");
    try {
      await finishImport(await API.createCustomEndpoint(importDefinition));
    } catch (e) {
      pushToast(errMsg(e, t("ce_import_failed")), "error");
    } finally {
      setImportBusy(null);
    }
  }, [importDefinition, finishImport, pushToast, t]);

  const handleImportOverwrite = useCallback(
    async (id: number) => {
      if (!importDefinition) return;
      setImportBusy("save");
      try {
        await finishImport(await API.updateCustomEndpoint(id, importDefinition));
      } catch (e) {
        pushToast(errMsg(e, t("ce_import_failed")), "error");
      } finally {
        setImportBusy(null);
      }
    },
    [importDefinition, finishImport, pushToast, t],
  );

  // --- 从市场更新 ---

  useEffect(() => {
    marketUpdateRef.current?.abort();
  }, [selectedKey]);

  useEffect(
    () => () => {
      marketUpdateRef.current?.abort();
      marketUpdateRef.current = null;
    },
    [],
  );

  const handleUpdateFromMarket = useCallback(
    async (
      installation: EndpointInstallation,
      currentDefinition: EndpointDefinition,
      hasUnsavedChanges: boolean,
    ) => {
      if (installation.source_id === null) return;
      marketUpdateRef.current?.abort();
      const controller = new AbortController();
      marketUpdateRef.current = controller;
      setMarketUpdatePending(true);
      try {
        const detail = await API.getMarketEntry(installation.source_id, installation.slug, {
          signal: controller.signal,
        });
        if (!controller.signal.aborted) {
          setMarketUpdateTarget({ entry: detail.entry, currentDefinition, hasUnsavedChanges });
        }
      } catch (e) {
        if (!controller.signal.aborted) pushToast(errMsg(e), "error");
      } finally {
        if (marketUpdateRef.current === controller) {
          marketUpdateRef.current = null;
          setMarketUpdatePending(false);
        }
      }
    },
    [pushToast],
  );


  // --- 接线到供应商 ---

  const handleCreateProvider = useCallback(
    (definition: EndpointDefinition, endpointKey: string) => {
      navigate(providerSettingsPath({ newCustom: { endpoint: endpointKey, baseUrl: definition.meta.hints?.base_url } }));
    },
    [navigate],
  );

  const handleNavigateToModel = useCallback(
    (reference: EndpointReference) => {
      navigate(providerSettingsPath({ custom: reference.provider_id, model: reference.model_id }));
    },
    [navigate],
  );

  const handleDiscardComfyui = useCallback(() => {
    // 导入的未保存定义整份丢掉：新导入的由兜底选中落回第一个端点，重新导入的落回已保存的那份。
    setComfyuiDraft(null);
    setResetEpoch((n) => n + 1);
  }, []);

  if (loadError) {
    return (
      <div role="alert" className="flex flex-col items-start gap-3 p-6">
        <p className="text-sm font-medium text-warn">{t("common:load_failed")}</p>
        <p className="text-sm text-subtle-foreground">{loadError}</p>
        <Button variant="outline" size="sm" onClick={() => setReloadKey((k) => k + 1)}>
          {t("common:retry")}
        </Button>
      </div>
    );
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2 p-6 text-sm text-muted-foreground">
        <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
        {t("common:loading")}
      </div>
    );
  }

  return (
    // 全出血档：二级栏与详情栏各自滚动
    <div className="flex min-h-0 min-w-0 flex-1">
      <SecondaryRail label={t("ce_rail_label")} groups={railGroups} activeId={selectedKey} replace />

      {selection ? (
        <EndpointDetail
          key={detailInstanceKey(selectedKey, selection, resetEpoch)}
          selection={selection}
          providers={providers}
          usages={(selectedKey && usagesByEndpoint.get(selectedKey)) || []}
          back={back}
          onSaved={(record) => {
            // 未保存的导入定义已经落盘，让位给列表里那一条，否则同一个键上两份定义谁也说不清。
            setComfyuiDraft(null);
            voidCall(reload().then(() => select(record.key)));
          }}
          onDeleted={() => {
            setComfyuiDraft(null);
            voidCall(reload().then(() => select(null)));
          }}
          onCopied={(record) => {
            voidCall(reload().then(() => select(record.key)));
          }}
          onCreateProvider={handleCreateProvider}
          onNavigateToModel={handleNavigateToModel}
          onImport={openImport}
          onUpdateFromMarket={(installation, currentDefinition, hasUnsavedChanges) =>
            void handleUpdateFromMarket(installation, currentDefinition, hasUnsavedChanges)
          }
          marketUpdatePending={marketUpdatePending}
          onReimportComfyui={startComfyuiReimport}
          onDiscardComfyui={handleDiscardComfyui}
          share={
            submissions
              ? {
                  submission:
                    (selection.mode === "custom" || selection.mode === "comfyui"
                      ? submissions.get(selection.record.id)
                      : undefined) ?? null,
                  onSubmitted: (submission) =>
                    setSubmissions((current) => new Map(current).set(submission.endpoint_id, submission)),
                }
              : undefined
          }
        />
      ) : (
        <DetailPane>
          <p className="p-6 text-sm text-muted-foreground">{t("ce_select_endpoint")}</p>
        </DetailPane>
      )}

      <EndpointImportDialog
        open={importOpen}
        fileName={importFileName}
        definition={importDefinition}
        validation={importValidation}
        busy={importBusy !== null}
        saving={importBusy === "save"}
        pending={importPending}
        mediaType={importMediaType}
        onSource={(text, name) => void takeImportSource(text, name)}
        onMediaTypeChange={handleImportMediaTypeChange}
        onCreateCopy={() => void handleImportCreate()}
        onOverwrite={(id) => void handleImportOverwrite(id)}
        onBindNodes={() => void handleImportBindings()}
        onCancel={closeImport}
      />

      {marketUpdateTarget && (
        <MarketInstallDialog
          key={`${marketUpdateTarget.entry.source_id}/${marketUpdateTarget.entry.slug}`}
          entry={marketUpdateTarget.entry}
          currentEndpointDefinition={marketUpdateTarget.currentDefinition}
          hasUnsavedEndpointChanges={marketUpdateTarget.hasUnsavedChanges}
          onClose={() => setMarketUpdateTarget(null)}
          onInstallationChange={(installation) => {
            voidCall(reload().then(() => (installation === null ? select(null) : undefined)));
          }}
        />
      )}
    </div>
  );
}
