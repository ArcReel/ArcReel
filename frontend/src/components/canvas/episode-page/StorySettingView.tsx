import { useTranslation } from "react-i18next";

import { StorySetting } from "@/components/canvas/overview/StorySetting";
import { EditUnitRetentionContext } from "@/components/shared/edit-unit/RetainedEditUnit";
import type { ProjectOverview } from "@/types";

import { useStaysInEpisodeView } from "./EpisodeViewScope";

const NO_GENERATE = () => {};

/**
 * 广告项目视频页的「故事设定」视图：梗概、类型、主题与世界观是 AI 生成脚本的输入，在这里作为一个编辑单元修改，
 * 保存后已生成的脚本由服务端按产物过期规则标记。广告项目没有原文，不提供「从原文生成」。
 * 视图不往页头放动作，其他视图的批量动作在这里随之隐藏。
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
        {/* 故事设定不随这一集的脚本变化：脚本生成或删除时不必为它保留集页的旧内容 */}
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
