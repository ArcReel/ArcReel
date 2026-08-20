import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Loader2, Pencil, X } from "lucide-react";
import { API, type AssetRenameResult, type ProjectAssetType } from "@/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { errMsg } from "@/utils/async";
import { rejectIfAssetBusy } from "./assetBusyGuard";

interface EditableAssetNameProps {
  projectName: string;
  name: string;
  assetType: ProjectAssetType;
  /** 只读展示（引导演示项目）：不渲染重命名入口。 */
  readOnly?: boolean;
  /** 与卡片兄弟控件共享的禁用态（生成中 / 上传中）。 */
  busy?: boolean;
}

const ICON_BTN_CLS =
  "focus-ring inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.06)] disabled:cursor-not-allowed disabled:opacity-40";

/**
 * 资产卡片名称的行内重命名：hover 出铅笔 → 名称变行内输入框（Enter 提交 / Esc 取消）→
 * 点钩先取影响预览（dry-run）再弹二次确认框，确认后执行级联重命名。
 *
 * 重命名会级联改写全部剧集剧本引用与关联文件，属占用感知型操作：进入编辑与确认提交时
 * 均经 `rejectIfAssetBusy` 复核占用态。成功后经 `refreshProject` 单一漏斗刷新（改名后
 * 卡片按新 key 重建，本组件不持有跨改名状态）。
 */
