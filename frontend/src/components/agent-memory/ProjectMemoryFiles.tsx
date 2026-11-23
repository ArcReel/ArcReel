import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { projectSettingsPath } from "@/app-routes";
import { SecondaryRailList } from "@/components/shared/master-detail/SecondaryRail";
import type { AgentMemoryScope } from "@/types/agent-memory";

import { MemoryDetail, MemoryLoadState, useMemoryWorkspace } from "./MemoryWorkspace";
import { MEMORY_FILE_PARAM } from "./memory-files";

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
      <div className="sticky top-6 min-w-0 flex-1">
        <MemoryDetail workspace={workspace} layout="flow" />
      </div>
    </div>
  );
}
