import { useCallback, useId, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, History, Loader2, MoreHorizontal, PenLine, Pencil, Shirt, Trash2, X } from "lucide-react";
import { API, CHARACTER_DERIVATIVE_RESOURCE_TYPE, derivativeResourceId } from "@/api";
import { enqueueCharacterDerivative } from "@/actions/generation";
import { ImageEditDialog } from "@/components/canvas/timeline/ImageEditButton";
import { VersionTimeMachine } from "@/components/canvas/timeline/VersionTimeMachine";
import { CrossfadeImage } from "@/components/canvas/shared/CrossfadeImage";
import { CopyButton } from "@/components/shared/CopyButton";
import { UnsavedChangesBar } from "@/components/shared/edit-unit/UnsavedChangesBar";
import { useEditUnit } from "@/components/shared/edit-unit/useEditUnit";
import { PromptPreviewButton } from "@/components/shared/PromptPreviewButton";
import { TruncatedText } from "@/components/shared/TruncatedText";
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
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { isResourceBusy, useActiveResourceIds } from "@/stores/tasks-store";
import { errMsg } from "@/utils/async";
import type { AssetSheetStatusRow, CharacterDerivative, CharacterDerivativeStatus } from "@/types";
import { rejectIfAssetBusy } from "./assetBusyGuard";
import { hasUsableDescription } from "./AssetSheetStatusBadge";
import type { GalleryMarker } from "./gallery-model";
import { GalleryStatusMarker } from "./GalleryStatusMarker";
import { GenerateButton } from "./GenerateButton";
import { useTrackWrite } from "./useAssetWrites";
import { useStaleRegenerateConfirm } from "./useStaleRegenerateConfirm";

/** 脚本正文里引用衍生的记号。 */
export function derivativeToken(characterName: string, derivativeName: string): string {
  return `@[${characterName}/${derivativeName}]`;
}

type RowDialog = "image-edit" | "versions" | "delete";

export interface CharacterDerivativeRowProps {
  projectName: string;
  characterName: string;
  name: string;
  derivative: CharacterDerivative;
  /** 衍生图与过期标记；还没取到时为 undefined。 */
  status: CharacterDerivativeStatus | undefined;
  /** 本体是否已有资产图。衍生图是对它的一次编辑，没有本体图就不能生成。 */
  ownerHasSheet: boolean;
  readOnly: boolean;
  /** 详情里其他写入或本体生成占用中：本条的写入入口一起禁用。 */
  busy: boolean;
  onView: (image: { src: string; alt: string; title: string }) => void;
  /** 改名已提交、项目数据刷新之前调用，让这一行沿用原来的编辑单元。 */
  onRenamed: (from: string, to: string) => void;
  /** 衍生图或登记变化后重新读取衍生图状态。 */
  onSheetsChanged: () => void;
}

/**
 * 角色详情「衍生」区块里的一条衍生：缩略图、名称、可复制的引用记号、脚本引用状态、外观变化与生成入口。
 *
 * 外观变化是这一条自己的编辑单元，在行内保存，与资产本体的编辑单元并存，离开拦截覆盖两者。
 * 改名、删除、局部修改与版本恢复是立即执行的动作。
 */
