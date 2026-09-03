import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Check, Layers, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import { API } from "@/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { CopyButton } from "@/components/ui/CopyButton";
import { GlassPopover } from "@/components/ui/GlassPopover";
import { useAppStore } from "@/stores/app-store";
import { errMsg } from "@/utils/async";
import { rejectIfAssetBusy } from "./assetBusyGuard";
import { CharacterDerivativeSheet } from "./CharacterDerivativeSheet";
import { useCharacterDerivativeSheets } from "./useCharacterDerivativeSheets";
import type { CharacterDerivative } from "@/types";

interface CharacterDerivativesButtonProps {
  projectName: string;
  characterName: string;
  derivatives: Record<string, CharacterDerivative>;
  /** 本体是否已有资产图：衍生图是对它的一次编辑，没有本体图就不放行生成。 */
  ownerHasSheet?: boolean;
  /** 与卡片兄弟控件共享的禁用态（生成中 / 上传中 / 保存中）。 */
  busy?: boolean;
  /** 单一漏斗刷新：每次写入结算后重新拉取项目数据。 */
  onReload?: () => Promise<unknown> | void;
}

const ICON_BTN_CLS =
  "focus-ring inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md transition-colors hover:bg-[oklch(1_0_0_/_0.06)] disabled:cursor-not-allowed disabled:opacity-40";
const ROW_BTN_CLS =
  "focus-ring grid h-6 w-6 shrink-0 place-items-center rounded-md transition-colors hover:bg-white/10 disabled:cursor-not-allowed disabled:opacity-40";
const INPUT_CLS =
  "focus-ring w-full rounded-md px-2 py-1 text-[12px] outline-none placeholder:text-[var(--color-text-4)]";
const INPUT_STYLE: React.CSSProperties = {
  background: "oklch(0.16 0.010 265 / 0.7)",
  border: "1px solid var(--color-hairline)",
  color: "var(--color-text)",
};

/** 脚本正文里引用该衍生的记号，浮层内可直接复制。 */
function derivativeToken(characterName: string, derivativeName: string): string {
  return `@[${characterName}/${derivativeName}]`;
}

/**
 * 角色卡上的「衍生」入口：带数量的图标按钮 + 浮层，浮层内登记与管理该角色的衍生
 * （新增、改描述、改名、删除），并展示可复制的 `@[角色/衍生]` 记号；每条衍生下由
 * {@link CharacterDerivativeSheet} 接上它的资产图与图上的各项操作。
 *
 * 衍生登记的写入随角色一起占用，属占用感知型操作：按钮随兄弟控件的 `busy` 禁用，每次提交
 * 前再经 `rejectIfAssetBusy` 与本组件自己的在途标志复核。衍生图只能由本体图编辑而来，
 * 浮层内不提供上传入口。
 */
