import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { Copy, Download, ExternalLink, Loader2, Plus, RefreshCw, Share2, Store, Trash2, Upload } from "lucide-react";
import { Link } from "wouter";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { settingsSectionPath } from "@/app-routes";
import { errMsg, voidCall } from "@/utils/async";
import { useAppStore } from "@/stores/app-store";
import { DetailPane } from "@/components/shared/master-detail/DetailPane";
import { SaveBar } from "@/components/shared/edit-unit/SaveBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogBody,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { DropdownMenuItem, DropdownMenuSeparator } from "@/components/ui/dropdown-menu";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import type {
  ComfyuiEndpointDefinition,
  CustomEndpointInfo,
  CustomProviderInfo,
  EndpointDefinition,
  EndpointDescriptor,
  EndpointInstallation,
  EndpointReference,
  EndpointValidateResponse,
  MarketSubmission,
} from "@/types";
import { MarketInstallBadges } from "../market/MarketInstallBadges";
import { MarketSubmissionBadge } from "../market/MarketSubmissionBadge";
import { MARKET_CONTRIBUTING_URL } from "../market/market-links";
import { isRenderableDefinition, slugFromName, type EndpointFormSection } from "./endpoint-definition-draft";
import { EndpointDiagnostics } from "./EndpointDiagnostics";
import { EndpointHeader, type EndpointBackTarget } from "./EndpointHeader";
import { EndpointReferenceList, endpointReferences } from "./EndpointReferenceList";
import { EndpointForm } from "./EndpointForm";
import { EndpointTestSection } from "./EndpointTestSection";
import { EndpointUsageList, type EndpointUsage } from "./EndpointUsageList";
import { exportEndpointDefinition } from "./export-endpoint-definition";
import { EXAMPLE_TEMPLATES } from "./example-templates";
import { VariableInsertionProvider } from "./endpoint-form-primitives";
import { ComfyuiEndpointDetail } from "./ComfyuiEndpointDetail";
import { ShareToMarketDialog } from "./ShareToMarketDialog";
import type { ComfyuiImportDraft } from "./comfyui-import";

const VALIDATE_DEBOUNCE_MS = 400;
/** 示例模板下拉里「空白定义」的取值；Select 的值不能是空串。 */
const BLANK_TEMPLATE = "blank";

/**
 * 选中项：新建端点、我的声明式端点、我的 ComfyUI 端点、刚导入还没保存的 ComfyUI 端点、
 * 内置声明式、内置 Python 六态。
 */
export type EndpointSelection =
  | { mode: "new"; definition: EndpointDefinition }
  | { mode: "custom"; record: CustomEndpointInfo; definition: EndpointDefinition }
  | { mode: "comfyui"; record: CustomEndpointInfo; definition: ComfyuiEndpointDefinition }
  | { mode: "comfyui-draft"; draft: ComfyuiImportDraft }
  | { mode: "builtin"; descriptor: EndpointDescriptor }
  | { mode: "python"; descriptor: EndpointDescriptor };

/** 我的端点：两种 kind 共用同一条保存记录，键、安装记录与删除入口都取自它。 */
function savedRecordOf(selection: EndpointSelection): CustomEndpointInfo | null {
  return selection.mode === "custom" || selection.mode === "comfyui" ? selection.record : null;
}

interface EndpointDetailProps {
  selection: EndpointSelection;
  providers: CustomProviderInfo[];
  /** 使用这个端点的模型行，来自自定义供应商的模型列表。 */
  usages: EndpointUsage[];
  /** 从自定义供应商跳来时的返回目标。 */
  back: EndpointBackTarget | null;
  onSaved: (record: CustomEndpointInfo) => void;
  onDeleted: () => void;
  onCopied: (record: CustomEndpointInfo) => void;
  onCreateProvider: (definition: EndpointDefinition, endpointKey: string) => void;
  onNavigateToModel: (reference: EndpointReference) => void;
  /** 打开导入对话框（新建端点页的入口）。 */
  onImport: () => void;
  /** 打开安装确认弹窗的更新态；只在市场轴可更新时提供入口。 */
  onUpdateFromMarket: (
    installation: EndpointInstallation,
    currentDefinition: EndpointDefinition,
    hasUnsavedChanges: boolean,
  ) => void;
  /** 更新弹窗所需的条目详情正在加载。 */
  marketUpdatePending: boolean;
  /** 为当前这个 ComfyUI 端点重新导入一份 workflow：新 workflow 接到传出去的这份定义上，回来走重匹配。 */
  onReimportComfyui: (current: ComfyuiEndpointDefinition) => void;
  /** 放弃 ComfyUI 端点的未保存修改：上层丢掉导入草稿并重建详情。 */
  onDiscardComfyui: () => void;
  /** 官方服务开启时提供：该端点最近一次分享提交与提交完成回调。 */
  share?: { submission: MarketSubmission | null; onSubmitted: (submission: MarketSubmission) => void };
}

