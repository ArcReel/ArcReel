/**
 * 引导期间注入大厅的演示卡（大厅第 3 步的锚点）。
 *
 * 用的是大厅真实的 `ProjectCard`，只切到只读形态 —— 「项目推进后长这样」这句话只有在
 * 演示卡与真实卡片是同一份实现时才不会随时间说谎。上方一行 eyebrow 与大厅其它分区同款，
 * 讲清它是演示、不是用户的项目。
 *
 * 只在引导运行期间挂载：调用方按 store 的 `active` 决定渲染与否，退出即卸载，不留痕迹。
 */

import { useMemo } from "react";
import { useTranslation } from "react-i18next";
import { ProjectCard } from "@/components/pages/ProjectCard";
import type { Phase } from "@/types";
import { ONBOARDING_ANCHORS } from "./anchors";
import { buildDemoProject } from "./demo-project";

export function OnboardingDemoCard() {
  const { t } = useTranslation("onboarding");
  const { t: tDashboard, i18n } = useTranslation("dashboard");

  const project = useMemo(() => buildDemoProject(t), [t]);
  const phaseLabels = useMemo<Record<Phase, string>>(
    () => ({
      setup: tDashboard("phase_setup"),
      worldbuilding: tDashboard("phase_worldbuilding"),
      scripting: tDashboard("phase_scripting"),
      production: tDashboard("phase_production"),
      completed: tDashboard("phase_completed"),
    }),
    // eslint-disable-next-line react-hooks/exhaustive-deps -- t reference rotates with i18n.language
    [i18n.language],
  );

  return (
    <section className="mb-7" aria-labelledby="onboarding-demo-heading">
      <div className="mb-3 flex items-baseline justify-between gap-3">
        <h2
          id="onboarding-demo-heading"
          className="m-0 font-mono text-[12.5px] font-semibold uppercase tracking-[0.06em] text-accent-2"
        >
          {t("demo_section_eyebrow")}
        </h2>
        <span className="font-mono text-[10.5px] text-text-3">{t("demo_section_note")}</span>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        <div data-onboarding={ONBOARDING_ANCHORS.lobbyDemoCard}>
          <ProjectCard
            project={project}
            styleLabel={t("demo_project_style")}
            phaseLabels={phaseLabels}
            t={tDashboard}
            readOnly
          />
        </div>
      </div>
    </section>
  );
}
