import { useEffect } from "react";
import { useTranslation } from "react-i18next";
import type { ReferenceRequestOptions } from "@/types";

type NarrationDelivery = NonNullable<ReferenceRequestOptions["narration_delivery"]>;

interface Props {
  value: NarrationDelivery;
  onChange: (value: NarrationDelivery) => void;
  disabled?: boolean;
  /**
   * 该模型的成片时长由端点固定。ArcReel 申请不到装得下旁白的时长，「使用当前 TTS」因此
   * 不可选，并在旁边给出可见的原因说明；后端对这种请求也会结构化拒绝。
   */
  ttsDurationEndpointFixed?: boolean;
  compact?: boolean;
}

/** Request-local narration delivery choice; callers must not persist it into script/project state. */
export function NarrationDeliveryChoice({
  value,
  onChange,
  disabled,
  ttsDurationEndpointFixed = false,
  compact = false,
}: Props) {
  const { t } = useTranslation("dashboard");
  const notice = ttsDurationEndpointFixed ? t("narration_delivery_tts_duration_endpoint_fixed") : null;
  // 已选中 use_tts 的用户换到这种模型时，只禁用按钮会留下一个选中却不可改的死态：调用方仍
  // 按 use_tts 提交、生成按钮照常可点，请求每次都被后端拒。交付方式是请求局部状态，因此把
  // 选择纠正回后期配音，让提交值、报价显示与界面呈现同时收敛到唯一真相。
  const blockedSelection = ttsDurationEndpointFixed && value === "use_tts";
  useEffect(() => {
    if (blockedSelection) onChange("post_production");
  }, [blockedSelection, onChange]);
  return (
    <div
      role="group"
      aria-label={t("narration_delivery_label")}
      className={`inline-flex items-center gap-2 ${compact ? "text-[11.5px]" : "text-xs"}`}
    >
      <span className="text-[var(--color-text-3)]">{t("narration_delivery_label")}</span>
      <span className="inline-flex overflow-hidden rounded-md border border-[var(--color-hairline)]">
        {(["post_production", "use_tts"] as const).map((choice) => {
          const blocked = choice === "use_tts" && ttsDurationEndpointFixed;
          return (
            <button
              key={choice}
              type="button"
              aria-pressed={value === choice}
              disabled={disabled || blocked}
              title={blocked ? (notice ?? undefined) : undefined}
              onClick={() => onChange(choice)}
              className={`focus-ring px-2 py-1 transition-colors disabled:cursor-not-allowed disabled:opacity-60 ${
                value === choice
                  ? "bg-[var(--color-accent-dim)] text-[var(--color-accent-2)]"
                  : "bg-[oklch(0.22_0.011_265_/_0.7)] text-[var(--color-text-3)]"
              }`}
            >
              {t(
                choice === "use_tts"
                  ? "narration_delivery_use_tts"
                  : "narration_delivery_post_production",
              )}
            </button>
          );
        })}
      </span>
      {notice && <span className="text-[var(--color-text-4)]">{notice}</span>}
    </div>
  );
}
