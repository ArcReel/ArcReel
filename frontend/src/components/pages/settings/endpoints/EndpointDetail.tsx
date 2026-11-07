import { useCallback, useEffect, useId, useMemo, useState, type ReactNode } from "react";
import { Copy, Download, ExternalLink, Loader2, Plus, Share2, Store, Trash2, Upload } from "lucide-react";
import { Link } from "wouter";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { marketSettingsPath } from "@/app-routes";
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
  AnyEndpointDefinition,
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
import { MarketSubmissionBadge } from "../market/MarketSubmissionBadge";
import { MARKET_CONTRIBUTING_URL } from "../market/market-links";
import {
  definitionMediaType,
  isRenderableDefinition,
  slugFromName,
  type EndpointFormSection,
} from "./endpoint-definition-draft";
import { EndpointDiagnostics } from "./EndpointDiagnostics";
import { EndpointHeader, type EndpointBackTarget } from "./EndpointHeader";
import { endpointReferences } from "./endpoint-references";
import { EndpointReferenceList } from "./EndpointReferenceList";
import { EndpointForm } from "./EndpointForm";
import { EndpointTestSection } from "./EndpointTestSection";
import type { EndpointUsage } from "./endpoint-usages";
import { EndpointUsageList } from "./EndpointUsageList";
import { exportEndpointDefinition } from "./export-endpoint-definition";
import { EXAMPLE_TEMPLATES } from "./example-templates";
import { VariableInsertionProvider } from "./endpoint-form-primitives";
import { ComfyuiEndpointDetail } from "./ComfyuiEndpointDetail";
import { EndpointInstallBadges, MarketOrigin, MarketUpdateButton } from "./EndpointMarketInfo";
import { ShareToMarketDialog } from "./ShareToMarketDialog";
import type { ComfyuiImportDraft } from "./comfyui-import";

const VALIDATE_DEBOUNCE_MS = 400;
/** Example-template Blank definition value; Select values cannot be empty strings. */
const BLANK_TEMPLATE = "blank";

/**
 * Six selection states: new, own declarative, own ComfyUI, imported unsaved ComfyUI,
 * built-in declarative, built-in Python.
 */
export type EndpointSelection =
  | { mode: "new"; definition: EndpointDefinition }
  | { mode: "custom"; record: CustomEndpointInfo; definition: EndpointDefinition }
  | { mode: "comfyui"; record: CustomEndpointInfo; definition: ComfyuiEndpointDefinition }
  | { mode: "comfyui-draft"; draft: ComfyuiImportDraft }
  | { mode: "builtin"; descriptor: EndpointDescriptor }
  | { mode: "python"; descriptor: EndpointDescriptor };

/** Own endpoints: both kinds share saved records supplying keys/install records/deletion entries. */
function savedRecordOf(selection: EndpointSelection): CustomEndpointInfo | null {
  return selection.mode === "custom" || selection.mode === "comfyui" ? selection.record : null;
}

interface EndpointDetailProps {
  selection: EndpointSelection;
  providers: CustomProviderInfo[];
  /** Model rows using endpoints, from custom-provider model lists. */
  usages: EndpointUsage[];
  /** Return targets from custom-provider navigation. */
  back: EndpointBackTarget | null;
  onSaved: (record: CustomEndpointInfo) => void;
  onDeleted: () => void;
  onCopied: (record: CustomEndpointInfo) => void;
  onCreateProvider: (definition: EndpointDefinition, endpointKey: string) => void;
  onNavigateToModel: (reference: EndpointReference) => void;
  /** Open import dialogs from new-endpoint pages. */
  onImport: () => void;
  /** Open install confirmations in update mode, available only for updateable market axes, shared by both kinds. */
  onUpdateFromMarket: (
    installation: EndpointInstallation,
    currentDefinition: AnyEndpointDefinition,
    hasUnsavedChanges: boolean,
  ) => void;
  /** Loading entry details for update dialogs. */
  marketUpdatePending: boolean;
  /** Reimport workflows for current ComfyUI endpoints: new workflows attach to outgoing definitions, rematched on return. */
  onReimportComfyui: (current: ComfyuiEndpointDefinition) => void;
  /** Discard unsaved ComfyUI edits; parents drop imported drafts and rebuild details. */
  onDiscardComfyui: () => void;
  /** With official services enabled: latest share submissions and completion callbacks. */
  share?: { submission: MarketSubmission | null; onSubmitted: (submission: MarketSubmission) => void };
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
  // Market update dialogs check unsaved edits at click time for overwrite warnings; block edits while entry details load.
  const readOnly = !editable || marketUpdatePending;
  const savedRecord = savedRecordOf(selection);
  const persistedId = savedRecord?.id ?? null;
  const installation = savedRecord?.installation ?? null;

