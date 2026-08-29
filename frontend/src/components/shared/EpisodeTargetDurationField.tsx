import { useId } from "react";
import { useTranslation } from "react-i18next";
import { FieldLabel } from "@/components/ui/FieldLabel";

/**
 * 单集目标时长（秒）的项目级可选输入。
 *
 * 与「默认时长」是两个尺度：那个是单个分镜 / 视频单元的默认秒数（受视频模型档位约束，
 * 落在模型配置栏内），这个是整集成片的期望体量，驱动脚本规划决定本集拆多少个单元。
 * 两者同为软偏好，可被内容需要覆盖；超出目标只提示、不阻断确认与后续生成。
 *
 * 广告/短片项目不呈现该输入：整集体量已由目标总时长（target_duration）预算表达，
 * 两套总量口径并存会互相竞争。
 */

/** 硬区间（闭区间，秒）：与后端 lib.episode_target_duration 的 is_valid_episode_target_duration 同一把尺。 */
const EPISODE_TARGET_DURATION_MIN = 10;
const EPISODE_TARGET_DURATION_MAX = 600;

/** 该值是否可提交（null = 未设目标，合法）。 */
export function isValidEpisodeTargetDuration(value: number | null): boolean {
  if (value === null) return true;
  return (
    Number.isInteger(value) &&
    value >= EPISODE_TARGET_DURATION_MIN &&
    value <= EPISODE_TARGET_DURATION_MAX
  );
}

export interface EpisodeTargetDurationFieldProps {
  value: number | null;
  onChange: (next: number | null) => void;
}

export function EpisodeTargetDurationField({ value, onChange }: EpisodeTargetDurationFieldProps) {
  const { t } = useTranslation("dashboard");
  const id = `${useId()}-episode-target-duration`;
  const errorId = `${id}-error`;
  const invalid = !isValidEpisodeTargetDuration(value);

  return (
    <div>
      <FieldLabel htmlFor={id}>{t("episode_target_duration_label")}</FieldLabel>
      <div className="flex items-center gap-2">
        <input
          id={id}
          type="number"
          inputMode="numeric"
          // 原生约束不比 isValidEpisodeTargetDuration 更严，否则同一个值会同时呈现自定义有效
          // 与浏览器无效两种状态：min 取 0（真实下界由校验函数判），step=1 表达整数量纲。
          min={0}
          max={EPISODE_TARGET_DURATION_MAX}
          step={1}
          value={value ?? ""}
          aria-invalid={invalid || undefined}
          aria-describedby={invalid ? errorId : undefined}
          onChange={(e) => {
            const raw = e.target.value;
            if (raw === "") {
              onChange(null);
              return;
            }
            const next = Number(raw);
            // 只挡非有限数（NaN / Infinity 会被序列化成 null，误触「清除」语义）；
            // 区间与整数校验交给下面的行内提示与后端，输入过程中不吞用户的按键
            if (Number.isFinite(next)) onChange(next);
          }}
          className="w-28 rounded-[8px] border border-hairline bg-bg-grad-a/55 px-3 py-2 text-[12.5px] text-text focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-accent"
        />
        <span className="font-mono text-[10.5px] uppercase tracking-[0.12em] text-text-3">
          {t("episode_target_duration_unit")}
        </span>
      </div>
      {invalid ? (
        <p id={errorId} role="alert" className="mt-1 text-[11px] text-warm-bright">
          {t("episode_target_duration_out_of_range", {
            min: EPISODE_TARGET_DURATION_MIN,
            max: EPISODE_TARGET_DURATION_MAX,
          })}
        </p>
      ) : (
        <p className="mt-1 text-[11px] text-text-4">{t("episode_target_duration_hint")}</p>
      )}
    </div>
  );
}
