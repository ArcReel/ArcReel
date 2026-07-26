import { useTranslation } from "react-i18next";
import { DEMO_SCRIPTED_EPISODE } from "./demo-project";

/**
 * 演示项目里没有剧本的那几集。
 *
 * 演示只把第 1 集做完整，其余分集只有标题。真实项目在这个位置会进源文切片审阅，但演示
 * 没有源文可切，所以直接说明情况并指回第 1 集，而不是让人对着一个空画布猜。
 */
export function DemoEpisodePlaceholder() {
  const { t } = useTranslation("onboarding");

  return (
    <div className="flex h-full items-center justify-center px-6">
      <p className="max-w-sm text-center text-[13px] leading-relaxed text-text-3">
        {t("demo_episode_placeholder", { episode: DEMO_SCRIPTED_EPISODE })}
      </p>
    </div>
  );
}