export function EditableAssetName({
  projectName,
  name,
  assetType,
  readOnly = false,
  busy = false,
}: EditableAssetNameProps) {
  const { t } = useTranslation(["assets", "common"]);
  const [isEditing, setIsEditing] = useState(false);
  const [draft, setDraft] = useState(name);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [preview, setPreview] = useState<AssetRenameResult | null>(null);
  const [renaming, setRenaming] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isEditing]);

  const trimmed = draft.trim();
  const submitting = previewLoading || renaming;
  const canSubmit = trimmed.length > 0 && !submitting;

  const heading = (
    <h3
      className="display-serif min-w-0 flex-1 truncate text-[16px] font-semibold tracking-tight"
      style={{ color: "var(--color-text)" }}
    >
      {name}
    </h3>
  );

  if (readOnly) return heading;

  /**
   * 占用复核并作三路取：`rejectIfAssetBusy` 只看任务队列，而卡片自身在途的写请求（上传、
   * 保存）与本组件自己那次尚未刷新完的改名都是本地 state、不进队列，队列口径看不见，必须
   * 把 `busy` 与 `renaming` 并进同一道闸——否则渲染时刻禁用了按钮，提交时刻却放行同一份占用。
   * `renaming` 直到 `refreshProject` 结算才落下：那之前 `name` 仍是旧值，放行会拿过期的
   * 基准名再提交一次，把刚改成的新名又改回去。
   */
  const rejectIfBusy = () => {
    if (busy || renaming) {
      useAppStore.getState().pushToast(t("assets:rename_busy_hint"), "info");
      return true;
    }
    return rejectIfAssetBusy(assetType, projectName, name, t, "assets:rename_busy_hint");
  };

  const enterEdit = () => {
    if (rejectIfBusy()) return;
    setDraft(name);
    setIsEditing(true);
  };

  const cancelEdit = () => {
    setDraft(name);
    setIsEditing(false);
  };

  const requestPreview = async () => {
    if (!canSubmit) return;
    if (trimmed === name) {
      setIsEditing(false);
      return;
    }
    if (rejectIfBusy()) return;
    setPreviewLoading(true);
    try {
      const result = await API.renameProjectAsset(projectName, assetType, name, trimmed, { dryRun: true });
      setPreview(result);
    } catch (err) {
      useAppStore.getState().pushToast(t("assets:rename_failed", { message: errMsg(err) }), "error");
    } finally {
      setPreviewLoading(false);
    }
  };

  const executeRename = async () => {
    if (!preview) return;
    if (rejectIfBusy()) {
      setPreview(null);
      return;
    }
    setRenaming(true);
    try {
      const result = await API.renameProjectAsset(projectName, assetType, name, trimmed);
      useAppStore.getState().pushToast(t("assets:rename_success", { name: result.new_name }), "success");
      setPreview(null);
      setIsEditing(false);
      // 重命名已提交，刷新是独立的后续步骤：refreshProject 以结算值报告失败而不 reject，
      // 不单独提示的话卡片会停在旧名上，看着像改名没生效。cancelled 是项目已切走，静默。
      const refreshed = await useProjectsStore.getState().refreshProject(projectName);
      if (refreshed === "failed") {
        useAppStore.getState().pushToast(t("assets:rename_refresh_failed"), "warning");
      }
    } catch (err) {
      useAppStore.getState().pushToast(t("assets:rename_failed", { message: errMsg(err) }), "error");
      // 失败保持确认框关闭、编辑态保留，用户可改名后重试
      setPreview(null);
    } finally {
      setRenaming(false);
    }
  };

  if (!isEditing) {
    return (
      <div className="group flex min-w-0 flex-1 items-center gap-1">
        {heading}
        <button
          type="button"
          onClick={enterEdit}
          disabled={busy || renaming}
          title={t("assets:rename_asset")}
          aria-label={t("assets:rename_asset")}
          className={`${ICON_BTN_CLS} text-[var(--color-text-3)] opacity-0 hover:text-[var(--color-text)] focus-visible:opacity-100 group-hover:opacity-100`}
        >
          <Pencil className="h-3.5 w-3.5" />
        </button>
      </div>
    );
  }

  const impactDescription = preview ? (
    <div className="space-y-1.5">
      <p>
        {preview.references > 0 || preview.files > 0
          ? t("assets:rename_impact", {
              episodes: preview.episodes,
              references: preview.references,
              files: preview.files,
            })
          : t("assets:rename_impact_none")}
      </p>
      <p className="tabular-nums" style={{ color: "var(--color-text-2)" }}>
        {name} → {trimmed}
      </p>
    </div>
  ) : null;

  return (
    <div className="flex min-w-0 flex-1 items-center gap-1.5">
      <input
        ref={inputRef}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={(e) => {
          // 输入法组合输入中（如中文拼音）按 Enter 是在确认候选词，不应触发提交
          if (e.nativeEvent.isComposing) return;
          if (e.key === "Enter") {
            e.preventDefault();
            void requestPreview();
          } else if (e.key === "Escape") {
            e.preventDefault();
            cancelEdit();
          }
        }}
        disabled={submitting}
        aria-label={t("assets:rename_asset")}
        className="display-serif focus-ring min-w-0 flex-1 rounded border-b bg-transparent text-[16px] font-semibold tracking-tight outline-none"
        style={{ color: "var(--color-text)", borderColor: "var(--color-accent-soft)" }}
      />
      <button
        type="button"
        onClick={() => void requestPreview()}
        disabled={!canSubmit}
        title={t("common:save")}
        aria-label={t("common:save")}
        className={ICON_BTN_CLS}
        style={{ color: "var(--color-accent-2)" }}
      >
        {previewLoading ? (
          <Loader2 className="h-4 w-4 motion-safe:animate-spin" />
        ) : (
          <Check className="h-4 w-4" />
        )}
      </button>
      <button
        type="button"
        onClick={cancelEdit}
        disabled={submitting}
        title={t("common:cancel")}
        aria-label={t("common:cancel")}
        className={ICON_BTN_CLS}
        style={{ color: "var(--color-text-3)" }}
      >
        <X className="h-4 w-4" />
      </button>
      <ConfirmDialog
        open={preview !== null}
        title={t("assets:rename_confirm_title", { name })}
        description={impactDescription}
        confirmLabel={t("assets:rename_asset")}
        loadingLabel={t("assets:renaming")}
        loading={renaming}
        onConfirm={executeRename}
        onCancel={() => setPreview(null)}
      />
    </div>
  );
}
