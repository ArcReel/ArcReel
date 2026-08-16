import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { NarrationDelivery, WorkflowNarrationDeliveryChoice } from "@/types/workflow";
import { ProblemList } from "./ProblemList";
import { ARTIFACT_TONES } from "./state-language";
import type { ProblemView } from "./problem-views";

interface Props {
  choice: WorkflowNarrationDeliveryChoice;
  /**
   * TTS 不可用的原因。非空时「使用当前 TTS」不可选，界面把后期配音摆成可走的那条路，
   * 而不是把这一步标红——旁白有两条交付路径，其中一条没配好不等于工作流停摆。
   */
  ttsUnavailable?: ProblemView | null;
  onSelect: (delivery: NarrationDelivery) => void;
  busy?: boolean;
}

/**
 * 本次视频请求的旁白交付方式。
 *
 * 后端把这个选择标为 `persisted: false`：它只作用于这一次生成，不写回项目设置。文案照此
 * 陈述，避免用户以为自己在改一个长期开关。选择做出之前后端不会给出准入结论，所以这里是
 * 视频步骤真正的入口，不是一个可跳过的偏好项。
 */
export function NarrationDeliveryChoice({ choice, ttsUnavailable, onSelect, busy }: Props) {
  const { t } = useTranslation("workflow");
  const groupId = useId();
  const noticeId = useId();
  const ttsBlocked = Boolean(ttsUnavailable);

  return (
    <fieldset className="space-y-1.5" disabled={busy}>
      <legend className="text-[12px] font-medium" style={{ color: "var(--color-text-2)" }}>
        {t("delivery_title")}
      </legend>
      <p className="text-[11.5px] leading-relaxed" style={{ color: "var(--color-text-3)" }}>
        {t("delivery_this_request_only")}
      </p>
      <div className="flex flex-col gap-1.5">
        {choice.options.map((option) => {
          const disabled = option === "use_tts" && ttsBlocked;
          const hintId = `${groupId}-${option}-hint`;
          return (
            <div key={option}>
              <label
                className="flex items-center gap-2 text-[12px]"
                style={{ color: disabled ? "var(--color-text-4)" : "var(--color-text-2)" }}
              >
                <input
                  type="radio"
                  name={groupId}
                  value={option}
                  checked={choice.selected === option}
                  disabled={disabled}
                  aria-describedby={disabled ? `${hintId} ${noticeId}` : hintId}
                  onChange={() => onSelect(option)}
                />
                {t(`delivery_${option}` as const)}
              </label>
              <p
                id={hintId}
                className="ml-5 text-[11.5px] leading-relaxed"
                style={{ color: "var(--color-text-3)" }}
              >
                {t(`delivery_${option}_hint` as const)}
              </p>
            </div>
          );
        })}
      </div>
      {ttsUnavailable && (
        <div
          id={noticeId}
          className="rounded-lg px-2.5 py-1.5 text-[11.5px]"
          style={{
            background: ARTIFACT_TONES.stale.soft,
            border: `1px solid ${ARTIFACT_TONES.stale.ring}`,
          }}
        >
          <p style={{ color: ARTIFACT_TONES.stale.color }}>{t("delivery_tts_unavailable")}</p>
          <ProblemList problems={[ttsUnavailable]} className="mt-1 space-y-1" />
        </div>
      )}
    </fieldset>
  );
}
