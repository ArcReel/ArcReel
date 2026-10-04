import { useEffect, useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { AlertTriangle, Loader2 } from "lucide-react";
import { API } from "@/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useDebouncedValue } from "@/hooks/useDebouncedValue";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isResourceBusy } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import type { Asset, AssetType } from "@/types/asset";
import { ASSET_TYPE_ICON } from "./asset-type-icons";
import { AssetThumb } from "./AssetThumb";

/** 入库预览展示的项目资产内容，取项目里已保存的数据：入库时后端照这份数据复制。 */
export interface LibraryImportPreview {
  description: string;
  /** 角色的声音风格。 */
  voiceStyle?: string;
  /** 角色是否有参考音频。 */
  hasReferenceAudio?: boolean;
  sheetPath?: string | null;
  /** 角色的衍生数量。 */
  derivativeCount?: number;
}

interface Props {
  resourceType: AssetType;
  resourceId: string;
  projectName: string;
  preview: LibraryImportPreview;
  /** 资源被生成或编辑任务占用：禁用入库，避免把编辑前的旧图复制进全局资产库。 */
  busy?: boolean;
}

/**
 * 把项目里的资产复制到全局资产库。对话框只读预览将要复制的内容，只有名称可以修改。
 * 受控打开：入口是画廊卡片「更多」菜单里的「加入资产库」。
 */
export function AddToLibraryDialog({
  open,
  onOpenChange,
  busy = false,
  ...props
}: Props & { open: boolean; onOpenChange: (open: boolean) => void }) {
  const [submitting, setSubmitting] = useState(false);

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        // 请求在途时不响应 Esc 与遮罩点击，避免入库结果还没回来对话框先消失
        if (!next && submitting) return;
        onOpenChange(next);
      }}
    >
      <DialogContent showCloseButton={!submitting}>
        <AddToLibraryForm
          {...props}
          busy={busy}
          submitting={submitting}
          setSubmitting={setSubmitting}
          onDone={() => onOpenChange(false)}
        />
      </DialogContent>
    </Dialog>
  );
}

function AddToLibraryForm({
  resourceType,
  resourceId,
  projectName,
  preview,
  busy,
  submitting,
  setSubmitting,
  onDone,
}: Props & {
  busy: boolean;
  submitting: boolean;
  setSubmitting: (submitting: boolean) => void;
  onDone: () => void;
}) {
  const { t } = useTranslation("assets");
  const nameId = useId();
  const [name, setName] = useState(resourceId);
  const [conflict, setConflict] = useState<{ name: string; asset: Asset | null } | null>(null);
  const [error, setError] = useState<string | null>(null);
  const trimmedName = name.trim();
  const checkedName = useDebouncedValue(trimmedName, 250);
  const sheetFp = useProjectsStore((s) => (preview.sheetPath ? s.getAssetFingerprint(preview.sheetPath) : null));
  const imageUrl = preview.sheetPath ? API.getFileUrl(projectName, preview.sheetPath, sheetFp) : null;
  const Icon = ASSET_TYPE_ICON[resourceType];
  const typeLabel = t(`type.${resourceType}`);
  const isCharacter = resourceType === "character";

  // 资产库里同类型下名称唯一：名称变化后重新查一次是否重名。
  useEffect(() => {
    if (!checkedName) return;
    const controller = new AbortController();
    API.listAssets({ type: resourceType, q: checkedName }, { signal: controller.signal }).then(
      (page) => {
        if (controller.signal.aborted) return;
        setConflict({ name: checkedName, asset: page.items.find((item) => item.name === checkedName) ?? null });
      },
      () => {
        // 查不到重名时照常提交，真有重名由后端拒绝并显示错误。
      },
    );
    return () => controller.abort();
  }, [checkedName, resourceType]);

  const conflictAsset = conflict?.name === trimmedName ? conflict.asset : null;

  const submit = async (overwrite: boolean) => {
    if (!trimmedName || submitting) return;
    // 弹窗打开后资源可能经 SSE、其他标签页或 Agent 进入生成或编辑占用态：提交时复核最新占用态，
    // 避免把占用期间的旧图复制进全局资产库。
    if (busy || isResourceBusy(resourceType, projectName, resourceId)) {
      setError(t("add_to_library_busy_hint"));
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await API.addAssetFromProject({
        project_name: projectName,
        resource_type: resourceType,
        resource_id: resourceId,
        override_name: trimmedName !== resourceId ? trimmedName : undefined,
        overwrite,
      });
      useAppStore.getState().pushToast(t("add_to_library_success", { name: trimmedName }), "success");
      onDone();
    } catch (err) {
      setError(t("add_to_library_failed", { message: errMsg(err) }));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{t("import_title", { name: resourceId })}</DialogTitle>
        <DialogDescription>{t("import_description")}</DialogDescription>
      </DialogHeader>
      <DialogBody>
        <div className="flex flex-col gap-5">
          <AssetThumb
            imageUrl={imageUrl}
            alt={t("asset_image_alt", { name: resourceId })}
            fallback={<span className="flex flex-col items-center gap-2 text-sm"><Icon aria-hidden className="size-8" />{t("no_sheet")}</span>}
            className="rounded-lg border border-border"
          />
          <div className="flex flex-col gap-1.5">
            <Label htmlFor={nameId}>{t("field.name")}</Label>
            <Input
              id={nameId}
              value={name}
              onChange={(event) => setName(event.target.value)}
              autoComplete="off"
              disabled={submitting}
            />
            {conflictAsset && (
              <p className="flex items-start gap-1.5 text-sm text-subtle-foreground">
                <AlertTriangle aria-hidden className="mt-0.5 size-4 shrink-0 text-warn" />
                {t("conflict_warning", { type: typeLabel, name: conflictAsset.name })}
              </p>
            )}
          </div>
          <dl className="flex flex-col gap-4 text-sm">
            <div className="flex flex-col gap-1">
              <dt className="text-xs font-medium text-muted-foreground">{t("field.description")}</dt>
              <dd className="whitespace-pre-wrap break-words">
                {preview.description || <span className="text-muted-foreground">{t("no_description")}</span>}
              </dd>
            </div>
            {isCharacter && (
              <>
                <div className="flex flex-col gap-1">
                  <dt className="text-xs font-medium text-muted-foreground">{t("voice")}</dt>
                  <dd className="break-words">
                    {preview.voiceStyle || <span className="text-muted-foreground">{t("voice_style_none")}</span>}
                  </dd>
                  <dd className="text-muted-foreground">
                    {t(preview.hasReferenceAudio ? "reference_audio_included" : "reference_audio_none")}
                  </dd>
                </div>
                <div className="flex flex-col gap-1">
                  <dt className="text-xs font-medium text-muted-foreground">{t("derivatives")}</dt>
                  <dd>{t("derivatives_count", { count: preview.derivativeCount ?? 0 })}</dd>
                </div>
              </>
            )}
          </dl>
          <p className="text-xs text-muted-foreground">{t("import_readonly_hint")}</p>
          {error && (
            <p role="alert" className="text-sm text-destructive">
              {error}
            </p>
          )}
        </div>
      </DialogBody>
      <DialogFooter>
        <DialogClose render={<Button variant="outline" disabled={submitting} />}>{t("cancel")}</DialogClose>
        {conflictAsset && (
          <Button variant="destructive" disabled={submitting} onClick={() => void submit(true)}>
            {t("overwrite_existing")}
          </Button>
        )}
        <Button disabled={!trimmedName || submitting || conflictAsset !== null} onClick={() => void submit(false)}>
          {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
          {t("confirm_import")}
        </Button>
      </DialogFooter>
    </>
  );
}
