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

/** 三种载荷形状各自的提示；端点定义是常态，不占版面。 */
const SHAPE_NOTICE_KEYS: Partial<Record<EndpointImportShape, string>> = {
  comfyui_api_workflow: "ce_import_shape_comfyui_api",
  comfyui_ui_workflow: "ce_import_shape_comfyui_ui",
};

const MEDIA_TYPES: readonly ComfyuiMediaType[] = ["video", "image"];

/**
 * 导入：在弹窗里交出一份载荷——上传文件或直接粘贴，两条路走同一次校验——再看形状与校验结果，
 * 决定新建副本、覆盖既有、去绑定节点，还是取消。
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
  /** 载荷来自哪个文件；粘贴进来的没有文件名。 */
  fileName: string;
  definition: AnyEndpointDefinition | null;
  validation: EndpointValidateResponse | null;
  /** 推断或落盘请求在途。 */
  busy: boolean;
  /** 落盘请求在途：已经发出就收不回，期间不能取消。 */
  saving: boolean;
  /** 交出去的那份载荷正在校验。 */
  pending: boolean;
  /** 交出一份载荷：上传的文件带文件名，粘贴的不带。 */
  onSource: (text: string, fileName: string) => void;
  /** 原始 workflow 按哪一种媒体类型包装；服务端据它选推断规则与语义键名录。 */
  mediaType: ComfyuiMediaType;
  onMediaTypeChange: (mediaType: ComfyuiMediaType) => void;
  onCreateCopy: () => void;
  onOverwrite: (id: number) => void;
  /** ComfyUI 两种形状都先进绑定编辑器，不在这里落盘。 */
  onBindNodes: () => void;
  onCancel: () => void;
}) {
  const { t, i18n } = useTranslation(["dashboard", "common"]);
  // 手上这份待识别的载荷。选了文件就把内容铺进文本框，用户改过之后它不再算那个文件的内容。
  const [source, setSource] = useState({ text: "", fileName: "" });
  // 最后一次交出去识别的原文。文本框改过之后它与 source.text 不再相等，手上那份识别结果说的
  // 就不是屏幕上这份载荷了——此时按导入会把旧载荷存下去，故一律当作还没识别过。
  const [detected, setDetected] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  // 来源输入的接管序号：``file.text()`` 没有可取消的句柄，读完之前用户可以再选一个文件，也可以
  // 直接往文本框里粘一份。任一动作先领一个号，在途的读回来时号已经不是最新的就整份丢弃——父层
  // 那个 run token 拦不住这一格，它要等 onSource 发出去之后才递增。
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
  // ComfyUI 端点在弹窗里只走到「进绑定编辑器」；同作者同名的判定留到那边改完名字再说。
  const toBindings = detectedDefinition?.kind === "comfyui";
  // 自动包装出来的定义节点绑定必然是空的，这一条在此刻不是错误、是它的真实状态。
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
        // 请求在途时不响应 Esc 与遮罩点击；识别与推断在途时「取消」仍可用，它会作废在途的请求
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
                    // 同一份文件再选一次也要触发 change，因此每次读完就清空。
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
                    // 单选：再次点击已选项不取消选择
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