export function CharacterDerivativesButton({
  projectName,
  characterName,
  derivatives,
  ownerHasSheet = false,
  busy = false,
  onReload,
}: CharacterDerivativesButtonProps) {
  const { t } = useTranslation("assets");
  const anchorRef = useRef<HTMLButtonElement>(null);
  const renameInputRef = useRef<HTMLInputElement>(null);
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDescription, setNewDescription] = useState("");
  const [descriptionDrafts, setDescriptionDrafts] = useState<Record<string, string>>({});
  const [renaming, setRenaming] = useState<string | null>(null);
  const [renameDraft, setRenameDraft] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<string | null>(null);

  const entries = Object.entries(derivatives);
  const count = entries.length;
  // 兄弟控件在写时面板一律收起：占用态由渲染派生，不靠 effect 追平。
  const expanded = open && !busy;
  const { statuses, refresh: refreshSheets } = useCharacterDerivativeSheets(
    projectName,
    characterName,
    expanded,
  );

  useEffect(() => {
    if (renaming) renameInputRef.current?.select();
  }, [renaming]);

  /** 提交时刻复核：队列占用由 `rejectIfAssetBusy` 看，卡片本地在途请求由 `busy` / `pending` 看。 */
  const rejectIfBusy = () => {
    if (busy || pending) {
      useAppStore.getState().pushToast(t("assets:derivative_busy_hint"), "info");
      return true;
    }
    return rejectIfAssetBusy("character", projectName, characterName, t, "assets:derivative_busy_hint");
  };

  /** 跑一次写入并结算；返回是否成功，失败时调用方保留用户已填的内容以便重试。 */
  const run = async (action: () => Promise<unknown>, successMessage: string): Promise<boolean> => {
    if (rejectIfBusy()) return false;
    setPending(true);
    try {
      await action();
      await onReload?.();
      refreshSheets();
      useAppStore.getState().pushToast(successMessage, "success");
      return true;
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
      return false;
    } finally {
      setPending(false);
    }
  };

  const dropDraft = (name: string) =>
    setDescriptionDrafts((prev) => {
      if (!(name in prev)) return prev;
      const next = { ...prev };
      delete next[name];
      return next;
    });

  const handleAdd = async () => {
    const name = newName.trim();
    if (!name) return;
    const ok = await run(
      () => API.addCharacterDerivative(projectName, characterName, name, newDescription.trim()),
      t("assets:derivative_added", { name }),
    );
    if (!ok) return;
    setNewName("");
    setNewDescription("");
  };

  const handleSaveDescription = async (name: string) => {
    const draft = descriptionDrafts[name];
    if (draft === undefined) return;
    const ok = await run(
      () => API.updateCharacterDerivative(projectName, characterName, name, draft),
      t("assets:derivative_saved", { name }),
    );
    // 草稿与服务端值一致后 dirty 自然落下，但删掉它才能让下一次上游变更直接显示新值
    if (ok) dropDraft(name);
  };

  const handleRename = async (name: string) => {
    const next = renameDraft.trim();
    if (!next || next === name) {
      setRenaming(null);
      return;
    }
    const ok = await run(
      () => API.renameCharacterDerivative(projectName, characterName, name, next),
      t("assets:derivative_renamed", { name: next }),
    );
    if (!ok) return;
    dropDraft(name);
    setRenaming(null);
  };

  /** 删除抹掉的是用户写下的变化描述且不可撤销，与站内其它破坏性资产操作一样先确认。 */
  const handleDelete = async (name: string) => {
    const ok = await run(
      () => API.deleteCharacterDerivative(projectName, characterName, name),
      t("assets:derivative_deleted", { name }),
    );
    setDeleteTarget(null);
    if (ok) dropDraft(name);
  };

  return (
    <>
      <button
        ref={anchorRef}
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        disabled={busy}
        title={t("assets:derivatives")}
        aria-label={t("assets:derivatives_with_count", { n: count })}
        aria-expanded={expanded}
        className={`${ICON_BTN_CLS} relative`}
        style={{ color: count > 0 ? "var(--color-accent-2)" : "var(--color-text-3)" }}
      >
        <Layers className="h-3.5 w-3.5" />
        {count > 0 && (
          <span
            aria-hidden
            className="absolute -right-0.5 -top-0.5 grid h-3.5 min-w-3.5 place-items-center rounded-full px-[3px] text-[9px] font-semibold leading-none"
            style={{ background: "var(--color-accent-dim)", color: "var(--color-accent-2)" }}
          >
            {count}
          </span>
        )}
      </button>

      <GlassPopover
        open={expanded}
        onClose={() => setOpen(false)}
        anchorRef={anchorRef}
        width="w-80"
        maxHeight={420}
        className="flex flex-col overflow-y-auto p-3"
      >
        <p className="font-mono text-[10px] uppercase tracking-[0.18em]" style={{ color: "var(--color-text-4)" }}>
          DERIVATIVES
        </p>
        <p className="mt-1 text-[11px] leading-[1.5]" style={{ color: "var(--color-text-4)" }}>
          {t("assets:derivative_hint")}
        </p>

        <ul className="mt-3 space-y-2.5">
          {entries.map(([name, derivative]) => {
            const draft = descriptionDrafts[name] ?? derivative.description;
            const dirty = draft !== derivative.description;
            return (
              <li
                key={name}
                className="rounded-lg p-2"
                style={{ border: "1px solid var(--color-hairline-soft)" }}
              >
                {renaming === name ? (
                  <div className="flex items-center gap-1">
                    <input
                      ref={renameInputRef}
                      className={INPUT_CLS}
                      style={INPUT_STYLE}
                      aria-label={t("assets:derivative_rename")}
                      value={renameDraft}
                      onChange={(e) => setRenameDraft(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") void handleRename(name);
                        if (e.key === "Escape") setRenaming(null);
                      }}
                    />
                    <button
                      type="button"
                      className={ROW_BTN_CLS}
                      disabled={pending}
                      aria-label={t("assets:derivative_rename_confirm")}
                      onClick={() => void handleRename(name)}
                      style={{ color: "var(--color-accent-2)" }}
                    >
                      <Check className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      className={ROW_BTN_CLS}
                      aria-label={t("assets:cancel")}
                      onClick={() => setRenaming(null)}
                      style={{ color: "var(--color-text-3)" }}
                    >
                      <X className="h-3.5 w-3.5" />
                    </button>
                  </div>
                ) : (
                  <div className="flex items-center gap-1">
                    <span
                      className="min-w-0 flex-1 truncate text-[12px] font-semibold"
                      style={{ color: "var(--color-text)" }}
                    >
                      {name}
                    </span>
                    <button
                      type="button"
                      className={ROW_BTN_CLS}
                      disabled={pending}
                      aria-label={t("assets:derivative_rename")}
                      onClick={() => {
                        setRenameDraft(name);
                        setRenaming(name);
                      }}
                      style={{ color: "var(--color-text-3)" }}
                    >
                      <Pencil className="h-3.5 w-3.5" />
                    </button>
                    <button
                      type="button"
                      className={ROW_BTN_CLS}
                      disabled={pending}
                      aria-label={t("assets:derivative_delete", { name })}
                      onClick={() => setDeleteTarget(name)}
                      style={{ color: "var(--color-text-3)" }}
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                    </button>
                  </div>
                )}

                <div className="mt-1.5 flex items-center gap-1">
                  <code
                    className="min-w-0 flex-1 truncate rounded px-1.5 py-0.5 font-mono text-[10px]"
                    style={{ background: "oklch(0.16 0.010 265 / 0.7)", color: "var(--color-text-3)" }}
                  >
                    {derivativeToken(characterName, name)}
                  </code>
                  <CopyButton text={derivativeToken(characterName, name)} />
                </div>

                {derivative.referenced !== undefined && (
                  <p
                    className="mt-1 text-[10px]"
                    style={{ color: derivative.referenced ? "var(--color-text-3)" : "var(--color-text-4)" }}
                  >
                    {t(derivative.referenced ? "assets:derivative_referenced" : "assets:derivative_unreferenced")}
                  </p>
                )}

                <textarea
                  rows={2}
                  className={`${INPUT_CLS} mt-1.5 resize-none leading-[1.5]`}
                  style={INPUT_STYLE}
                  aria-label={t("assets:derivative_description_of", { name })}
                  placeholder={t("assets:derivative_description_placeholder")}
                  value={draft}
                  onChange={(e) =>
                    setDescriptionDrafts((prev) => ({ ...prev, [name]: e.target.value }))
                  }
                />
                {dirty && (
                  <button
                    type="button"
                    className="focus-ring mt-1 rounded-md px-2 py-1 text-[11px] font-medium disabled:cursor-not-allowed disabled:opacity-40"
                    disabled={pending}
                    onClick={() => void handleSaveDescription(name)}
                    style={{ background: "var(--color-accent-dim)", color: "var(--color-accent-2)" }}
                  >
                    {t("assets:save")}
                  </button>
                )}

                <CharacterDerivativeSheet
                  projectName={projectName}
                  characterName={characterName}
                  derivativeName={name}
                  status={statuses[name]}
                  ownerHasSheet={ownerHasSheet}
                  busy={busy || pending}
                  onRestore={async () => {
                    await onReload?.();
                    refreshSheets();
                  }}
                />
              </li>
            );
          })}
        </ul>

        {count === 0 && (
          <p className="mt-3 text-[11px]" style={{ color: "var(--color-text-4)" }}>
            {t("assets:derivative_empty")}
          </p>
        )}

        <div className="mt-3 space-y-1.5 border-t pt-3" style={{ borderColor: "var(--color-hairline-soft)" }}>
          <input
            className={INPUT_CLS}
            style={INPUT_STYLE}
            aria-label={t("assets:derivative_name")}
            placeholder={t("assets:derivative_name_placeholder")}
            value={newName}
            onChange={(e) => setNewName(e.target.value)}
          />
          <textarea
            rows={2}
            className={`${INPUT_CLS} resize-none leading-[1.5]`}
            style={INPUT_STYLE}
            aria-label={t("assets:derivative_description")}
            placeholder={t("assets:derivative_description_placeholder")}
            value={newDescription}
            onChange={(e) => setNewDescription(e.target.value)}
          />
          <button
            type="button"
            className="focus-ring inline-flex items-center gap-1 rounded-md px-2 py-1 text-[11px] font-medium disabled:cursor-not-allowed disabled:opacity-40"
            disabled={pending || newName.trim().length === 0}
            onClick={() => void handleAdd()}
            style={{ background: "var(--color-accent-dim)", color: "var(--color-accent-2)" }}
          >
            {pending ? <Loader2 className="h-3 w-3 animate-spin" /> : <Plus className="h-3 w-3" />}
            {t("assets:derivative_add")}
          </button>
        </div>
      </GlassPopover>

      <ConfirmDialog
        open={deleteTarget !== null}
        tone="danger"
        title={t("assets:derivative_delete_confirm")}
        description={
          deleteTarget !== null ? (
            <span className="font-mono">{derivativeToken(characterName, deleteTarget)}</span>
          ) : null
        }
        confirmLabel={t("assets:delete")}
        loading={pending}
        onConfirm={() => {
          if (deleteTarget !== null) void handleDelete(deleteTarget);
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </>
  );
}
