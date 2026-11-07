import { useTranslation } from "react-i18next";

import { StorySetting } from "@/components/canvas/overview/StorySetting";
import { EditUnitRetentionContext } from "@/components/shared/edit-unit/edit-unit-retention";
import type { ProjectOverview } from "@/types";

import { useStaysInEpisodeView } from "./EpisodeViewScope";

const NO_GENERATE = () => {};

/**
 * Story-settings view on ad-project video pages: synopsis, genre, theme and worldbuilding form one editing unit and feed AI script generation.
 * After saving, the server marks existing scripts according to artifact-staleness rules. Ad projects have no source text and offer no generation from source.
 * This view adds no header actions, hiding other views' batch actions here.
 */
export function StorySettingView({
  projectName,
  overview,
  readOnly,
}: {
  projectName: string;
  overview: ProjectOverview | undefined;
  readOnly: boolean;
}) {
  const { t } = useTranslation("dashboard");
  const allowNavigation = useStaysInEpisodeView();
  return (
    <div className="relative flex min-h-0 flex-1 flex-col overflow-y-auto p-6 [scrollbar-gutter:stable] @3xl/canvas:p-8">
      <div className="flex w-full max-w-190 shrink-0 flex-col">
        {/* Story settings do not change with this episode's script; script generation/deletion need not retain old episode-page content for them */}
        <EditUnitRetentionContext.Provider value={null}>
          <StorySetting
            key={projectName}
            projectName={projectName}
            overview={overview}
            readOnly={readOnly}
            canGenerate={false}
            generating={false}
            generateError={null}
            onGenerate={NO_GENERATE}
            description={readOnly ? undefined : t("ad_setting_hint")}
            allowNavigation={allowNavigation}
          />
        </EditUnitRetentionContext.Provider>
      </div>
    </div>
  );
}
