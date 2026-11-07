/**
 * Lobby Example projects during onboarding, with step-6 anchors on demo cards.
 *
 * Use real lobby `ProjectCard` readonly: demonstrating advanced-project appearances stays truthful over time
 * only through shared implementations. Place sections above grids/empty states, noting beside titles that they appear only during
 * onboarding, clarifying these are not users' projects.
 *
 * Mount only while onboarding runs: callers use store `active`; exit unmounts this section alone,
 * preserving underlying grids/empty states.
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
