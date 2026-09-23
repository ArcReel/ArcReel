import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Trash2 } from "lucide-react";
import { AutoTextarea } from "@/components/ui/AutoTextarea";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";

interface ShotStructureActionsProps {
  segmentId: string;
  contentMode: "narration" | "drama" | "ad";
  /** 切镜同源的禁用条件（未保存草稿、保存中、重排或增删在途）。 */
  disabled: boolean;
  disabledHint?: string;
  /** 禁止移除的原因（生成任务在跑、唯一分镜等），给出即禁用移除并以其作提示。 */
  removeBlockedHint?: string;
  /** 在当前分镜之后新增分镜；旁白分镜带上正文。resolve 为是否成功。 */
  onInsert?: (afterId: string, novelText?: string) => Promise<boolean>;
  /** 移除当前分镜；resolve 为是否成功。 */
  onRemove?: (itemId: string) => Promise<boolean>;
}

/**
 * 分镜详情头部的「新增分镜」「移除分镜」动作。
 *
 * 剧情演绎与广告直接新增空分镜；旁白分镜的正文即配音内容，先弹框填写正文再新增。
 * 移除前弹 danger 确认框说明产物去向。
 */
export function ShotStructureActions({
  segmentId,
  contentMode,
  disabled,
  disabledHint,
  removeBlockedHint,
  onInsert,
  onRemove,
}: ShotStructureActionsProps) {
  const { t } = useTranslation("dashboard");
  const textareaId = useId();
  const [narrationOpen, setNarrationOpen] = useState(false);
  const [novelText, setNovelText] = useState("");
  const [removeOpen, setRemoveOpen] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  if (!onInsert && !onRemove) return null;

  const run = async (action: () => Promise<boolean>, close: () => void) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      if (await action()) close();
    } finally {
      setSubmitting(false);
    }
  };

  const handleInsertClick = () => {
    if (!onInsert) return;
    if (contentMode === "narration") {
      setNovelText("");
      setNarrationOpen(true);
      return;
    }
    void run(() => onInsert(segmentId), () => {});
  };

  return (
    <>
      {onInsert && (
        <button
          type="button"
          onClick={handleInsertClick}
          disabled={disabled || submitting}
          title={disabledHint ?? t("shot_insert_after")}
          className="sv-navbtn disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={t("shot_insert_after")}
        >
          <Plus className="h-3.5 w-3.5" />
        </button>
      )}
      {onRemove && (
        <button
          type="button"
          onClick={() => setRemoveOpen(true)}
          disabled={disabled || submitting || removeBlockedHint !== undefined}
          title={disabledHint ?? removeBlockedHint ?? t("shot_remove")}
          className="sv-navbtn disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={t("shot_remove")}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}
      {onInsert && contentMode === "narration" && (
        <ConfirmDialog
          open={narrationOpen}
          title={t("shot_insert_narration_title")}
          description={
            <div className="flex flex-col gap-2">
              <p className="m-0">{t("shot_insert_narration_desc")}</p>
              <label htmlFor={textareaId} className="text-[11px] font-medium" style={{ color: "var(--color-text-2)" }}>
                {t("shot_insert_narration_label")}
              </label>
              <AutoTextarea id={textareaId} value={novelText} onChange={setNovelText} disabled={submitting} />
            </div>
          }
          confirmLabel={t("shot_insert_narration_confirm")}
          loading={submitting}
          confirmDisabled={!novelText.trim()}
          onConfirm={() => run(() => onInsert(segmentId, novelText), () => setNarrationOpen(false))}
          onCancel={() => setNarrationOpen(false)}
        />
      )}
      {onRemove && (
        <ConfirmDialog
          open={removeOpen}
          title={t("shot_remove_title", { id: segmentId })}
          description={t("shot_remove_desc")}
          confirmLabel={t("shot_remove_confirm")}
          tone="danger"
          loading={submitting}
          confirmDisabled={removeBlockedHint !== undefined}
          onConfirm={() => {
            // 确认框打开后才出现的阻塞（如别处开始生成）同样拦下，不以打开时的状态为准。
            if (removeBlockedHint !== undefined) return;
            void run(() => onRemove(segmentId), () => setRemoveOpen(false));
          }}
          onCancel={() => setRemoveOpen(false)}
        />
      )}
    </>
  );
}
