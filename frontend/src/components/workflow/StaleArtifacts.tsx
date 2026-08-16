import { useId } from "react";
import { useTranslation } from "react-i18next";
import { UnitTag } from "./UnitTag";
import { ARTIFACT_TONES, INLINE_ACTION_CLS } from "./state-language";

interface Props {
  staleIds: string[];
  /** 跳到画布上的该单元；那里已经有预览与版本历史。 */
  onView?: (unitId: string) => void;
  /** 显式重生。没有回调时只陈述状态，不长出一个点了没反应的按钮。 */
  onRegenerate?: (unitIds: string[]) => void;
  /** 重生请求在途；同一步骤的兄弟控件同步禁用。 */
  busy?: boolean;
}

/**
 * 已过时但仍然可用的产物。
 *
 * 这一段的立场是「保留是默认，重做要你亲自点」：过时只说明产物比当前内容旧，文件还在、
 * 还照常参与成片，也不挡着这一集走到导出。所以这里既不自动重生，也不把它算进缺口，
 * 只把「去看看」和「确实要重做」两个入口摆出来——后者要花钱，必须是用户按下去的。
 */
export function StaleArtifacts({ staleIds, onView, onRegenerate, busy }: Props) {
  const { t } = useTranslation("workflow");
  const headingId = useId();
  if (staleIds.length === 0) return null;

  return (
    <section
      aria-labelledby={headingId}
      className="rounded-lg px-3 py-2"
      style={{
        background: ARTIFACT_TONES.stale.soft,
        border: `1px solid ${ARTIFACT_TONES.stale.ring}`,
      }}
    >
      <h4
        id={headingId}
        className="text-[12px] font-medium"
        style={{ color: ARTIFACT_TONES.stale.color }}
      >
        {t("stale_title", { count: staleIds.length })}
      </h4>
      <p className="mt-0.5 text-[11.5px] leading-relaxed" style={{ color: "var(--color-text-3)" }}>
        {t("stale_still_usable")}
      </p>
      <ul className="mt-1.5 max-h-56 space-y-1 overflow-y-auto">
        {staleIds.map((unitId) => (
          <li key={unitId} className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <UnitTag unitId={unitId} />
            {onView && (
              <button
                type="button"
                onClick={() => onView(unitId)}
                aria-label={t("stale_view_unit", { id: unitId })}
                className={INLINE_ACTION_CLS}
                style={{ color: "var(--color-text-2)" }}
              >
                {t("stale_view")}
              </button>
            )}
            {onRegenerate && (
              <button
                type="button"
                disabled={busy}
                onClick={() => onRegenerate([unitId])}
                aria-label={t("stale_regenerate_unit", { id: unitId })}
                className={INLINE_ACTION_CLS}
                style={{ color: ARTIFACT_TONES.stale.color }}
              >
                {t("stale_regenerate")}
              </button>
            )}
          </li>
        ))}
      </ul>
      {onRegenerate && staleIds.length > 1 && (
        <button
          type="button"
          disabled={busy}
          onClick={() => onRegenerate(staleIds)}
          className={`mt-2 ${INLINE_ACTION_CLS}`}
          style={{ color: ARTIFACT_TONES.stale.color }}
        >
          {t("stale_regenerate_all", { count: staleIds.length })}
        </button>
      )}
    </section>
  );
}
