import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Plus, Trash2 } from "lucide-react";
import { AutoTextarea } from "@/components/legacy/AutoTextarea";
import { ConfirmDialog } from "@/components/legacy/ConfirmDialog";
import { itemIdWithinEpisode } from "@/utils/episode-display";

type StructureContentMode = "narration" | "drama" | "ad";

/** 新增分镜；`afterId` 为 null 时追加到末尾（空脚本里即第一条）。resolve 为是否成功。 */
export type InsertShotHandler = (afterId: string | null, novelText?: string) => Promise<boolean>;

interface InsertShotButtonProps {
  afterId: string | null;
  contentMode: StructureContentMode;
  onInsert: InsertShotHandler;
  label: string;
  disabled?: boolean;
  disabledHint?: string;
  /** icon：只显示图标（分镜详情头部）；compact：列表头部的小按钮；primary：空状态的主按钮。 */
  variant: "icon" | "compact" | "primary";
}

/**
 * 新增分镜按钮。剧情演绎与广告直接新增空分镜；旁白分镜的正文即配音内容，先弹框填写正文再新增。
 */
export function InsertShotButton({
  afterId,
  contentMode,
  onInsert,
  label,
  disabled = false,
  disabledHint,
  variant,
}: InsertShotButtonProps) {
  const { t } = useTranslation("dashboard");
  const textareaId = useId();
  const [narrationOpen, setNarrationOpen] = useState(false);
  const [novelText, setNovelText] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const run = async (text: string | undefined, close: () => void) => {
    if (submitting) return;
    setSubmitting(true);
    try {
      if (await onInsert(afterId, text)) close();
    } finally {
      setSubmitting(false);
    }
  };

  const handleClick = () => {
    if (contentMode === "narration") {
      setNovelText("");
      setNarrationOpen(true);
      return;
    }
    void run(undefined, () => {});
  };

  const className =
    variant === "icon"
      ? "sv-navbtn disabled:cursor-not-allowed disabled:opacity-50"
      : variant === "compact"
        ? "sv-navbtn inline-flex items-center gap-1 px-2 disabled:cursor-not-allowed disabled:opacity-50"
        : "arc-btn-primary focus-ring inline-flex items-center gap-1.5 rounded-lg px-4 py-2 text-[12.5px] font-semibold disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <>
      <button
        type="button"
        onClick={handleClick}
        disabled={disabled || submitting}
        title={disabledHint ?? label}
        aria-label={label}
        className={className}
      >
        <Plus className={variant === "icon" ? "h-3.5 w-3.5" : "h-3 w-3"} aria-hidden />
        {variant !== "icon" && <span>{label}</span>}
      </button>
      {contentMode === "narration" && (
        <ConfirmDialog
          open={narrationOpen}
          title={t("shot_insert_narration_title")}
          description={
            <div className="flex flex-col gap-2">
              <p className="m-0">{t("shot_insert_narration_desc")}</p>
              <label htmlFor={textareaId} className="text-[11px] font-medium" style={{ color: "var(--subtle-foreground)" }}>
                {t("shot_insert_narration_label")}
              </label>
              <AutoTextarea id={textareaId} value={novelText} onChange={setNovelText} disabled={submitting} />
            </div>
          }
          confirmLabel={t("shot_insert_narration_confirm")}
          loading={submitting}
          confirmDisabled={!novelText.trim()}
          onConfirm={() => run(novelText, () => setNarrationOpen(false))}
          onCancel={() => setNarrationOpen(false)}
        />
      )}
    </>
  );
}

interface ShotStructureActionsProps {
  segmentId: string;
  contentMode: StructureContentMode;
  /** 切镜同源的禁用条件（未保存草稿、保存中、重排或增删在途）。 */
  disabled: boolean;
  disabledHint?: string;
  /** 禁止移除的原因（生成任务在跑等），给出即禁用移除并以其作提示。 */
  removeBlockedHint?: string;
  onInsert?: InsertShotHandler;
  /** 移除当前分镜；resolve 为是否成功。 */
  onRemove?: (itemId: string) => Promise<boolean>;
}

/**
 * 分镜详情头部的「在此后插入」「移除分镜」动作。移除前弹 danger 确认框说明产物去向。
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
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removing, setRemoving] = useState(false);

  if (!onInsert && !onRemove) return null;

  const handleRemove = async () => {
    if (!onRemove || removing) return;
    setRemoving(true);
    try {
      if (await onRemove(segmentId)) setRemoveOpen(false);
    } finally {
      setRemoving(false);
    }
  };

  return (
    <>
      {onInsert && (
        <InsertShotButton
          afterId={segmentId}
          contentMode={contentMode}
          onInsert={onInsert}
          label={t("shot_insert_after")}
          disabled={disabled}
          disabledHint={disabledHint}
          variant="icon"
        />
      )}
      {onRemove && (
        <button
          type="button"
          onClick={() => setRemoveOpen(true)}
          disabled={disabled || removing || removeBlockedHint !== undefined}
          title={disabledHint ?? removeBlockedHint ?? t("shot_remove")}
          className="sv-navbtn disabled:cursor-not-allowed disabled:opacity-50"
          aria-label={t("shot_remove")}
        >
          <Trash2 className="h-3.5 w-3.5" />
        </button>
      )}
      {onRemove && (
        <ConfirmDialog
          open={removeOpen}
          title={t("shot_remove_title", { id: itemIdWithinEpisode(segmentId) })}
          description={t("shot_remove_desc")}
          confirmLabel={t("shot_remove_confirm")}
          tone="danger"
          loading={removing}
          confirmDisabled={removeBlockedHint !== undefined}
          onConfirm={() => {
            // 确认框打开后才出现的阻塞（如别处开始生成）同样拦下，不以打开时的状态为准。
            if (removeBlockedHint !== undefined) return;
            void handleRemove();
          }}
          onCancel={() => setRemoveOpen(false)}
        />
      )}
    </>
  );
}
