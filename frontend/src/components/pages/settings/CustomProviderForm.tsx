import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import { CheckCircle2, Eye, EyeOff, Loader2, MoreHorizontal, Plus, Trash2, XCircle } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { TruncatedText } from "@/components/shared/TruncatedText";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuTrigger } from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { InputGroup, InputGroupAddon, InputGroupButton, InputGroupInput } from "@/components/ui/input-group";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { useAppStore } from "@/stores/app-store";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { useEndpointCatalogStore } from "@/stores/endpoint-catalog-store";
import type { CustomProviderInfo, EndpointKey } from "@/types";
import { errMsg, voidCall } from "@/utils/async";
import { DurationParseError } from "@/utils/duration_format";
import { formatNameList } from "@/utils/list-format";
import { FieldHint, FieldRow } from "./CustomProviderFieldRow";
import { DURATION_ERROR_KEY, ModelTable } from "./CustomProviderModelTable";
import {
  isComfyuiEndpoint,
  isComfyuiProtocol,
  mergeDiscoveredModels,
  toggleDefaultReducer,
  urlPreviewFor,
  type DiscoveryFormat,
} from "./customProviderHelpers";
import {
  discoveredToRow,
  effectiveModelRows,
  formFromProvider,
  newModelRow,
  newProviderForm,
  parsePositiveInt,
  rowToInput,
  type CustomProviderFormValue,
  type ModelRow,
} from "./customProviderFormState";

const DISCOVERY_FORMAT_OPTIONS: { value: DiscoveryFormat; labelKey: string }[] = [
  { value: "openai", labelKey: "discovery_format_openai" },
  { value: "google", labelKey: "discovery_format_google" },
  { value: "comfyui", labelKey: "discovery_format_comfyui" },
];

interface CustomProviderFormProps {
  /** 已保存的供应商；不传时是新建表单。 */
  existing?: CustomProviderInfo | null;
  /** 从「调用端点」接线过来的预填接口地址（定义里的 meta.hints.base_url）。 */
  initialBaseUrl?: string;
  /** 接线时预选的调用端点：新建表单据此起一行模型。 */
  initialEndpoint?: EndpointKey;
  /** 深链里的模型 ID：打开时展开并定位到这一行。 */
  focusModelId?: string;
  /** 保存成功后调用，带回保存后的供应商（新建时是刚创建的供应商，调用方据此选中它）。 */
  onSaved: (provider?: CustomProviderInfo) => void;
  /** 删除成功后调用。 */
  onDeleted?: () => void;
}

/**
 * 自定义供应商详情：打开即是表单，没有只读态。页头显示协议与接口地址，「删除供应商」在更多操作菜单；
 * 名称、接口地址、密钥、模型与并发上限是一个编辑单元，由详情栏底部的保存栏统一提交。
 */
