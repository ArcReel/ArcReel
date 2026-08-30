import { useCallback, useEffect, useMemo, useState } from "react";
import { Copy, Download, Loader2, Plus, Trash2 } from "lucide-react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { errMsg, voidCall } from "@/utils/async";
import { downloadBlob } from "@/utils/download";
import { useAppStore } from "@/stores/app-store";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import {
  ACCENT_BTN_SM_CLS,
  ACCENT_BUTTON_STYLE,
  CARD_STYLE,
  GHOST_BTN_CLS,
  INPUT_CLS,
} from "@/components/ui/darkroom-tokens";
import type {
  CustomEndpointInfo,
  CustomProviderInfo,
  EndpointDefinition,
  EndpointDescriptor,
  EndpointValidateResponse,
} from "@/types";
import { definitionFileName, isRenderableDefinition, type EndpointFormSection } from "./endpoint-definition-draft";
import { EndpointDiagnostics } from "./EndpointDiagnostics";
import { EndpointForm } from "./EndpointForm";
import { EndpointTestSection } from "./EndpointTestSection";
import { VariableInsertionProvider } from "./endpoint-form-primitives";

const VALIDATE_DEBOUNCE_MS = 400;

/** 选中项：新建草稿、我的端点、内置声明式、内置 Python 四态。 */
export type EndpointSelection =
  | { mode: "new"; definition: EndpointDefinition }
  | { mode: "custom"; record: CustomEndpointInfo }
  | { mode: "builtin"; descriptor: EndpointDescriptor }
  | { mode: "python"; descriptor: EndpointDescriptor };

interface EndpointDetailProps {
  selection: EndpointSelection;
  providers: CustomProviderInfo[];
  /** 引用该端点的模型行数量，来自自定义供应商的模型列表。 */
  referenceCount: number;
  onSaved: (record: CustomEndpointInfo) => void;
  onDeleted: () => void;
  onCopied: (record: CustomEndpointInfo) => void;
  onCreateProvider: (definition: EndpointDefinition, endpointKey: string) => void;
}

function KindBadge({ selection }: { selection: EndpointSelection }) {
  const { t } = useTranslation("dashboard");
  const custom = selection.mode === "new" || selection.mode === "custom";
  const label =
    selection.mode === "python"
      ? t("ce_group_builtin_python")
      : custom
        ? t("ce_kind_custom")
        : t("ce_group_builtin");
  return (
    <span
      className={`shrink-0 rounded-[5px] border px-1.5 py-0.5 font-mono text-[9.5px] font-bold uppercase tracking-[0.1em] ${
        custom ? "border-accent/35 bg-accent-dim text-accent-2" : "border-hairline-soft bg-bg-grad-a/55 text-text-3"
      }`}
    >
      {label}
    </span>
  );
}

