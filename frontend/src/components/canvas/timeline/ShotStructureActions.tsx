import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import { Loader2, Plus, Trash2 } from "lucide-react";
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
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
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { TooltipIconButton } from "./TooltipIconButton";
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
  const { t } = useTranslation(["dashboard", "common"]);
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

  const busy = disabled || submitting;
  const trigger =
    variant === "icon" ? (
      <TooltipIconButton label={label} hint={disabledHint} disabled={busy} onClick={handleClick}>
        <Plus aria-hidden />
      </TooltipIconButton>
    ) : (
      <Button
        variant={variant === "primary" ? "default" : "ghost"}
        size={variant === "primary" ? "default" : "xs"}
        disabled={busy}
        onClick={handleClick}
      >
        <Plus aria-hidden data-icon="inline-start" />
        {label}
      </Button>
    );

  return (
    <>
      {trigger}
      {contentMode === "narration" && (
        <Dialog
          open={narrationOpen}
          onOpenChange={(next) => {
            // 提交中不关闭，结果出来前对话框保持原样
            if (!next && submitting) return;
            setNarrationOpen(next);
          }}
        >
          <DialogContent>
            <DialogHeader>
              <DialogTitle>{t("shot_insert_narration_title")}</DialogTitle>
              <DialogDescription>{t("shot_insert_narration_desc")}</DialogDescription>
            </DialogHeader>
            <DialogBody>
              <div className="flex flex-col gap-2">
                <Label htmlFor={textareaId}>{t("shot_insert_narration_label")}</Label>
                <Textarea
                  id={textareaId}
                  value={novelText}
                  onChange={(e) => setNovelText(e.target.value)}
                  disabled={submitting}
                />
              </div>
            </DialogBody>
            <DialogFooter>
              <DialogClose render={<Button variant="outline" disabled={submitting} />}>{t("common:cancel")}</DialogClose>
              <Button
                disabled={submitting || !novelText.trim()}
                onClick={() => void run(novelText, () => setNarrationOpen(false))}
              >
                {submitting ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
                {t("shot_insert_narration_confirm")}
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>
      )}
    </>
  );
}

interface ShotStructureActionsProps {
  segmentId: string;
  contentMode: StructureContentMode;
  /** 切镜同源的禁用条件（未保存修改、保存中、重排或增删在途）。 */
  disabled: boolean;
  disabledHint?: string;
  /** 禁止移除的原因（生成任务在跑等），给出即禁用移除并以其作提示。 */
  removeBlockedHint?: string;
  onInsert?: InsertShotHandler;
  /** 移除当前分镜；resolve 为是否成功。 */
  onRemove?: (itemId: string) => Promise<boolean>;
}

/**
 * 分镜详情头部的「在此后插入」「移除分镜」动作。移除不可撤销，先确认并说明产物去向。
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
  const { t } = useTranslation(["dashboard", "common"]);
  const [removeOpen, setRemoveOpen] = useState(false);
  const [removing, setRemoving] = useState(false);

  if (!onInsert && !onRemove) return null;

  const handleRemove = async () => {
    // 确认框打开后才出现的阻塞（如别处开始生成）同样拦下，不以打开时的状态为准。
    if (!onRemove || removing || removeBlockedHint !== undefined) return;
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
        <>
          <TooltipIconButton
            label={t("shot_remove")}
            hint={disabledHint ?? removeBlockedHint}
            disabled={disabled || removing || removeBlockedHint !== undefined}
            onClick={() => setRemoveOpen(true)}
          >
            <Trash2 aria-hidden />
          </TooltipIconButton>
          <AlertDialog
            open={removeOpen}
            onOpenChange={(next) => {
              if (!next && removing) return;
              setRemoveOpen(next);
            }}
          >
            <AlertDialogContent>
              <AlertDialogHeader>
                <AlertDialogTitle>{t("shot_remove_title", { id: itemIdWithinEpisode(segmentId) })}</AlertDialogTitle>
                <AlertDialogDescription>{t("shot_remove_desc")}</AlertDialogDescription>
              </AlertDialogHeader>
              <AlertDialogFooter>
                <AlertDialogCancel disabled={removing}>{t("common:cancel")}</AlertDialogCancel>
                <AlertDialogAction
                  variant="destructive"
                  disabled={removing || removeBlockedHint !== undefined}
                  onClick={() => void handleRemove()}
                >
                  {removing ? <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" /> : null}
                  {t("shot_remove_confirm")}
                </AlertDialogAction>
              </AlertDialogFooter>
            </AlertDialogContent>
          </AlertDialog>
        </>
      )}
    </>
  );
}