  // Fetch built-in declarative definitions separately for readonly display.
  const [builtinDefinition, setBuiltinDefinition] = useState<EndpointDefinition | null>(null);
  const [editorMode, setEditorMode] = useState<"form" | "json">("form");
  // JSON fragment editors own text state; increment on endpoint changes/returning from JSON views to remount with new definitions.
  const [formEpoch, setFormEpoch] = useState(0);
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
      API.getBuiltinEndpointDefinition(builtinKey, { signal: controller.signal })
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

  const saveDefinition = useCallback(
    async (value: { definition: EndpointDefinition | null; jsonText: string | null }) => {
      if (!value.definition) return;
      if (value.jsonText !== null) {
        let parsed: unknown;
        try {
          parsed = JSON.parse(value.jsonText);
        } catch {
          throw new Error(t("ce_json_parse_error"));
        }
        if (!isRenderableDefinition(parsed)) throw new Error(t("ce_json_shape_error"));
      }
      // Error diagnostics/invalid JSON text also fail servers; retain reasons in save bars.
      if (hasErrors) throw new Error(t("ce_save_blocked"));
      const saved =
        persistedId === null
          ? await API.createCustomEndpoint(value.definition)
          : await API.updateCustomEndpoint(persistedId, value.definition);
      onSaved(saved);
      return { definition: isRenderableDefinition(saved.definition) ? saved.definition : value.definition, jsonText: null };
    },
    [hasErrors, persistedId, onSaved, t],
  );

  const editableSource = selection.mode === "new" || selection.mode === "custom" ? selection.definition : null;
  // Raw JSON belongs to the same edit unit: syntax errors still participate in leave guards/discard.
  const source = useMemo(() => ({ definition: editableSource, jsonText: null as string | null }), [editableSource]);
  const unit = useEditUnit({ source, save: saveDefinition });
  const draft = editable ? unit.value.definition : builtinDefinition;
  const jsonText = unit.value.jsonText ?? JSON.stringify(draft ?? {}, null, 2);
  const jsonIssue = useMemo(() => {
    if (unit.value.jsonText === null) return null;
    try {
      return isRenderableDefinition(JSON.parse(unit.value.jsonText)) ? null : "shape";
    } catch {
      return "parse";
    }
  }, [unit.value.jsonText]);
  const hasUnsavedChanges = unit.dirty;

  const draftJson = useMemo(() => (draft ? JSON.stringify(draft) : null), [draft]);