export function EndpointDetail({
  selection,
  providers,
  referenceCount,
  onSaved,
  onDeleted,
  onCopied,
  onCreateProvider,
}: EndpointDetailProps) {
  const { t } = useTranslation(["dashboard", "common"]);
  const pushToast = useAppStore((s) => s.pushToast);

  const editable = selection.mode === "new" || selection.mode === "custom";
  const persistedId = selection.mode === "custom" ? selection.record.id : null;

  // 选中项由父级以 key 区分挂载，草稿因此可以直接由初始 selection 派生；
  // 只有内置声明式端点的定义需要另行拉取。
  const [draft, setDraft] = useState<EndpointDefinition | null>(() =>
    selection.mode === "new"
      ? selection.definition
      : selection.mode === "custom"
        ? selection.record.definition
        : null,
  );
  const [editorMode, setEditorMode] = useState<"form" | "json">("form");
  // JSON 片段编辑器自持文本状态：换端点或从 JSON 视图返回时递增，强制它按新定义重挂载。
  const [formEpoch, setFormEpoch] = useState(0);
  const [jsonText, setJsonText] = useState("");
  // parse：JSON 语法不通过；shape：语法通过但缺表单/头部直接解引用的容器结构。
  // 两种情况都不写回草稿，文本保留供继续编辑。
  const [jsonIssue, setJsonIssue] = useState<"parse" | "shape" | null>(null);
  const [validation, setValidation] = useState<EndpointValidateResponse | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const builtinKey = selection.mode === "builtin" ? selection.descriptor.key : null;

  useEffect(() => {
    if (builtinKey === null) return;
    const controller = new AbortController();
    voidCall(
      API.getBuiltinEndpointDefinition(builtinKey)
        .then((definition) => {
          if (!controller.signal.aborted) setDraft(definition);
        })
        .catch((e) => {
          if (!controller.signal.aborted) setLoadError(errMsg(e));
        }),
    );
    return () => controller.abort();
  }, [builtinKey]);

  const draftJson = useMemo(() => (draft ? JSON.stringify(draft) : null), [draft]);

  // 诊断卡与保存共用服务端校验器，编辑期持续复核。
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

  const dirty =
    selection.mode === "new" ||
    (selection.mode === "custom" && draftJson !== JSON.stringify(selection.record.definition));

  const hasErrors = (validation?.errors.length ?? 0) > 0;

  const enterJsonMode = () => {
    setJsonText(JSON.stringify(draft ?? {}, null, 2));
    setJsonIssue(null);
    setEditorMode("json");
  };

  const leaveJsonMode = () => {
    setFormEpoch((n) => n + 1);
    setEditorMode("form");
  };

  const handleSave = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const saved =
        persistedId === null
          ? await API.createCustomEndpoint(draft)
          : await API.updateCustomEndpoint(persistedId, draft);
      pushToast(t("ce_saved"), "success");
      onSaved(saved);
    } catch (e) {
      pushToast(errMsg(e, t("ce_save_failed")), "error");
    } finally {
      setSaving(false);
    }
  }, [draft, persistedId, onSaved, pushToast, t]);

  const handleDelete = useCallback(async () => {
    if (persistedId === null) return;
    setDeleting(true);
    try {
      await API.deleteCustomEndpoint(persistedId);
      setConfirmDelete(false);
      pushToast(t("ce_deleted"), "success");
      onDeleted();
    } catch (e) {
      pushToast(errMsg(e, t("ce_delete_failed")), "error");
    } finally {
      setDeleting(false);
    }
  }, [persistedId, onDeleted, pushToast, t]);

  const handleExport = useCallback(() => {
    if (!draft) return;
    const blob = new Blob([JSON.stringify(draft, null, 2)], { type: "application/json" });
    downloadBlob(blob, definitionFileName(draft));
  }, [draft]);

  const handleCopyAsMine = useCallback(async () => {
    if (!draft) return;
    setSaving(true);
    try {
      const created = await API.createCustomEndpoint(draft);
      pushToast(t("ce_copied"), "success");
      onCopied(created);
    } catch (e) {
      pushToast(errMsg(e, t("ce_copy_failed")), "error");
    } finally {
      setSaving(false);
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
      : selection.mode === "custom"
        ? selection.record.display_name || t("ce_unnamed")
        : (selection.descriptor.display_name ?? t(selection.descriptor.display_name_key));

  const endpointKey = selection.mode === "custom" ? selection.record.key : selection.mode === "new" ? null : selection.descriptor.key;

  return (
    <div className="px-6 py-6">
      {/* 头部 */}
      <div className="mb-5 flex flex-wrap items-start gap-3">
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2.5">
            <h2 className="font-editorial text-[20px] text-text">{title}</h2>
            <KindBadge selection={selection} />
          </div>
          <div className="mt-1 flex items-center gap-2.5 text-[12px] text-text-3">
            {draft && (
              <span>
                {draft.meta.author} · v{draft.meta.version}
              </span>
            )}
            {referenceCount > 0 && <span>{t("ce_reference_count", { n: referenceCount })}</span>}
          </div>
        </div>

        {draft && endpointKey && (
          <button
            type="button"
            onClick={() => onCreateProvider(draft, endpointKey)}
            title={t("ce_create_provider_hint")}
            className={GHOST_BTN_CLS}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden />
            {t("ce_create_provider")}
          </button>
        )}

        {editable ? (
          <>
            <button type="button" onClick={handleExport} disabled={!draft} className={GHOST_BTN_CLS}>
              <Download className="h-3.5 w-3.5" aria-hidden />
              {t("ce_export")}
            </button>
            {persistedId !== null && (
              <button
                type="button"
                onClick={() => setConfirmDelete(true)}
                disabled={referenceCount > 0}
                title={referenceCount > 0 ? t("ce_delete_blocked") : undefined}
                className={GHOST_BTN_CLS}
              >
                <Trash2 className="h-3.5 w-3.5" aria-hidden />
                {t("common:delete")}
              </button>
            )}
            <button
              type="button"
              onClick={() => void handleSave()}
              disabled={saving || hasErrors || jsonIssue !== null || !dirty}
              title={hasErrors ? t("ce_save_blocked") : undefined}
              className={ACCENT_BTN_SM_CLS}
              style={ACCENT_BUTTON_STYLE}
            >
              {saving && <Loader2 className="h-3 w-3 motion-safe:animate-spin" aria-hidden />}
              {t("ce_save")}
            </button>
          </>
        ) : (
          selection.mode === "builtin" && (
            <button
              type="button"
              onClick={() => void handleCopyAsMine()}
              disabled={saving || !draft}
              className={GHOST_BTN_CLS}
            >
              <Copy className="h-3.5 w-3.5" aria-hidden />
              {t("ce_copy_as_mine")}
            </button>
          )
        )}
      </div>

      {!editable && (
        <div className="mb-5 rounded-[10px] border border-hairline bg-bg-grad-a/40 px-4 py-3 text-[12.5px] leading-[1.55] text-text-2">
          {selection.mode === "builtin" ? t("ce_builtin_readonly") : t("ce_python_readonly")}
        </div>
      )}

      {selection.mode === "python" && (
        <div className="rounded-[10px] border border-hairline p-4 font-mono text-[12px] text-text-2" style={CARD_STYLE}>
          {selection.descriptor.request_method}{" "}
          <span className="text-good/85">{selection.descriptor.request_path_template}</span>
        </div>
      )}

      {loadError && (
        <p role="alert" className="text-[12.5px] text-warm-bright">
          {loadError}
        </p>
      )}

      {selection.mode !== "python" && !draft && !loadError && (
        <div className="flex items-center gap-2 py-8 text-text-3">
          <Loader2 className="h-3.5 w-3.5 motion-safe:animate-spin text-accent-2" aria-hidden />
          <span className="font-mono text-[11px] uppercase tracking-[0.14em]">
            {t("common:loading")}
          </span>
        </div>
      )}

      {draft && selection.mode !== "python" && (
        <>
          {editable && validation && (
            <EndpointDiagnostics
              errors={validation.errors}
              warnings={validation.warnings}
              onLocate={locateSection}
            />
          )}

          <div className="mb-4 inline-flex rounded-[8px] border border-hairline bg-bg-grad-a/40 p-0.5">
            {(["form", "json"] as const).map((mode) => (
              <button
                key={mode}
                type="button"
                onClick={() => {
                  if (mode === editorMode) return;
                  if (mode === "json") enterJsonMode();
                  else leaveJsonMode();
                }}
                aria-pressed={editorMode === mode}
                className={`rounded-[6px] px-3 py-1 text-[12px] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent ${
                  editorMode === mode ? "bg-accent-dim text-accent-2" : "text-text-3 hover:text-text"
                }`}
              >
                {mode === "form" ? t("ce_view_form") : t("ce_view_json")}
              </button>
            ))}
          </div>

          {editorMode === "json" ? (
            <div>
              <textarea
                value={jsonText}
                readOnly={!editable}
                spellCheck={false}
                aria-label={t("ce_view_json")}
                aria-invalid={jsonIssue !== null || undefined}
                rows={28}
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
                  setDraft(parsed);
                  setJsonIssue(null);
                }}
                className={`${INPUT_CLS} resize-y font-mono text-[11.5px] leading-[1.65]`}
              />
              {jsonIssue !== null && (
                <span role="alert" className="mt-1.5 block text-[12px] text-warm-bright">
                  {t(jsonIssue === "parse" ? "ce_json_parse_error" : "ce_json_shape_error")}
                </span>
              )}
            </div>
          ) : (
            <VariableInsertionProvider key={formEpoch}>
              <EndpointForm definition={draft} onChange={setDraft} readOnly={!editable} />
              <EndpointTestSection definition={draft} providers={providers} />
            </VariableInsertionProvider>
          )}
        </>
      )}

      <ConfirmDialog
        open={confirmDelete}
        title={t("ce_delete_title")}
        description={t("ce_delete_desc", { name: title })}
        confirmLabel={t("common:delete")}
        tone="danger"
        loading={deleting}
        onConfirm={() => void handleDelete()}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}
