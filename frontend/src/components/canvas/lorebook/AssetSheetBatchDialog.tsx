import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { API } from "@/api";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { useAppStore } from "@/stores/app-store";
import { useAssetSheetBatchStore } from "@/stores/asset-sheet-batch-store";
import { errMsg } from "@/utils/async";
import { costEntries, formatCurrencyAmount } from "@/utils/cost-format";
import type { AssetSheetBatchPreview, AssetSheetBatchScope, AssetSheetRef, AssetSheetType } from "@/types";

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
  const { t } = useTranslation("assets");
  const [preview, setPreview] = useState<AssetSheetBatchPreview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const scopeKey = JSON.stringify(scope);

  useEffect(() => {
    let cancelled = false;
    API.previewAssetSheetBatch(projectName, JSON.parse(scopeKey) as AssetSheetBatchScope)
      .then((res) => {
        if (!cancelled) setPreview(res);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(errMsg(err));
      });
    return () => {
      cancelled = true;
    };
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

  const handleConfirm = async () => {
    setSubmitting(true);
    try {
      const submitted = await API.submitAssetSheetBatch(projectName, scope);
      const members = submitted.members.filter((member) => member.status !== "blocked");
      if (members.length > 0) {
        useAssetSheetBatchStore.getState().track({ batchId: submitted.batch_id, projectName, members });
      }
      const queuedCount = members.filter((member) => member.task_id).length;
      if (queuedCount > 0) useAppStore.getState().pushToast(t("sheet_batch_submitted", { count: queuedCount }), "success");
      onClose();
    } catch (err) {
      useAppStore.getState().pushToast(errMsg(err), "error");
    } finally {
      setSubmitting(false);
    }
  };

  let body;
  if (error) {
    body = <p>{t("sheet_batch_load_failed", { message: error })}</p>;
  } else if (!preview) {
    body = <p>{t("sheet_batch_loading")}</p>;
  } else {
    body = (
      <div className="space-y-3" data-testid="asset-sheet-batch-preview">
        {groups.length === 0 ? (
          <p>{t("sheet_batch_nothing")}</p>
        ) : (
          <div>
            <p className="font-medium" style={{ color: "var(--color-text-2)" }}>
              {t("sheet_batch_targets", { count: preview.targets.length })}
            </p>
            {groups.map((group) => (
              <div key={group.type} className="mt-1.5">
                <p className="text-[11px] uppercase tracking-[0.08em]" style={{ color: "var(--color-text-4)" }}>
                  {t(`type.${group.type}`)}
                </p>
                <ul className="mt-0.5 space-y-0.5">
                  {group.targets.map((item) => (
                    <li key={item.unit_id}>
                      {refLabel(item)}
                      {item.depends_on && (
                        <span style={{ color: "var(--color-text-4)" }}>
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
            <p className="font-medium" style={{ color: "var(--color-text-2)" }}>
              {t("sheet_batch_skipped", { count: preview.skipped.length })}
            </p>
            <ul className="mt-0.5 space-y-0.5">
              {preview.skipped.map((item) => (
                <li key={item.unit_id}>
                  {t(`type.${item.asset_type}`)} · {refLabel(item)}
                  <span style={{ color: "var(--color-text-4)" }}>
                    {" · "}
                    {t(`sheet_batch_skip.${item.reason}`)}
                  </span>
                </li>
              ))}
            </ul>
          </div>
        )}
        {preview.targets.length > 0 && (
          <p>{cost ? t("sheet_batch_cost", { cost }) : t("sheet_batch_cost_unknown")}</p>
        )}
      </div>
    );
  }

  return (
    <ConfirmDialog
      open
      title={"episode_id" in scope ? t("sheet_batch_title_episode") : t("sheet_batch_title_type")}
      description={body}
      confirmLabel={t("sheet_batch_confirm")}
      loadingLabel={t("sheet_batch_submitting")}
      loading={submitting}
      confirmDisabled={!preview || preview.targets.length === 0}
      onConfirm={handleConfirm}
      onCancel={onClose}
    />
  );
}
