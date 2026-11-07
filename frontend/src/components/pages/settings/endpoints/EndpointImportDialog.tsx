import { useRef, useState } from "react";
import { CircleAlert, Loader2, TriangleAlert, Upload } from "lucide-react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Textarea } from "@/components/ui/textarea";
import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { voidCall } from "@/utils/async";
import type {
  AnyEndpointDefinition,
  ComfyuiMediaType,
  EndpointImportShape,
  EndpointValidateResponse,
} from "@/types";
import { formatNameList } from "@/utils/list-format";
import { EndpointDuplicateChoices } from "./EndpointDuplicateChoices";

/** Hints per three payload shapes; ordinary endpoint definitions need no space. */
const SHAPE_NOTICE_KEYS: Partial<Record<EndpointImportShape, string>> = {
  comfyui_api_workflow: "ce_import_shape_comfyui_api",
  comfyui_ui_workflow: "ce_import_shape_comfyui_ui",
};

const MEDIA_TYPES: readonly ComfyuiMediaType[] = ["video", "image"];

/**
 * Import dialogs receive uploaded/pasted payloads, both sharing validation, then show shapes/results
 * for creating copies, overwriting existing endpoints, binding nodes or cancelling.
 */
export function EndpointImportDialog({
  open,
  fileName,
  definition,
  validation,
  busy,
  saving,
  pending,
  mediaType,
  onSource,
  onMediaTypeChange,
  onCreateCopy,
  onOverwrite,
  onBindNodes,
  onCancel,
}: {
  open: boolean;
  /** Origin filenames; pasted payloads have none. */
  fileName: string;
  definition: AnyEndpointDefinition | null;
  validation: EndpointValidateResponse | null;
  /** Inference/persistence requests in flight. */
  busy: boolean;
  /** Persistence in flight cannot be withdrawn, disabling cancellation. */
  saving: boolean;
  /** Submitted payload validation in progress. */
  pending: boolean;
  /** Submit payloads, filenames for uploads only, none for pastes. */
  onSource: (text: string, fileName: string) => void;
  /** Media types wrapping raw workflows, selecting server inference rules/semantic-key vocabularies. */
  mediaType: ComfyuiMediaType;
  onMediaTypeChange: (mediaType: ComfyuiMediaType) => void;
  onCreateCopy: () => void;
  onOverwrite: (id: number) => void;
  /** Both ComfyUI shapes enter binding editors first, without persistence here. */
  onBindNodes: () => void;
  onCancel: () => void;
}) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  // Current unidentified payloads: file selection fills textboxes; user edits mean contents no longer represent those files.
  const [source, setSource] = useState({ text: "", fileName: "" });
  // Last raw text submitted for recognition. Edited text no longer equals source.text; held recognition results then describe
  // different payloads, and Import would persist old content. Always treat as unrecognized.
  const [detected, setDetected] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // Input takeover sequence: ``file.text()`` has no cancellable handles; users may choose another file or
  // paste before reads finish. Every action claims numbers first; returning reads with nonlatest numbers discard entirely. Parent
  // run tokens cannot guard here because they increment only after onSource dispatch.
  const sourceSeq = useRef(0);

  const stale = detected !== null && detected !== source.text;
  const result = stale ? null : validation;
  const detectedDefinition = stale ? null : definition;

  const submitSource = (text: string, name: string) => {
    setDetected(text);
    onSource(text, name);
  };

  const schemaVersion = result?.schema_version;
  const minAppVersion = result?.min_app_version;
  const shapeNoticeKey = result ? SHAPE_NOTICE_KEYS[result.import_shape] : undefined;
  const isRawWorkflow = result?.import_shape === "comfyui_api_workflow";
  // ComfyUI dialogs stop at binding-editor entry; same-author/name matching waits until names are edited there.
  const toBindings = detectedDefinition?.kind === "comfyui";
  // Automatically wrapped definitions necessarily lack node bindings, a true current state rather than an error here.
  const errors = (result?.errors ?? []).filter(
    (issue) => !(isRawWorkflow && issue.code === "comfyui_binding_required"),
  );
  const hasErrors = errors.length > 0;

  const subtitle = [
    fileName || (result || pending ? t("ce_import_pasted") : ""),
    detectedDefinition?.meta?.name,
    detectedDefinition?.meta?.version ? `v${detectedDefinition.meta.version}` : "",
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <Dialog
      open={open}
      onOpenChange={(next: boolean) => {
        // In-flight requests ignore Esc/backdrop clicks; Cancel remains available during recognition/inference, invalidating those requests.
        if (!next && !busy) onCancel();
      }}
    >
      <DialogContent size="lg" showCloseButton={false}>
        <DialogHeader>
          <DialogTitle>{t("ce_import_title")}</DialogTitle>
          {subtitle && <DialogDescription>{subtitle}</DialogDescription>}
        </DialogHeader>
        <DialogBody>
          <div className="flex flex-col gap-4">
            <div className="flex flex-col gap-2">
              <p className="text-sm text-muted-foreground">{t("ce_import_source_desc")}</p>
              <div className="flex flex-wrap items-center gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy || pending}
                  onClick={() => fileRef.current?.click()}
                >
                  <Upload aria-hidden data-icon="inline-start" />
                  {t("ce_import_pick_file")}
                </Button>
                <input
                  ref={fileRef}
                  type="file"
                  accept="application/json,.json"
                  className="hidden"
                  onChange={(event) => {
                    const file = event.target.files?.[0];
                    // Reselecting identical files must fire change; clear after every read.
                    event.target.value = "";
                    if (!file) return;
                    sourceSeq.current += 1;
                    const pick = sourceSeq.current;
                    voidCall(
                      file.text().then((text) => {
                        if (pick !== sourceSeq.current) return;
                        setSource({ text, fileName: file.name });
                        submitSource(text, file.name);
                      }),
                    );
                  }}
                />
                <span className="text-xs text-muted-foreground">{t("ce_import_or_paste")}</span>
              </div>
              <Textarea
                mono
                className="min-h-28"
                aria-label={t("ce_import_paste_label")}
                placeholder={t("ce_import_paste_placeholder")}
                spellCheck={false}
                translate="no"
                value={source.text}
                onChange={(event) => {
                  sourceSeq.current += 1;
                  setSource({ text: event.target.value, fileName: "" });
                }}
              />
              <div className="flex justify-end">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={busy || pending || source.text.trim() === ""}
                  onClick={() => submitSource(source.text, source.fileName)}
                >
                  {t("ce_import_detect")}
                </Button>
              </div>
            </div>

            {pending && (
              <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 aria-hidden className="size-4 animate-spin text-primary" />
                {t("common:loading")}
              </p>
            )}

            {shapeNoticeKey && <p className="rounded-md border border-border px-3 py-2 text-sm text-subtle-foreground">{t(shapeNoticeKey)}</p>}

            {schemaVersion && schemaVersion.level !== "direct" && (
              <p className="rounded-md border border-border px-3 py-2 text-sm text-subtle-foreground">
                {t("ce_import_schema_mismatch", {
                  file: schemaVersion.file ?? t("ce_import_schema_unknown"),
                  current: schemaVersion.current,
                })}
              </p>
            )}

            {minAppVersion && !minAppVersion.satisfied && (
              <p className="flex items-start gap-2 rounded-md border border-border px-3 py-2 text-sm text-subtle-foreground">
                <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />
                <span>
                  {t("ce_import_requires_newer_app", {
                    required: minAppVersion.required,
                    current: minAppVersion.current,
                  })}
                </span>
              </p>
            )}

            {isRawWorkflow && (
              <div className="flex flex-col gap-2">
                <span id="ce-import-media-type" className="text-sm font-medium">
                  {t("ce_import_media_type")}
                </span>
                <ToggleGroup
                  aria-labelledby="ce-import-media-type"
                  variant="outline"
                  size="sm"
                  value={[mediaType]}
                  disabled={busy}
                  onValueChange={(next: string[]) => {
                    // Single-select: clicking selected items again never deselects.
                    const picked = MEDIA_TYPES.find((media) => media === next[0]);
                    if (picked) onMediaTypeChange(picked);
                  }}
                >
                  {MEDIA_TYPES.map((media) => (
                    <ToggleGroupItem key={media} value={media}>
                      {t(media === "image" ? "endpoint_image_group" : "endpoint_video_group")}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
                <span className="text-xs text-muted-foreground">{t("ce_import_media_type_note")}</span>
              </div>
            )}

            {result && (errors.length > 0 || result.warnings.length > 0) && (
              <ul className="flex flex-col gap-1.5">
                {errors.map((issue) => (
                  <li key={`e-${issue.path}-${issue.code}`} className="flex items-start gap-2 text-sm text-subtle-foreground">
                    <CircleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />
                    <span>{issue.message}</span>
                  </li>
                ))}
                {result.warnings.map((issue) => (
                  <li key={`w-${issue.path}-${issue.code}`} className="flex items-start gap-2 text-sm text-subtle-foreground">
                    <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                    <span>{issue.message}</span>
                  </li>
                ))}
              </ul>
            )}

            {(result?.hints?.base_url || (result?.hints?.suggested_models?.length ?? 0) > 0) && (
              <div className="flex flex-col gap-1 text-sm text-muted-foreground">
                {result?.hints?.base_url && <p>{t("ce_import_hint_base_url", { url: result.hints.base_url })}</p>}
                {result?.hints?.suggested_models && result.hints.suggested_models.length > 0 && (
                  <p>
                    {t("ce_import_hint_models", {
                      models: formatNameList(
                        result.hints.suggested_models.map((m) => m.label ?? m.id),
                        i18n.language,
                      ),
                    })}
                  </p>
                )}
              </div>
            )}

            {result && !toBindings && (
              <EndpointDuplicateChoices
                duplicates={result.duplicates}
                disabled={busy || hasErrors}
                onOverwrite={onOverwrite}
              />
            )}
          </div>
        </DialogBody>
        <DialogFooter>
          {hasErrors && <p className="mr-auto self-center text-sm text-warn">{t("ce_import_blocked")}</p>}
          <Button variant="outline" onClick={onCancel} disabled={saving}>
            {t("common:cancel")}
          </Button>
          <Button
            disabled={busy || hasErrors || !detectedDefinition || result === null}
            onClick={toBindings ? onBindNodes : onCreateCopy}
          >
            {busy && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {toBindings
              ? t("ce_import_to_bindings")
              : result && result.duplicates.length > 0
                ? t("ce_import_create_copy")
                : t("ce_import_create")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
