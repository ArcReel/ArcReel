import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { projectSettingsPath } from "@/app-routes";
import { SecondaryRailList } from "@/components/shared/master-detail/SecondaryRail";
import type { AgentMemoryScope } from "@/types/agent-memory";

import { MemoryDetail, MemoryLoadState, useMemoryWorkspace } from "./MemoryWorkspace";
import { MEMORY_FILE_PARAM } from "./memory-files";

/**
 * File area in the project-settings memory tab: a width-limited page cannot fit a secondary sidebar. The left file list has no independent scroll; the right uses the same editor.
 * The page body scrolls and the editor sticks to its top. The editor is its own editing unit and does not use the shell footer save bar.
 */
export function ProjectMemoryFiles({ projectName }: { projectName: string }) {
  const { t } = useTranslation("dashboard");
  const scope = useMemo<AgentMemoryScope>(() => ({ level: "project", projectName }), [projectName]);
  const hrefFor = useCallback(
    (file: string) => projectSettingsPath(projectName, "memory", { [MEMORY_FILE_PARAM]: file }),
    [projectName],
  );
  const workspace = useMemoryWorkspace(scope, hrefFor);
  const loadState = <MemoryLoadState workspace={workspace} />;
  if (workspace.error !== null || workspace.overview === null) return loadState;

  return (
    <div className="flex items-start gap-6">
      <nav aria-label={t("agent_memory_files")} className="w-52 shrink-0">
        <SecondaryRailList group={workspace.group} activeId={workspace.activeId} replace />
      </nav>
      {/* Long file lists extend the page; the editor sticks to the body top and stays visible when selecting files near the list end */}
      <div className="sticky top-6 min-w-0 flex-1">
        <MemoryDetail workspace={workspace} layout="flow" />
      </div>
    </div>
  );
}