/** 安装记录的来源描述：来源被删除时只剩规范键原文，禁用或删除都注明。 */
function MarketOrigin({ installation }: { installation: EndpointInstallation }) {
  const { t } = useTranslation("dashboard");
  const source = installation.source_display_name ?? installation.source_key;
  const key =
    installation.source_enabled === null
      ? "ce_from_market_source_deleted"
      : installation.source_enabled
        ? "ce_from_market"
        : "ce_from_market_source_disabled";
  return <span className="min-w-0 break-all">{t(key, { source })}</span>;
}

function kindLabelKey(selection: EndpointSelection): string {
  if (selection.mode === "python") return "ce_group_builtin_python";
  if (selection.mode === "builtin") return "ce_group_builtin";
  return "ce_kind_custom";
}

export function EndpointDetail({
  selection,
  providers,
  usages,
  back,
  onSaved,
  onDeleted,
  onCopied,
  onCreateProvider,
  onNavigateToModel,
  onImport,
  onUpdateFromMarket,
  marketUpdatePending,
  onReimportComfyui,
  onDiscardComfyui,
  share,
}: EndpointDetailProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);
  const templateLabelId = useId();

  const editable = selection.mode === "new" || selection.mode === "custom";
  // 市场更新弹窗按点击时的未保存修改判断是否提示覆盖，条目详情加载期间不再接受编辑。
  const readOnly = !editable || marketUpdatePending;
  const savedRecord = savedRecordOf(selection);
  const persistedId = savedRecord?.id ?? null;
  const installation = savedRecord?.installation ?? null;

  // 内置声明式端点的定义另行拉取，只读展示。
  const [builtinDefinition, setBuiltinDefinition] = useState<EndpointDefinition | null>(null);
  const [editorMode, setEditorMode] = useState<"form" | "json">("form");
  // JSON 片段编辑器自持文本状态：换端点或从 JSON 视图返回时递增，强制它按新定义重挂载。
  const [formEpoch, setFormEpoch] = useState(0);
  const [jsonText, setJsonText] = useState("");
  // parse：JSON 语法不通过；shape：语法通过但缺表单/头部直接解引用的容器结构。
  // 两种情况都不写回定义，文本保留供继续编辑。
  const [jsonIssue, setJsonIssue] = useState<"parse" | "shape" | null>(null);
  const [validation, setValidation] = useState<EndpointValidateResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [copying, setCopying] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteReferences, setDeleteReferences] = useState<EndpointReference[] | null>(null);
  const [deleting, setDeleting] = useState(false);
  const [shareOpen, setShareOpen] = useState(false);
  const [templateId, setTemplateId] = useState(BLANK_TEMPLATE);

  const builtinKey = selection.mode === "builtin" ? selection.descriptor.key : null;

  useEffect(() => {
    if (builtinKey === null) return;
    const controller = new AbortController();
    voidCall(
      API.getBuiltinEndpointDefinition(builtinKey)
        .then((definition) => {
          if (!controller.signal.aborted) setBuiltinDefinition(definition);
        })
        .catch((e) => {
          if (!controller.signal.aborted) setLoadError(errMsg(e));
        }),
    );
    return () => controller.abort();
  }, [builtinKey]);

  const hasErrors = (validation?.errors.length ?? 0) > 0;
  const saveBlocked = hasErrors || jsonIssue !== null;

  const saveDefinition = useCallback(
    async (value: EndpointDefinition | null): Promise<EndpointDefinition | void> => {
      if (!value) return;
      // 诊断有错误或 JSON 视图里的文本不成立时，服务端也不会收；把原因留在保存栏里。
      if (saveBlocked) throw new Error(t("ce_save_blocked"));
      const saved =
        persistedId === null
          ? await API.createCustomEndpoint(value)
          : await API.updateCustomEndpoint(persistedId, value);
      onSaved(saved);
      return isRenderableDefinition(saved.definition) ? saved.definition : value;
    },
    [saveBlocked, persistedId, onSaved, t],
  );

  const editableSource = selection.mode === "new" || selection.mode === "custom" ? selection.definition : null;
  const unit = useEditUnit<EndpointDefinition | null>({ source: editableSource, save: saveDefinition });
  const draft = editable ? unit.value : builtinDefinition;
  const hasUnsavedChanges = unit.dirty || jsonIssue !== null;

  const draftJson = useMemo(() => (draft ? JSON.stringify(draft) : null), [draft]);

  // 诊断与保存共用服务端校验器，编辑期持续复核。
  useEffect(() => {
    if (!editable || draftJson === null) return;
    const controller = new AbortController();
    const timer = setTimeout(() => {
      voidCall(
        API.validateCustomEndpoint(JSON.parse(draftJson), {
          excludeId: persistedId ?? undefined,
          signal: controller.signal,
        })
          .then((result) => {
            if (!controller.signal.aborted) setValidation(result);
          })
          .catch(() => {
            // 校验请求本身失败时保留上一轮结果，不把网络问题呈现成定义错误。
          }),
      );
    }, VALIDATE_DEBOUNCE_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [draftJson, editable, persistedId]);

  const enterJsonMode = () => {
    setJsonText(JSON.stringify(draft ?? {}, null, 2));
    setJsonIssue(null);
    setEditorMode("json");
  };

  const leaveJsonMode = () => {
    setFormEpoch((n) => n + 1);
    setEditorMode("form");
  };

  /** 用示例模板整份替换新建端点的定义；两种视图都按新定义重挂载。 */
  const applyTemplate = (id: string) => {
    if (selection.mode !== "new") return;
    const template = EXAMPLE_TEMPLATES.find((item) => item.id === id);
    const next = structuredClone(template ? template.definition : selection.definition);
    setTemplateId(id);
    unit.setValue(next);
    setJsonText(JSON.stringify(next, null, 2));
    setJsonIssue(null);
    setFormEpoch((n) => n + 1);
  };

  const handleDelete = useCallback(async () => {
    if (persistedId === null) return;
    setDeleting(true);
    try {
      await API.deleteCustomEndpoint(persistedId);
      setConfirmDelete(false);
      onDeleted();
    } catch (e) {
      const references = endpointReferences(e);
      if (references) {
        setDeleteReferences(references);
        return;
      }
      pushToast(errMsg(e, t("ce_delete_failed")), "error");
    } finally {
      setDeleting(false);
    }
  }, [persistedId, onDeleted, pushToast, t]);

  const handleCopyAsMine = useCallback(async () => {
    if (!draft) return;
    setCopying(true);
    try {
      onCopied(await API.createCustomEndpoint(draft));
    } catch (e) {
      pushToast(errMsg(e, t("ce_copy_failed")), "error");
    } finally {
      setCopying(false);
    }
  }, [draft, onCopied, pushToast, t]);

  /**
   * 定位到诊断所指的分节；`enum_maps`、`defaults` 这类表单没有控件的字段落在
   * JSON 视图。已在目标视图时不重挂载表单——重挂载会重置正在编辑的 JSON 片段。
   */
  const locateSection = (section: EndpointFormSection | null) => {
    if (section === null) {
      if (editorMode !== "json") enterJsonMode();
      return;
    }
    if (editorMode === "json") leaveJsonMode();
    requestAnimationFrame(() => {
      document.getElementById(`ce-section-${section}`)?.scrollIntoView({ block: "start" });
    });
  };

  const title =
    selection.mode === "new"
      ? t("ce_new_endpoint")
      : savedRecord
        ? savedRecord.display_name || t("ce_unnamed")
        : selection.mode === "builtin" || selection.mode === "python"
          ? (selection.descriptor.display_name ?? t(selection.descriptor.display_name_key))
          : t("ce_unnamed");

  const endpointKey = savedRecord
    ? savedRecord.key
    : selection.mode === "builtin" || selection.mode === "python"
      ? selection.descriptor.key
      : null;

  // 「更多操作」里与 kind 无关的条目：删除、分享与它们的对话框两种详情共用。
  const openDelete = () => {
    setDeleteReferences(null);
    setConfirmDelete(true);
  };
  const deleteItem = persistedId !== null && (
    <DropdownMenuItem variant="destructive" onClick={openDelete}>
      <Trash2 aria-hidden />
      {t("ce_delete_action")}
    </DropdownMenuItem>
  );
  const shareItem = (unsaved: boolean, pending: boolean): ReactNode =>
    share &&
    persistedId !== null && (
      <DropdownMenuItem disabled={pending || marketUpdatePending || unsaved} onClick={() => setShareOpen(true)}>
        <Share2 aria-hidden />
        {unsaved ? t("market_share_save_first") : t("market_share_action")}
      </DropdownMenuItem>
    );
  const submissionBadge = share?.submission && <MarketSubmissionBadge submission={share.submission} />;

  const dialogs = (
    <>
      <AlertDialog
        open={confirmDelete}
        onOpenChange={(open) => {
          // 删除请求在途时不响应 Esc 与遮罩点击
          if (!open && !deleting) {
            setConfirmDelete(false);
            setDeleteReferences(null);
          }
        }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t("ce_delete_title")}</AlertDialogTitle>
            {!deleteReferences && <AlertDialogDescription>{t("ce_delete_desc", { name: title })}</AlertDialogDescription>}
          </AlertDialogHeader>
          {deleteReferences && (
            <AlertDialogBody>
              <EndpointReferenceList references={deleteReferences} onNavigateToModel={onNavigateToModel} />
            </AlertDialogBody>
          )}
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>{t("common:cancel")}</AlertDialogCancel>
            {!deleteReferences && (
              <Button variant="destructive" disabled={deleting} onClick={() => void handleDelete()}>
                {deleting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
                {t("common:delete")}
              </Button>
            )}
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
      {shareOpen && share && persistedId !== null && (
        <ShareToMarketDialog
          endpointId={persistedId}
          initialSlug={
            share.submission?.slug ??
            installation?.slug ??
            slugFromName(selection.mode === "comfyui" ? selection.definition.meta.name : (draft?.meta.name ?? ""))
          }
          onClose={() => setShareOpen(false)}
          onSubmitted={(submission) => {
            // 提交结果由页头的分享状态标记表达
            setShareOpen(false);
            share.onSubmitted(submission);
          }}
        />
      )}
    </>
  );

  // ComfyUI 端点的定义是 workflow 加节点绑定，没有声明式表单的 submit / poll 两节，
  // 详情与绑定编辑器另有其形；删除与分享入口仍由本组件提供，两种 kind 共用同一条生命周期。
  if (selection.mode === "comfyui" || selection.mode === "comfyui-draft") {
    const draftRecord = selection.mode === "comfyui" ? selection.record : selection.draft.record;
    // 端点测试的凭证来源只列 comfyui 协议的供应商：别的协议的地址与密钥打不到一台 ComfyUI 上。
    const comfyuiProviders = providers.filter((provider) => provider.discovery_format === "comfyui");
    return (
      <>
        <ComfyuiEndpointDetail
          record={draftRecord}
          definition={selection.mode === "comfyui" ? selection.definition : selection.draft.definition}
          sourceFileName={selection.mode === "comfyui" ? null : selection.draft.fileName}
          initialInference={selection.mode === "comfyui" ? null : selection.draft.inference}
          usages={usages}
          back={back}
          providers={comfyuiProviders}
          onSaved={onSaved}
          onReimport={onReimportComfyui}
          onDiscard={onDiscardComfyui}
          menuItems={(unsaved, pending) => (
            <>
              {shareItem(unsaved, pending)}
              {deleteItem && (
                <>
                  <DropdownMenuSeparator />
                  {deleteItem}
                </>
              )}
            </>
          )}
          submissionBadge={submissionBadge}
        />
        {dialogs}
      </>
    );
  }

  const exportItem = draft && (
    <DropdownMenuItem onClick={() => exportEndpointDefinition(draft, installation?.slug)}>
      <Download aria-hidden />
      {t("ce_export_json")}
    </DropdownMenuItem>
  );

  const createProviderButton = draft && endpointKey && (
    <Button variant="outline" onClick={() => onCreateProvider(draft, endpointKey)} title={t("ce_create_provider_hint")}>
      <Plus aria-hidden data-icon="inline-start" />
      {t("ce_create_provider")}
    </Button>
  );

  let primary: ReactNode = null;
  let menu: ReactNode = null;
  if (selection.mode === "new") {
    primary = (
      <Button variant="outline" onClick={onImport}>
        <Upload aria-hidden data-icon="inline-start" />
        {t("ce_import_definition")}
      </Button>
    );
  } else if (selection.mode === "custom") {
    primary = createProviderButton;
    menu = (
      <>
        {exportItem}
        {shareItem(hasUnsavedChanges, unit.status === "saving")}
        <DropdownMenuItem onClick={() => window.open(MARKET_CONTRIBUTING_URL, "_blank", "noopener,noreferrer")}>
          <ExternalLink aria-hidden />
          {t("ce_contribute_to_market")}
        </DropdownMenuItem>
        <DropdownMenuSeparator />
        {deleteItem}
      </>
    );
  } else if (selection.mode === "builtin") {
    primary = (
      <Button variant="outline" onClick={() => void handleCopyAsMine()} disabled={copying || !draft}>
        {copying ? (
          <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
        ) : (
          <Copy aria-hidden data-icon="inline-start" />
        )}
        {t("ce_copy_as_mine")}
      </Button>
    );
    menu = draft && endpointKey && (
      <>
        <DropdownMenuItem onClick={() => onCreateProvider(draft, endpointKey)}>
          <Plus aria-hidden />
          {t("ce_create_provider")}
        </DropdownMenuItem>
        {exportItem}
      </>
    );
  }

  const updateButton = installation?.state === "update_available" && (
    <Button
      variant="outline"
      onClick={() => draft && onUpdateFromMarket(installation, draft, hasUnsavedChanges)}
      disabled={marketUpdatePending || !draft}
    >
      {marketUpdatePending ? (
        <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
      ) : (
        <RefreshCw aria-hidden data-icon="inline-start" />
      )}
      {t("market_update")}
    </Button>
  );

  return (
    <>
      <DetailPane
        header={
          <EndpointHeader
            back={back}
            title={title}
            badges={
              <>
                <Badge variant={editable ? "secondary" : "outline"}>{t(kindLabelKey(selection))}</Badge>
                {installation && <MarketInstallBadges state={installation.state} modified={installation.modified} />}
                {submissionBadge}
              </>
            }
            meta={
              (draft || installation) && (
                <>
                  {draft && selection.mode !== "new" && (
                    <span className="whitespace-nowrap">
                      {draft.meta.author} · v{draft.meta.version}
                    </span>
                  )}
                  {installation && <MarketOrigin installation={installation} />}
                </>
              )
            }
            primary={
              <>
                {updateButton}
                {primary}
              </>
            }
            menu={menu}
          />
        }
        // 保存栏与正文的表单列同宽同起点（正文 max-w-190 含左右各 24px 内边距）
        footer={editable ? <SaveBar unit={unit} className="max-w-178" /> : undefined}
      >
        <div className="flex max-w-190 flex-col gap-8 px-6 py-6">
          {selection.mode === "new" && (
            <Alert>
              <AlertDescription>
                {t("ce_new_desc")}{" "}
                <Link href={settingsSectionPath("market")} className={buttonVariants({ variant: "link", size: "sm" })}>
                  <Store aria-hidden data-icon="inline-start" />
                  {t("ce_get_from_market")}
                </Link>
              </AlertDescription>
            </Alert>
          )}

          {!editable && (
            <Alert>
              <AlertDescription>
                {selection.mode === "builtin" ? t("ce_builtin_readonly") : t("ce_python_readonly")}
              </AlertDescription>
            </Alert>
          )}

          {selection.mode === "python" && (
            <p className="rounded-lg border border-border px-4 py-3 font-mono text-sm break-all text-subtle-foreground" translate="no">
              {selection.descriptor.request_method} <span className="text-good">{selection.descriptor.request_path_template}</span>
            </p>
          )}

          {endpointKey && <EndpointUsageList usages={usages} />}

          {loadError && (
            <p role="alert" className="text-sm text-warn">
              {loadError}
            </p>
          )}

          {selection.mode !== "python" && !draft && !loadError && (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
              {t("common:loading")}
            </p>
          )}

          {draft && selection.mode !== "python" && (
            <div className="flex flex-col gap-4">
              {editable && validation && (
                <EndpointDiagnostics errors={validation.errors} warnings={validation.warnings} onLocate={locateSection} />
              )}

              {selection.mode === "new" && (
                <div className="flex flex-col gap-1.5">
                  <span id={templateLabelId} className="text-sm font-medium">
                    {t("ce_template")}
                  </span>
                  <Select
                    items={[
                      { value: BLANK_TEMPLATE, label: t("ce_template_blank") },
                      ...EXAMPLE_TEMPLATES.map((template) => ({ value: template.id, label: t(template.labelKey) })),
                    ]}
                    value={templateId}
                    onValueChange={(value) => {
                      if (typeof value === "string") applyTemplate(value);
                    }}
                  >
                    <SelectTrigger aria-labelledby={templateLabelId} className="w-72">
                      <SelectValue />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value={BLANK_TEMPLATE}>{t("ce_template_blank")}</SelectItem>
                      {EXAMPLE_TEMPLATES.map((template) => (
                        <SelectItem key={template.id} value={template.id}>
                          {t(template.labelKey)}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-xs text-muted-foreground">{t("ce_template_hint")}</p>
                </div>
              )}

              <ToggleGroup
                aria-label={t("ce_view_switch")}
                variant="outline"
                size="sm"
                value={[editorMode]}
                onValueChange={(next: string[]) => {
                  const mode = next[0];
                  // 单选：再次点击已选项不取消选择
                  if (!mode || mode === editorMode) return;
                  if (mode === "json") enterJsonMode();
                  else leaveJsonMode();
                }}
              >
                <ToggleGroupItem value="form">{t("ce_view_form")}</ToggleGroupItem>
                <ToggleGroupItem value="json">{t("ce_view_json")}</ToggleGroupItem>
              </ToggleGroup>

              {editorMode === "json" ? (
                <div className="flex flex-col gap-1.5">
                  <Textarea
                    value={jsonText}
                    readOnly={readOnly}
                    spellCheck={false}
                    aria-label={t("ce_view_json")}
                    aria-invalid={jsonIssue !== null || undefined}
                    onChange={(e) => {
                      setJsonText(e.target.value);
                      let parsed: unknown;
                      try {
                        parsed = JSON.parse(e.target.value);
                      } catch {
                        setJsonIssue("parse");
                        return;
                      }
                      if (!isRenderableDefinition(parsed)) {
                        setJsonIssue("shape");
                        return;
                      }
                      unit.setValue(parsed);
                      setJsonIssue(null);
                    }}
                  />
                  {jsonIssue !== null && (
                    <p role="alert" className="text-sm text-warn">
                      {t(jsonIssue === "parse" ? "ce_json_parse_error" : "ce_json_shape_error")}
                    </p>
                  )}
                </div>
              ) : (
                <VariableInsertionProvider key={formEpoch}>
                  <EndpointForm definition={draft} onChange={(next) => unit.setValue(next)} readOnly={readOnly} />
                  <EndpointTestSection definition={draft} providers={providers} />
                </VariableInsertionProvider>
              )}
            </div>
          )}
        </div>
      </DetailPane>
      {dialogs}
    </>
  );
}
