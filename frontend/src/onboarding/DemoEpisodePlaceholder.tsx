import { useTranslation } from "react-i18next";
import { Empty, EmptyDescription, EmptyHeader } from "@/components/ui/empty";
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
    <Empty className="min-h-0 flex-1">
      <EmptyHeader>
        {/* 演示账本按集 ID 升序排列，集 ID 即播出位置。 */}
        <EmptyDescription>{t("demo_episode_placeholder", { position: DEMO_SCRIPTED_EPISODE })}</EmptyDescription>
      </EmptyHeader>
    </Empty>
  );
}
