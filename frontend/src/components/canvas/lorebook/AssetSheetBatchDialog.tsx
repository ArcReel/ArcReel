import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { assetSheetResource, enqueueAssetSheetBatch } from "@/actions/generation";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogBody,
  DialogClose,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useAppStore } from "@/stores/app-store";
import { useAssetSheetBatchStore } from "@/stores/asset-sheet-batch-store";
import { errMsg } from "@/utils/async";
import { costEntries, formatCurrencyAmount } from "@/utils/cost-format";
import type { AssetSheetBatchPreview, AssetSheetBatchScope, AssetSheetRef, AssetSheetType } from "@/types";
import { isAssetBusy, useAssetSlotsBusy } from "./assetBusyGuard";

const TYPE_ORDER: AssetSheetType[] = ["character", "scene", "prop", "product"];

function refLabel(ref: AssetSheetRef): string {
  return ref.derivative ? `${ref.name} / ${ref.derivative}` : ref.name;
}

function ownerOf(unitId: string): string {
  // `<类型>/<本体>`：去掉类型段即本体名。
  return unitId.slice(unitId.indexOf("/") + 1);
}

/**
 * 资产图批量生成的确认框：按类型列出要生成的资产图、跳过项与原因，能算出时给出预估费用。
 * 确认后提交一批并交给批次跟踪，整批结束时汇总成一条通知。
 */
export function AssetSheetBatchDialog({
  projectName,
  scope,
  onClose,
}: {
  projectName: string;
  scope: AssetSheetBatchScope;
  onClose: () => void;
}) {
  const { t } = useTranslation(["assets", "common"]);
  const [preview, setPreview] = useState<AssetSheetBatchPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const scopeKey = JSON.stringify(scope);

  useEffect(() => {
    const controller = new AbortController();
    API.previewAssetSheetBatch(projectName, JSON.parse(scopeKey) as AssetSheetBatchScope, { signal: controller.signal })
      .then((res) => {
        if (!controller.signal.aborted) setPreview(res);
      })
      .catch((err: unknown) => {
        if (!controller.signal.aborted) setError(errMsg(err));
      });
    return () => controller.abort();
  }, [projectName, scopeKey]);

  const groups = useMemo(
    () =>
      TYPE_ORDER.map((type) => ({
        type,
        targets: (preview?.targets ?? []).filter((item) => item.asset_type === type),
      })).filter((group) => group.targets.length > 0),
    [preview],
  );

  const cost = costEntries(preview?.estimated_cost ?? undefined)
    .map(([currency, amount]) => formatCurrencyAmount(currency, amount))
    .join(" + ");

  const slots = useMemo(() => (preview?.targets ?? []).map(assetSheetResource), [preview]);
  // 打开期间目标可能被别处占用（另一标签页、Agent、image_edit），从那一刻起同步禁掉提交，
  // 与下面提交时刻的复核一起覆盖「渲染 → 点击」这个竞态窗口。
  const busyNow = useAssetSlotsBusy(projectName, slots);

  const handleConfirm = async () => {
    // 服务端准入只按该资产的生成任务类型判定，而 image_edit 与生成任务共用同一占用槽：
    // 请求发出前逐项新鲜复核，命中即拒绝，避免两个操作并发覆写同一张资产图。
    if (slots.some((slot) => isAssetBusy(slot.kind, projectName, slot.id))) {
      useAppStore.getState().pushToast(t("sheet_batch_busy_hint"), "info");
      return;
    }
    setSubmitting(true);
    try {
      const submitted = await enqueueAssetSheetBatch(projectName, scope);
      const members = submitted.members.filter((member) => member.status !== "blocked");
      if (members.length > 0) {
        useAssetSheetBatchStore.getState().track({ batchId: submitted.batch_id, projectName, members });
      }
      onClose();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSubmitting(false);
    }
  };

  let body;
  if (error) {
    body = <p role="alert" className="text-destructive">{t("sheet_batch_load_failed", { message: error })}</p>;
  } else if (!preview) {
    body = <p className="text-subtle-foreground">{t("sheet_batch_loading")}</p>;
  } else {
    body = (
      <div className="flex flex-col gap-3" data-testid="asset-sheet-batch-preview">
        {groups.length === 0 ? (
          <p>{t("sheet_batch_nothing")}</p>
        ) : (
          <div>
            <p className="font-medium">
              {t("sheet_batch_targets", { count: preview.targets.length })}
            </p>
            {groups.map((group) => (
              <div key={group.type} className="mt-2">
                <p className="text-xs font-medium text-muted-foreground">{t(`type.${group.type}`)}</p>
                <ul className="mt-0.5 flex flex-col gap-0.5 text-subtle-foreground">
                  {group.targets.map((item) => (
                    <li key={item.unit_id}>
                      {refLabel(item)}
                      {item.depends_on && (
                        <span className="text-muted-foreground">
                          {" · "}
                          {t("sheet_batch_after_owner", { owner: ownerOf(item.depends_on) })}
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </div>
            ))}
          </div>
        )}
        {preview.skipped.length > 0 && (
          <div>
            <p className="font-medium">{t("sheet_batch_skipped", { count: preview.skipped.length })}</p>
            <ul className="mt-0.5 flex flex-col gap-0.5 text-subtle-foreground">
              {preview.skipped.map((item) => (
                <li key={item.unit_id}>
                  {t(`type.${item.asset_type}`)} · {refLabel(item)}
                  <span className="text-muted-foreground">
                    {" · "}
                    {t(`sheet_batch_skip.${item.reason}`)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {preview.targets.length > 0 && (
          <>
            <p>{cost ? t("sheet_batch_cost", { cost }) : t("sheet_batch_cost_unknown")}</p>
            {preview.unpriced_count > 0 && <p>{t("sheet_batch_unpriced", { count: preview.unpriced_count })}</p>}
          </>
        )}
      </div>
    );
  }

  const title = "episode_id" in scope ? t("sheet_batch_title_episode") : t("sheet_batch_title_type");

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        // 提交在途时忽略 Esc 与遮罩点击
        if (!next && !submitting) onClose();
      }}
    >
      <DialogContent showCloseButton={!submitting}>
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>
        <DialogBody tabIndex={0} role="region" aria-label={title}>
          <div className="text-sm">{body}</div>
          {/* 禁用按钮不解释原因等于让人无从下手：被占用时把原因常驻显示，
              点击事件到不了 handleConfirm，光靠提交时弹 toast 是够不着的。 */}
          {busyNow && (
            <p role="status" className="mt-3 text-xs text-subtle-foreground" data-testid="asset-sheet-batch-busy-hint">
              {t("sheet_batch_busy_hint")}
            </p>
          )}
        </DialogBody>
        <DialogFooter>
          <DialogClose render={<Button variant="outline" />} disabled={submitting}>
            {t("common:cancel")}
          </DialogClose>
          <Button disabled={submitting || busyNow || !preview || preview.targets.length === 0} onClick={() => void handleConfirm()}>
            {submitting && <Loader2 aria-hidden data-icon="inline-start" className="animate-spin" />}
            {submitting ? t("sheet_batch_submitting") : t("sheet_batch_confirm")}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