export function CustomProviderForm({
  existing,
  initialBaseUrl,
  initialEndpoint,
  focusModelId,
  onSaved,
  onDeleted,
}: CustomProviderFormProps) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);

  // 端点目录（后端单一真相源）：媒体类型、默认互斥分组与协议配对都从这里读。
  const endpointToMediaType = useEndpointCatalogStore((s) => s.endpointToMediaType);
  const endpointToImageCapabilities = useEndpointCatalogStore((s) => s.endpointToImageCapabilities);
  const catalogEndpoints = useEndpointCatalogStore((s) => s.endpoints);
  const catalogInitialized = useEndpointCatalogStore((s) => s.initialized);
  const fetchEndpointCatalog = useEndpointCatalogStore((s) => s.fetch);
  useEffect(() => {
    void fetchEndpointCatalog();
  }, [fetchEndpointCatalog]);

  // 已保存的内容：新建表单在挂载时定下，此后保持同一个引用
  const [newForm] = useState(() => newProviderForm(initialBaseUrl, initialEndpoint));
  const source = useMemo(() => (existing ? formFromProvider(existing) : newForm), [existing, newForm]);

  // 接线过来的端点定协议：ComfyUI 端点只挂得上 ComfyUI 供应商（docs/adr/0081 的双向配对），
  // 让用户自己去把协议改过来就是先让他撞一次保存失败。端点目录是异步取的，因此这里取派生值：
  // 目录到齐后协议随之落定。
  const wiredFormat: DiscoveryFormat | null = useMemo(() => {
    const descriptor = catalogEndpoints.find((item) => item.key === initialEndpoint);
    return descriptor && isComfyuiEndpoint(descriptor) ? "comfyui" : null;
  }, [catalogEndpoints, initialEndpoint]);
  const formatOf = useCallback(
    (value: CustomProviderFormValue): DiscoveryFormat => value.pickedFormat ?? wiredFormat ?? "openai",
    [wiredFormat],
  );
  const deriveModels = useCallback(
    (value: CustomProviderFormValue) =>
      effectiveModelRows(
        value.models,
        catalogInitialized ? catalogEndpoints : null,
        isComfyuiProtocol(formatOf(value)),
      ),
    [catalogInitialized, catalogEndpoints, formatOf],
  );

  // 删除后离开：供应商已不存在，未保存的修改无处可存，这次跳转不经离开拦截询问。
  // （新建后跳到新供应商不需要放行：跳转发生在保存在途时，拦截会等保存落定、修改清空后放行。）
  const deletedRef = useRef(false);
  const allowNavigation = useCallback(() => deletedRef.current, []);

  const save = useCallback(
    async (value: CustomProviderFormValue): Promise<CustomProviderFormValue | void> => {
      const discoveryFormat = formatOf(value);
      const keyOptional = value.noApiKey || isComfyuiProtocol(discoveryFormat);
      const models = deriveModels(value);
      const enabledModels = models.filter((m) => m.is_enabled);
      // 保存失败的原因显示在保存栏上：先说用户自己填漏的字段，再说要等的端点目录
      const fail = (message: string): never => {
        throw new Error(message);
      };
      if (!value.displayName.trim()) fail(t("fill_provider_name"));
      if (!value.baseUrl.trim()) fail(t("fill_base_url"));
      if (!existing && !keyOptional && !value.apiKey.trim()) fail(t("fill_api_key"));
      if (enabledModels.length === 0) fail(t("enable_one_model"));
      if (enabledModels.some((m) => !m.model_id.trim())) fail(t("enabled_model_needs_id"));
      // 目录没取回来时行挂不挂得住当前协议判不了：接线端点还没解析出来，协议就回退成了 openai，
      // 那一行照样吃 422。两种情形都没有时表单停在默认协议与默认端点上，本来就不用查目录。
      const catalogPending =
        !catalogInitialized &&
        value.models.length > 0 &&
        (initialEndpoint !== undefined || value.pickedFormat !== null);
      if (catalogPending) fail(t("cp_endpoint_catalog_pending"));
      // 停在未选择端点上的行：服务端按行校验协议配对，一行没有端点整份配置都落不了库
      if (models.some((m) => !m.endpoint)) fail(t("cp_model_endpoint_unselected"));
      if (
        models.some(
          (m) => endpointToMediaType[m.endpoint] === "text" && parsePositiveInt(m.max_output_tokens_text) === undefined,
        )
      ) {
        fail(t("max_output_tokens_invalid"));
      }
      let payloadModels;
      try {
        payloadModels = models.map(rowToInput);
      } catch (e) {
        if (e instanceof DurationParseError) {
          fail(t("supported_durations_invalid", { message: t(DURATION_ERROR_KEY[e.code], e.params) }));
        }
        throw e;
      }
      const imageMax = parsePositiveInt(value.imageMaxWorkers);
      const videoMax = parsePositiveInt(value.videoMaxWorkers);
      const audioMax = parsePositiveInt(value.audioMaxWorkers);
      if (imageMax === undefined || videoMax === undefined || audioMax === undefined) fail(t("max_workers_invalid"));

      const workers = { image_max_workers: imageMax ?? null, video_max_workers: videoMax ?? null, audio_max_workers: audioMax ?? null };
      if (existing) {
        // 单个事务原子更新 provider + models
        await API.fullUpdateCustomProvider(existing.id, {
          display_name: value.displayName,
          base_url: value.baseUrl,
          ...(value.noApiKey ? { api_key: "" } : value.apiKey ? { api_key: value.apiKey } : {}),
          models: payloadModels,
          ...workers,
        });
        // 能力覆盖随本次保存落库，但它不落任何项目字段，在用的能力查询不会因 props 变化而重取；
        // 显式作废，让常驻的能力警告无需重新挂载组件即随新覆盖增减。
        useCapabilitiesStore.getState().invalidate();
        // 入库内容以重取结果为准（能力判定、全局引用由后端算出）；重取失败不算保存失败
        let updated: CustomProviderInfo | undefined;
        try {
          updated = await API.getCustomProvider(existing.id);
        } catch {
          updated = undefined;
        }
        onSaved(updated);
        return updated ? formFromProvider(updated) : { ...value, apiKey: "", models };
      }
      const created = await API.createCustomProvider({
        display_name: value.displayName,
        discovery_format: discoveryFormat,
        base_url: value.baseUrl,
        api_key: value.noApiKey ? "" : value.apiKey,
        models: payloadModels,
        ...workers,
      });
      useCapabilitiesStore.getState().invalidate();
      onSaved(created);
    },
    [formatOf, deriveModels, t, existing, catalogInitialized, initialEndpoint, endpointToMediaType, onSaved],
  );

  const unit = useEditUnit({ source, save, allowNavigation });
  const value = unit.value;
  const setValue = unit.setValue;
  const discoveryFormat = formatOf(value);
  const isComfyui = isComfyuiProtocol(discoveryFormat);
  const models = useMemo(() => deriveModels(value), [deriveModels, value]);

  const setField = useCallback(
    <K extends keyof CustomProviderFormValue>(key: K, next: CustomProviderFormValue[K]) =>
      setValue((prev) => ({ ...prev, [key]: next })),
    [setValue],
  );

  // 用户改动以派生后的行为基准写回，派生结果就此坐实。否则那些由派生兜底改写的字段（改挂的端点、
  // 随之作废的默认标记）会在下一次派生里被同一条规则再改一遍，用户的改动看不见效果。
  const updateModels = useCallback(
    (change: (rows: ModelRow[]) => ModelRow[]) =>
      setValue((prev) => ({ ...prev, models: change(deriveModels(prev)) })),
    [setValue, deriveModels],
  );

  // 展开的模型行：深链定位的模型默认展开；新建表单接线带来的行还没填模型 ID，也展开
  const [expanded, setExpanded] = useState<ReadonlySet<string>>(() => {
    if (!existing) return new Set(source.models.map((m) => m.key));
    const focused = source.models.find((m) => m.model_id === focusModelId);
    return new Set(focused ? [focused.key] : []);
  });
  const toggleExpanded = useCallback((key: string) => {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }, []);
  const tableRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!focusModelId) return;
    tableRef.current?.querySelector("[aria-expanded=true]")?.scrollIntoView({ block: "center" });
  }, [focusModelId]);

  const comfyuiEndpoints = useMemo(() => catalogEndpoints.filter(isComfyuiEndpoint), [catalogEndpoints]);
  // ComfyUI 协议下新行必须挂 ComfyUI 端点：没有可挂的端点时按钮禁用并给出去处
  const noComfyuiEndpointYet = isComfyui && comfyuiEndpoints.length === 0;
  const addModel = () => {
    const endpoint = isComfyui ? comfyuiEndpoints[0]?.key : undefined;
    const row = endpoint ? newModelRow({ endpoint }) : newModelRow();
    updateModels((rows) => [...rows, row]);
    setExpanded((prev) => new Set(prev).add(row.key));
  };

  // --- 测试连接与获取模型列表：用表单当前内容，不经保存 ---
  const [discovering, setDiscovering] = useState(false);
  const [testing, setTesting] = useState(false);
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null);
  const showError = useCallback((msg: string) => useAppStore.getState().pushToast(msg, "error"), []);

  // base_url 相对存储值是否变更：变更后必须用 UI 上的新地址 + 新密钥走明文路径，
  // 否则 by-id 端点会用 DB 中的旧 base_url，与保存的新地址错位。
  const baseUrlChanged = !!existing && value.baseUrl.trim() !== existing.base_url.trim();
  // 编辑时未输入新密钥且接口地址未变，用已存储凭证（by-id 端点）；新建或接口地址变更时必须明文
  // 密钥。勾选「无需密钥」后保存写入的是空密钥，测试与发现须同样走明文空密钥路径。
  const useStoredCredential = !!existing && !value.apiKey && !baseUrlChanged && !value.noApiKey;
  // ComfyUI 本体零鉴权，反向代理的凭据模板写在端点定义的 auth 节（docs/adr/0081），密钥留空是常态。
  const keyOptional = value.noApiKey || isComfyui;

  /** 测试连接与获取模型列表的前置检查；不满足时提示并返回 false。 */
  const checkConnectionInputs = () => {
    if (!value.baseUrl) {
      showError(t("fill_base_url_first"));
      return false;
    }
    if (!useStoredCredential && !keyOptional && !value.apiKey) {
      showError(t(baseUrlChanged ? "base_url_changed_reenter_key" : "fill_api_key_first"));
      return false;
    }
    return true;
  };

  const handleDiscover = async () => {
    if (!checkConnectionInputs()) return;
    setDiscovering(true);
    try {
      const res =
        useStoredCredential && existing
          ? await API.discoverModelsForProvider(existing.id)
          : await API.discoverModels({ discovery_format: discoveryFormat, base_url: value.baseUrl, api_key: value.apiKey });
      if (res.not_applicable) {
        showError(res.reason ?? t("discovery_not_applicable"));
        return;
      }
      const discovered = res.models.map(discoveredToRow);
      // 用 getState 读最新目录映射：目录在挂载时异步拉取，就绪前点「获取模型列表」时闭包里仍是空表，
      // 合并会跳过默认消解，保存时可能 default_model_conflict。
      const { endpointToMediaType: mediaMap, endpointToImageCapabilities: capsMap } =
        useEndpointCatalogStore.getState();
      updateModels((rows) => mergeDiscoveredModels(rows, discovered, mediaMap, capsMap));
    } catch (e) {
      showError(errMsg(e, t("fetch_models_failed")));
    } finally {
      setDiscovering(false);
    }
  };

  const handleTest = async () => {
    // 先清空上一次结果：校验失败直接返回时也不残留旧的成功或失败提示
    setTestResult(null);
    if (!checkConnectionInputs()) return;
    setTesting(true);
    try {
      const res =
        useStoredCredential && existing
          ? await API.checkCustomConnectivityById(existing.id)
          : await API.checkCustomConnectivity({
              discovery_format: discoveryFormat,
              base_url: value.baseUrl,
              api_key: value.apiKey,
            });
      setTestResult(res);
    } catch (e) {
      setTestResult({ success: false, message: errMsg(e, t("connectivity_check_failed")) });
    } finally {
      setTesting(false);
    }
  };

  // 勾选「无需密钥」后才需要知道哪些端点其实要密钥，因而只在那时拉一次自定义端点列表。
  // 内置端点一律按需要密钥处理：它们的定义都带 auth 节，无凭证接口只可能是用户自建的。
  // null 表示尚未成功拉到列表：此时不计算冲突提示——空 Set 会把全部模型行误报成需要密钥。
  const [authFreeEndpoints, setAuthFreeEndpoints] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (!value.noApiKey) return;
    const controller = new AbortController();
    voidCall(
      API.listCustomEndpoints({ signal: controller.signal })
        .then((res) => {
          if (controller.signal.aborted) return;
          setAuthFreeEndpoints(
            new Set(
              res.endpoints
                .filter((e) => {
                  const auth = e.definition?.auth;
                  return Object.keys(auth?.headers ?? {}).length === 0 && Object.keys(auth?.query ?? {}).length === 0;
                })
                .map((e) => e.key),
            ),
          );
        })
        .catch(() => {
          // 拉不到时不提示：联动提示是辅助信息，缺失好过误报。
        }),
    );
    return () => controller.abort();
  }, [value.noApiKey]);
  const keyRequiringModels =
    authFreeEndpoints === null
      ? []
      : models
          .filter((m) => m.is_enabled && m.model_id.trim() && !authFreeEndpoints.has(m.endpoint))
          .map((m) => m.model_id);

  // --- 删除供应商 ---
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const handleDelete = async () => {
    if (!existing) return;
    setDeleting(true);
    setDeleteError(null);
    try {
      await API.deleteCustomProvider(existing.id);
      // 该供应商上的模型与能力覆盖随之消失，项目会退回解析别的模型；这同样不落任何项目字段。
      useCapabilitiesStore.getState().invalidate();
      deletedRef.current = true;
      setConfirmDelete(false);
      onDeleted?.();
    } catch (e) {
      setDeleteError(errMsg(e));
    } finally {
      setDeleting(false);
    }
  };

  const [showApiKey, setShowApiKey] = useState(false);
  const nameId = useId();
  const urlId = useId();
  const keyId = useId();
  const formatId = useId();
  const modelsHeadingId = useId();
  const connectionHeadingId = useId();
  const concurrencyHeadingId = useId();
  const urlPreview = urlPreviewFor(discoveryFormat, value.baseUrl);
  const hasUnsetEndpoint = models.some((m) => !m.endpoint);
  const workersPlaceholder = isComfyui ? t("cp_max_workers_placeholder_comfyui") : t("cp_max_workers_placeholder");
  const protocolName = t(DISCOVERY_FORMAT_OPTIONS.find((o) => o.value === discoveryFormat)?.labelKey ?? "");

  const header = (
    <div className="flex items-start gap-3">
      {/* 自定义供应商不按名称猜品牌图标：中转站与协议无关，统一用名称首字 */}
      <span
        aria-hidden
        className="mt-0.5 inline-flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-muted text-sm text-subtle-foreground"
      >
        {existing ? (Array.from(existing.display_name)[0] ?? "?") : <Plus className="size-4" />}
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-1">
        <h2 className="min-w-0 text-lg font-medium">
          {existing ? <TruncatedText text={existing.display_name} /> : t("add_custom_provider_title")}
        </h2>
        {existing && (
          <p className="flex min-w-0 items-center gap-2 text-sm text-muted-foreground">
            <span className="shrink-0">{protocolName}</span>
            <span aria-hidden>·</span>
            <TruncatedText text={existing.base_url} className="font-mono" />
          </p>
        )}
      </div>
      <Button variant="outline" size="sm" disabled={testing} onClick={() => void handleTest()}>
        {testing && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
        {testing ? t("connectivity_checking") : t("connectivity_check")}
      </Button>
      {existing && (
        <DropdownMenu>
          <DropdownMenuTrigger
            render={<Button variant="ghost" size="icon-sm" aria-label={t("cp_more_actions")} />}
          >
            <MoreHorizontal />
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem
              variant="destructive"
              onClick={() => {
                setDeleteError(null);
                setConfirmDelete(true);
              }}
            >
              <Trash2 />
              {t("cp_delete_provider")}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      )}
    </div>
  );

  return (
    <DetailPane header={header} footer={<SaveBar unit={unit} className="max-w-6xl" />}>
      <div className="flex flex-col gap-10 px-6 py-6">
        <section aria-labelledby={connectionHeadingId} className="flex max-w-178 flex-col gap-4">
          <h3 id={connectionHeadingId} className="text-base font-medium">
            {t("cp_connection_heading")}
          </h3>
          <FieldRow label={t("cp_name_label")} htmlFor={nameId}>
            <Input
              id={nameId}
              autoComplete="off"
              value={value.displayName}
              onChange={(e) => setField("displayName", e.target.value)}
              placeholder={t("cp_name_placeholder")}
            />
          </FieldRow>
          <FieldRow
            label={t("discovery_format_label")}
            htmlFor={formatId}
            hint={<FieldHint>{existing ? t("cp_protocol_locked_hint") : t("discovery_format_help")}</FieldHint>}
          >
            <Select<DiscoveryFormat>
              value={discoveryFormat}
              onValueChange={(next) => next && setField("pickedFormat", next)}
              disabled={!!existing}
              items={DISCOVERY_FORMAT_OPTIONS.map((o) => ({ value: o.value, label: t(o.labelKey) }))}
            >
              <SelectTrigger id={formatId} className="w-56">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {DISCOVERY_FORMAT_OPTIONS.map((o) => (
                  <SelectItem key={o.value} value={o.value}>
                    {t(o.labelKey)}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </FieldRow>
          <FieldRow
            label={t("cp_base_url_label")}
            htmlFor={urlId}
            hint={
              urlPreview ? (
                <FieldHint>
                  {t("preview_url")}
                  <span className="font-mono break-all">{urlPreview}</span>
                </FieldHint>
              ) : undefined
            }
          >
            <Input
              id={urlId}
              type="url"
              autoComplete="off"
              value={value.baseUrl}
              onChange={(e) => setField("baseUrl", e.target.value)}
              placeholder="https://api.example.com"
            />
          </FieldRow>
          <FieldRow
            label={t("credential_secret_label")}
            htmlFor={value.noApiKey ? undefined : keyId}
            hint={
              value.noApiKey && keyRequiringModels.length > 0 ? (
                <FieldHint tone="warn">
                  {t("cp_no_api_key_conflict", { models: formatNameList(keyRequiringModels, i18n.language) })}
                </FieldHint>
              ) : undefined
            }
          >
            {!value.noApiKey && (
              <InputGroup>
                <InputGroupInput
                  id={keyId}
                  type={showApiKey ? "text" : "password"}
                  autoComplete="off"
                  value={value.apiKey}
                  onChange={(e) => setField("apiKey", e.target.value)}
                  placeholder={
                    existing ? existing.api_key_masked || t("keep_existing_key_hint") : t("enter_api_key_placeholder")
                  }
                />
                <InputGroupAddon align="inline-end">
                  <InputGroupButton
                    size="icon-xs"
                    aria-label={showApiKey ? t("common:hide") : t("common:show")}
                    onClick={() => setShowApiKey((v) => !v)}
                  >
                    {showApiKey ? <EyeOff aria-hidden /> : <Eye aria-hidden />}
                  </InputGroupButton>
                </InputGroupAddon>
              </InputGroup>
            )}
            <label className="flex items-center gap-2 text-sm text-subtle-foreground">
              <Checkbox
                checked={value.noApiKey}
                onCheckedChange={(checked) =>
                  setValue((prev) => ({ ...prev, noApiKey: checked, apiKey: checked ? "" : prev.apiKey }))
                }
              />
              {t("cp_no_api_key")}
            </label>
          </FieldRow>
          {/* ComfyUI 协议下探针可能被反向代理的自定义头鉴权挡住，据此提前说明判据。 */}
          {isComfyui && <p className="text-sm text-muted-foreground">{t("cp_comfyui_connectivity_hint")}</p>}
          {testResult && (
            <Alert variant={testResult.success ? "default" : "destructive"}>
              {testResult.success ? <CheckCircle2 aria-hidden /> : <XCircle aria-hidden />}
              <AlertDescription>{testResult.message}</AlertDescription>
            </Alert>
          )}
        </section>

        <section aria-labelledby={modelsHeadingId} className="flex max-w-6xl flex-col gap-3">
          <div className="flex flex-wrap items-center gap-2">
            <h3 id={modelsHeadingId} className="mr-auto text-base font-medium">
              {t("model_list")}
              {models.length > 0 && <span className="ml-2 text-muted-foreground tabular-nums">{models.length}</span>}
            </h3>
            {!isComfyui && (
              <Button variant="outline" size="sm" disabled={discovering} onClick={() => void handleDiscover()}>
                {discovering && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
                {discovering ? t("discovering_models") : t("discover_models")}
              </Button>
            )}
            <Button variant="outline" size="sm" disabled={noComfyuiEndpointYet} onClick={addModel}>
              <Plus aria-hidden data-icon="inline-start" />
              {t("add_model_manually")}
            </Button>
          </div>
          {/* comfyui 没有模型发现，用说明替代按钮 */}
          {isComfyui && <p className="text-sm text-muted-foreground">{t("discovery_not_applicable")}</p>}
          {hasUnsetEndpoint && <p className="text-sm text-warn">{t("cp_model_endpoint_unselected")}</p>}
          {models.length > 0 ? (
            <div ref={tableRef}>
              <ModelTable
                rows={models}
                protocol={discoveryFormat}
                providerId={existing?.id}
                expanded={expanded}
                onToggleExpanded={toggleExpanded}
                onUpdate={(key, patch) =>
                  updateModels((rows) => rows.map((m) => (m.key === key ? { ...m, ...patch } : m)))
                }
                onRemove={(key) => updateModels((rows) => rows.filter((m) => m.key !== key))}
                onToggleDefault={(key) =>
                  updateModels((rows) => toggleDefaultReducer(rows, key, endpointToMediaType, endpointToImageCapabilities))
                }
                onSetEnabled={(keys, enabled) =>
                  updateModels((rows) => rows.map((m) => (keys.includes(m.key) ? { ...m, is_enabled: enabled } : m)))
                }
              />
            </div>
          ) : (
            <p className="rounded-lg border border-dashed border-input px-4 py-6 text-center text-sm text-muted-foreground">
              {noComfyuiEndpointYet
                ? t("cp_comfyui_no_endpoint_hint")
                : isComfyui
                  ? t("cp_comfyui_add_model_hint")
                  : t("cp_models_empty_hint")}
            </p>
          )}
        </section>

        <section aria-labelledby={concurrencyHeadingId} className="flex max-w-178 flex-col gap-4">
          <div className="flex flex-col gap-1">
            <h3 id={concurrencyHeadingId} className="text-base font-medium">
              {t("cp_concurrency_label")}
            </h3>
            <p className="text-sm text-muted-foreground">
              {isComfyui ? t("cp_concurrency_help_comfyui") : t("cp_concurrency_help")}
            </p>
          </div>
          {(
            [
              ["imageMaxWorkers", "cp_image_max_workers_label"],
              ["videoMaxWorkers", "cp_video_max_workers_label"],
              ["audioMaxWorkers", "cp_audio_max_workers_label"],
            ] as const
          ).map(([field, labelKey]) => (
            <FieldRow key={field} label={t(labelKey)} htmlFor={`${concurrencyHeadingId}-${field}`}>
              <Input
                id={`${concurrencyHeadingId}-${field}`}
                type="number"
                min={1}
                step={1}
                inputMode="numeric"
                autoComplete="off"
                className="w-32"
                value={value[field]}
                onChange={(e) => setField(field, e.target.value)}
                placeholder={workersPlaceholder}
              />
            </FieldRow>
          ))}
        </section>
      </div>

      {existing && (
        <AlertDialog
          open={confirmDelete}
          onOpenChange={(open) => {
            // 删除请求在途时不响应 Esc
            if (!open && !deleting) setConfirmDelete(false);
          }}
        >
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t("cp_delete_provider_title", { name: existing.display_name })}</AlertDialogTitle>
            </AlertDialogHeader>
            <AlertDialogBody>
              <div className="flex flex-col gap-2">
                <AlertDialogDescription>
                  {t("cp_delete_provider_description", { count: existing.models.length })}
                </AlertDialogDescription>
                {deleteError && (
                  <p role="alert" className="text-sm wrap-break-word text-destructive">
                    {t("delete_failed", { message: deleteError })}
                  </p>
                )}
              </div>
            </AlertDialogBody>
            <AlertDialogFooter>
              <AlertDialogCancel disabled={deleting}>{t("common:cancel")}</AlertDialogCancel>
              <AlertDialogAction variant="destructive" disabled={deleting} onClick={() => void handleDelete()}>
                {deleting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
                {t("cp_delete_provider")}
              </AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      )}
    </DetailPane>
  );
}

