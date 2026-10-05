/**
 * 引导期间大厅里的「示例项目」区块（第 6 步的锚点在演示卡上）。
 *
 * 用的是大厅真实的 `ProjectCard`，只切到只读形态：「项目推进后长这样」这句话只有在演示卡与
 * 真实卡片是同一份实现时才不会随时间说谎。区块放在项目网格或空状态之上，标题旁注明只在引导
 * 期间显示，讲清它不是用户自己的项目。
 *
 * 只在引导运行期间挂载：调用方按 store 的 `active` 决定渲染与否，退出时只卸载这个区块，
 * 下方的网格与空状态不受影响。
 */

import { useId, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { ProjectCard } from "@/components/pages/lobby/ProjectCard";
import { ONBOARDING_ANCHORS } from "./anchors";
import { buildDemoProject } from "./demo-project";

export function OnboardingDemoCard() {
  const { t } = useTranslation("onboarding");
  const headingId = useId();

  const project = useMemo(() => buildDemoProject(t), [t]);

  return (
    <section className="flex flex-col gap-3 pb-6" aria-labelledby={headingId}>
      <div className="flex items-baseline gap-2">
        <h2 id={headingId} className="text-sm font-medium">
          {t("demo_section_title")}
        </h2>
        <span className="text-xs text-muted-foreground">{t("demo_section_note")}</span>
      </div>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(280px,1fr))] gap-4">
        <div data-onboarding={ONBOARDING_ANCHORS.lobbyDemoCard} className="flex min-w-0 flex-col">
          <ProjectCard project={project} readOnly />
        </div>
      </div>
    </section>
  );
}