  // Diagnostics share server save validators, continuously checked during editing.
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
            // Failed validation requests retain prior results, never present network errors as invalid definitions.
          }),
      );
    }, VALIDATE_DEBOUNCE_MS);
    return () => {
      controller.abort();
      clearTimeout(timer);
    };
  }, [draftJson, editable, persistedId]);

  const enterJsonMode = () => {
    setEditorMode("json");
  };

  const leaveJsonMode = () => {
    setFormEpoch((n) => n + 1);
    setEditorMode("form");
  };

  /** Replace entire new-endpoint definitions from examples, remounting both views. */
  const applyTemplate = (id: string) => {
    if (selection.mode !== "new") return;
    const template = EXAMPLE_TEMPLATES.find((item) => item.id === id);
    const next = structuredClone(template ? template.definition : selection.definition);
    setTemplateId(id);
    unit.setValue({ definition: next, jsonText: null });
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
   * Locate diagnostic sections; fields without form controls, such as `enum_maps` / `defaults`, use
   * JSON views. Already-targeted views never remount forms, which resets edited JSON fragments.
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

  // Kind-independent More entries: deletion/sharing/dialogs share both detail implementations.
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
          // In-flight deletion ignores Esc/backdrop clicks.
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
            // Header sharing-status markers communicate submission results.
            setShareOpen(false);
            share.onSubmitted(submission);
          }}
        />
      )}
    </>
  );

  // ComfyUI definitions combine workflows/bindings, without declarative submit/poll sections;
  // details/binding editors have distinct shapes, but this component supplies deletion/sharing, sharing lifecycles across kinds.
  if (selection.mode === "comfyui" || selection.mode === "comfyui-draft") {
    const draftRecord = selection.mode === "comfyui" ? selection.record : selection.draft.record;
    // ComfyUI test credentials list comfyui providers only; other protocols' addresses/secrets cannot access ComfyUI machines.
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
          onUpdateFromMarket={onUpdateFromMarket}
          marketUpdatePending={marketUpdatePending}
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
    // Built-ins are readonly; creating providers is common, copying endpoints secondary.
    primary = (
      <>
        <Button variant="outline" onClick={() => void handleCopyAsMine()} disabled={copying || !draft}>
          {copying ? (
            <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />
          ) : (
            <Copy aria-hidden data-icon="inline-start" />
          )}
          {t("ce_copy_as_mine")}
        </Button>
        <Button onClick={() => draft && endpointKey && onCreateProvider(draft, endpointKey)} disabled={!draft || !endpointKey}>
          <Plus aria-hidden data-icon="inline-start" />
          {t("ce_create_provider")}
        </Button>
      </>
    );
    menu = exportItem;
  }

  const updateButton = (
    <MarketUpdateButton
      installation={installation}
      pending={marketUpdatePending}
      disabled={!draft}
      onUpdate={(target) => draft && onUpdateFromMarket(target, draft, hasUnsavedChanges)}
    />
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
                <EndpointInstallBadges installation={installation} />
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
                  <MarketOrigin installation={installation} />
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
        // Save bars match body form-column widths/origins (body max-w-190 includes 24px padding each side).
        footer={editable ? <SaveBar unit={unit} className="max-w-178" /> : undefined}
      >
        <div className="flex max-w-190 flex-col gap-8 px-6 py-6">
          {selection.mode === "new" && (
            <Alert>
              <AlertDescription>
                {t("ce_new_desc")}{" "}
                <Link
                  href={marketSettingsPath("browse", { media: draft ? definitionMediaType(draft) : null })}
                  className={buttonVariants({ variant: "link", size: "sm" })}
                >
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
                  // Single-select: clicking selected items again never deselects.
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
                    mono
                    value={jsonText}
                    readOnly={readOnly}
                    spellCheck={false}
                    aria-label={t("ce_view_json")}
                    aria-invalid={jsonIssue !== null || undefined}
                    onChange={(e) => {
                      const text = e.target.value;
                      let definition = draft;
                      try {
                        const parsed: unknown = JSON.parse(text);
                        if (isRenderableDefinition(parsed)) definition = parsed;
                      } catch {
                        // Invalid raw text still counts as modified; forms/diagnostics retain latest renderable definitions.
                      }
                      unit.setValue({ definition, jsonText: text });
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
                  <EndpointForm definition={draft} onChange={(next) => unit.setValue({ definition: next, jsonText: null })} readOnly={readOnly} />
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