export function CharacterDerivativeRow({
  projectName,
  characterName,
  name,
  derivative,
  status,
  ownerHasSheet,
  readOnly,
  busy,
  onView,
  onRenamed,
  onSheetsChanged,
}: CharacterDerivativeRowProps) {
  const { t } = useTranslation(["assets", "common", "dashboard"]);
  const descriptionId = useId();
  const moreRef = useRef<HTMLButtonElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const track = useTrackWrite();
  const [dialog, setDialog] = useState<RowDialog | null>(null);
  const [renaming, setRenaming] = useState(false);
  const [renameDraft, setRenameDraft] = useState("");
  const [pending, setPending] = useState(false);
  const [restoring, setRestoring] = useState(false);
  const [failedUrl, setFailedUrl] = useState<string | null>(null);

  const resourceId = derivativeResourceId(characterName, name);
  const generating = useActiveResourceIds("character_derivative", projectName).has(resourceId);
  const token = derivativeToken(characterName, name);
  const qualified = `${characterName}/${name}`;

  const save = useCallback(
    async (value: string) => {
      await track(
        API.updateCharacterDerivative(projectName, characterName, name, value).then(() =>
          useProjectsStore.getState().refreshProject(projectName),
        ),
      );
      onSheetsChanged();
    },
    [track, projectName, characterName, name, onSheetsChanged],
  );
  const unit = useEditUnit<string>({
    source: derivative.description,
    save,
    leaveTitle: t("assets:edit_leave_title", { name: qualified }),
  });
  const saving = unit.status === "saving";
  // 本条的生成、版本恢复、改名或删除在途，或详情里别处在写：兄弟入口一起禁用
  const rowBusy = busy || generating || restoring || pending;

  const artifactStatus = status?.artifact_status ?? (status?.stale ? "stale" : status?.character_sheet ? "current" : "missing");
  const sheetPath = artifactStatus === "missing" ? "" : (status?.character_sheet ?? derivative.character_sheet ?? "");
  const sheetFp = useProjectsStore((s) => (sheetPath ? s.getAssetFingerprint(sheetPath) : null));
  const sheetUrl = sheetPath ? API.getFileUrl(projectName, sheetPath, sheetFp) : null;
  const imageUrl = sheetUrl && sheetUrl !== failedUrl ? sheetUrl : null;
  const imageAlt = t("assets:library_derivative_thumb", { name: qualified });
  const describable = hasUsableDescription(unit.value);
  const marker: GalleryMarker = generating
    ? "generating"
    : !hasUsableDescription(derivative.description)
      ? "no-description"
      : artifactStatus === "stale"
        ? "stale"
        : sheetPath
          ? "current"
          : "missing";

  /** 提交时刻复核：队列里本体与这条衍生的占用，加上详情里的本地在途写入。 */
  const rejectIfBusy = () => {
    if (rowBusy) {
      useAppStore.getState().pushToast(t("assets:derivative_busy_hint"), "info");
      return true;
    }
    return (
      rejectIfAssetBusy("character", projectName, characterName, t, "assets:derivative_busy_hint") ||
      rejectIfAssetBusy("character_derivative", projectName, resourceId, t, "assets:derivative_busy_hint")
    );
  };

  const handleGenerate = async () => {
    if (
      isResourceBusy("character", projectName, characterName) ||
      isResourceBusy("character_derivative", projectName, resourceId)
    ) {
      useAppStore.getState().pushToast(t("assets:derivative_busy_hint"), "info");
      return;
    }
    try {
      await enqueueCharacterDerivative(projectName, characterName, name);
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    }
  };

  const sheetStatus: AssetSheetStatusRow = {
    unit_id: `character/${resourceId}`,
    asset_type: "character",
    name: characterName,
    derivative: name,
    status: artifactStatus,
    description_missing: !hasUsableDescription(derivative.description),
    image_to_image: true,
  };
  const staleConfirm = useStaleRegenerateConfirm({
    projectName,
    assetType: "character",
    name: characterName,
    derivativeName: name,
    status: sheetStatus,
    hasSheet: Boolean(sheetPath),
    onGenerate: () => void handleGenerate(),
  });

  const generate = () => {
    // 提交前复核：从渲染到点击之间，本体或这条衍生可能刚被别处占用
    if (rejectIfBusy()) return;
    // 衍生图由外观变化生成：改了它，保存后这张图就会过期，服务端此刻的判定还看不到
    const willBeStale = unit.value !== unit.savedValue;
    void unit.saveAndGenerate(handleGenerate, {
      confirm: () => staleConfirm.confirm({ willBeStale }),
    });
  };

  /** 跑一次立即执行的写入；失败时弹出原因并返回 false。 */
  const run = async (work: () => Promise<unknown>): Promise<boolean> => {
    if (rejectIfBusy()) return false;
    setPending(true);
    try {
      await track(work());
      onSheetsChanged();
      return true;
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
      return false;
    } finally {
      setPending(false);
    }
  };

  const startRename = () => {
    setRenameDraft(name);
    setRenaming(true);
    // 菜单关闭后焦点会回到「更多」，等它落定再移到输入框
    requestAnimationFrame(() => renameInputRef.current?.select());
  };

  const submitRename = async () => {
    const next = renameDraft.trim();
    if (!next || next === name) {
      setRenaming(false);
      return;
    }
    const ok = await run(() =>
      API.renameCharacterDerivative(projectName, characterName, name, next).then(() => {
        onRenamed(name, next);
        return useProjectsStore.getState().refreshProject(projectName);
      }),
    );
    if (ok) setRenaming(false);
  };

  const handleDelete = async () => {
    const ok = await run(() =>
      API.deleteCharacterDerivative(projectName, characterName, name).then(() =>
        useProjectsStore.getState().refreshProject(projectName),
      ),
    );
    if (ok) setDialog(null);
  };

  // 打开会写这条衍生的对话框前复核占用态
  const openGuarded = (key: RowDialog) => {
    if (!rejectIfBusy()) setDialog(key);
  };
  const closeDialog = (open: boolean) => {
    if (!open) setDialog(null);
  };

  const thumbnail = (
    <div className="relative aspect-video w-28 shrink-0 overflow-hidden rounded-md border border-border bg-muted/40">
      {imageUrl ? (
        <button
          type="button"
          onClick={() => onView({ src: imageUrl, alt: imageAlt, title: qualified })}
          aria-label={t("assets:view_image", { name: imageAlt })}
          className="focus-ring block size-full"
        >
          <CrossfadeImage
            src={imageUrl}
            alt={imageAlt}
            className="object-contain"
            onError={() => setFailedUrl(imageUrl)}
            fallback={null}
          />
        </button>
      ) : (
        <div className="flex size-full items-center justify-center text-muted-foreground">
          <Shirt aria-hidden className="size-5" />
        </div>
      )}
      <GalleryStatusMarker marker={marker} className="absolute top-1 left-1" />
    </div>
  );

  return (
    <li className="flex flex-col gap-2 p-3">
      <div className="flex min-w-0 gap-3">
        {thumbnail}
        <div className="flex min-w-0 flex-1 flex-col gap-1">
          {renaming ? (
            <form
              className="flex items-center gap-1"
              onSubmit={(e) => {
                e.preventDefault();
                void submitRename();
              }}
            >
              <Input
                ref={renameInputRef}
                aria-label={t("assets:derivative_rename_label", { name })}
                value={renameDraft}
                disabled={pending}
                onChange={(e) => setRenameDraft(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Escape") {
                    // Esc 只退出改名，不连带关闭外层 Sheet
                    e.preventDefault();
                    e.stopPropagation();
                    setRenaming(false);
                  }
                }}
                className="h-7 min-w-0 flex-1"
              />
              <Button type="submit" variant="ghost" size="icon-sm" disabled={pending} aria-label={t("assets:derivative_rename_confirm")}>
                {pending ? <Loader2 aria-hidden className="animate-spin" /> : <Check aria-hidden />}
              </Button>
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                disabled={pending}
                onClick={() => setRenaming(false)}
                aria-label={t("common:cancel")}
              >
                <X aria-hidden />
              </Button>
            </form>
          ) : (
            <div className="flex min-h-7 min-w-0 items-center gap-1">
              <h4 className="min-w-0 flex-1 text-sm font-medium">
                <TruncatedText text={name} />
              </h4>
              {readOnly ? null : (
                <DropdownMenu>
                  <DropdownMenuTrigger
                    ref={moreRef}
                    render={<Button variant="ghost" size="icon-sm" className="shrink-0" />}
                    aria-label={t("assets:derivative_menu_label", { name })}
                  >
                    <MoreHorizontal aria-hidden />
                  </DropdownMenuTrigger>
                  <DropdownMenuContent align="end" className="min-w-40">
                    <DropdownMenuGroup>
                      <DropdownMenuItem disabled={rowBusy} onClick={startRename}>
                        <Pencil aria-hidden />
                        {t("assets:derivative_rename")}
                      </DropdownMenuItem>
                      <DropdownMenuItem disabled={rowBusy || !imageUrl} onClick={() => openGuarded("image-edit")}>
                        <PenLine aria-hidden />
                        {t("dashboard:image_edit_action")}
                      </DropdownMenuItem>
                      <DropdownMenuItem onClick={() => setDialog("versions")}>
                        <History aria-hidden />
                        {t("assets:gallery_menu_versions")}
                      </DropdownMenuItem>
                    </DropdownMenuGroup>
                    <DropdownMenuSeparator />
                    <DropdownMenuGroup>
                      <DropdownMenuItem variant="destructive" disabled={rowBusy} onClick={() => openGuarded("delete")}>
                        <Trash2 aria-hidden />
                        {t("assets:delete")}
                      </DropdownMenuItem>
                    </DropdownMenuGroup>
                  </DropdownMenuContent>
                </DropdownMenu>
              )}
            </div>
          )}
          <div className="flex min-w-0 items-center gap-1">
            <code className="min-w-0 truncate rounded-sm bg-muted px-1.5 py-0.5 font-mono text-xs text-subtle-foreground">
              {token}
            </code>
            <CopyButton text={token} label={t("assets:derivative_copy_token", { token })} />
          </div>
          {derivative.referenced === undefined ? null : (
            <p className="text-xs text-muted-foreground">
              {t(derivative.referenced ? "assets:derivative_referenced" : "assets:derivative_unreferenced")}
            </p>
          )}
        </div>
      </div>

      <Textarea
        id={descriptionId}
        aria-label={t("assets:derivative_description_of", { name })}
        placeholder={t("assets:derivative_description_placeholder")}
        value={unit.value}
        readOnly={readOnly}
        onChange={(e) => unit.setValue(e.target.value)}
        rows={2}
      />

      {readOnly ? null : (
        <>
          <UnsavedChangesBar unit={unit} />
          <div className="flex flex-wrap items-center gap-1.5">
            <GenerateButton
              onClick={generate}
              loading={generating}
              disabled={rowBusy || saving || !ownerHasSheet || !describable}
              label={
                unit.dirty
                  ? t("common:save_and_generate")
                  : sheetPath
                    ? t("assets:derivative_regenerate")
                    : t("assets:derivative_generate")
              }
              size="sm"
              variant="outline"
            />
            <PromptPreviewButton
              title={t("assets:prompt_preview_title", { name: qualified })}
              beforeOpen={unit.save}
              saveFirst={unit.dirty}
              load={(signal) =>
                API.previewAssetPrompt(projectName, "character", characterName, unit.value, {
                  signal,
                  derivativeName: name,
                })
              }
            />
          </div>
          {ownerHasSheet ? (
            describable ? null : (
              <p className="text-xs text-muted-foreground">{t("assets:sheet_description_required")}</p>
            )
          ) : (
            <p className="text-xs text-muted-foreground">{t("assets:derivative_owner_sheet_required")}</p>
          )}

          {staleConfirm.dialog}
          <ImageEditDialog
            open={dialog === "image-edit"}
            onOpenChange={closeDialog}
            projectName={projectName}
            resourceType="character_derivative"
            resourceId={resourceId}
            hasImage={Boolean(imageUrl)}
            busy={rowBusy}
          />
          <VersionTimeMachine
            open={dialog === "versions"}
            onOpenChange={closeDialog}
            anchor={moreRef}
            projectName={projectName}
            resourceType={CHARACTER_DERIVATIVE_RESOURCE_TYPE}
            resourceId={resourceId}
            onRestore={async () => {
              await useProjectsStore.getState().refreshProject(projectName);
              onSheetsChanged();
            }}
            busy={busy || generating || pending}
            onRestoringChange={setRestoring}
            checkBusy={() => isResourceBusy("character_derivative", projectName, resourceId)}
          />
          <AlertDialog
            open={dialog === "delete"}
            onOpenChange={(open) => {
              // 删除在途时忽略关闭
              if (!open && !pending) setDialog(null);
            }}
          >
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{t("assets:derivative_delete_title", { name })}</AlertDialogTitle>
              </AlertDialogHeader>
              <AlertDialogBody>
                <AlertDialogDescription>
                  {t(
                    derivative.referenced
                      ? "assets:derivative_delete_description_referenced"
                      : "assets:derivative_delete_description",
                    { token },
                  )}
                </AlertDialogDescription>
              </AlertDialogBody>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={pending}>{t("common:cancel")}</AlertDialogCancel>
                <AlertDialogAction variant="destructive" disabled={pending} onClick={() => void handleDelete()}>
                  {pending ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
                  {t("assets:delete")}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </>
      )}
    </li>
  );
}
