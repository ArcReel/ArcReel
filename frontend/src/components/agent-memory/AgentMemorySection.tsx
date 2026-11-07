import { useTranslation } from "react-i18next";

import { settingsSectionPath } from "@/app-routes";
import { SecondaryRail } from "@/components/shared/master-detail/SecondaryRail";
import type { AgentMemoryScope } from "@/types/agent-memory";

import { MemoryDetail, MemoryLoadState, useMemoryWorkspace } from "./MemoryWorkspace";
import { MEMORY_FILE_PARAM } from "./memory-files";

const USER_SCOPE: AgentMemoryScope = { level: "user" };

function userMemoryHref(file: string): string {
  return settingsSectionPath("agent-memory", { [MEMORY_FILE_PARAM]: file });
}

/** Global Agent memory settings (user memory): full-bleed section with files in the secondary column and an editor in the detail column. */
export function AgentMemorySection() {
  const { t } = useTranslation("dashboard");
  const workspace = useMemoryWorkspace(USER_SCOPE, userMemoryHref);
  const loadState = <MemoryLoadState workspace={workspace} className="p-6" />;
  if (workspace.error !== null || workspace.overview === null) return loadState;

  return (
    // Full-bleed layout: secondary and detail columns scroll independently
    <div className="flex min-h-0 min-w-0 flex-1">
      <SecondaryRail
        label={t("agent_memory_files")}
        groups={[workspace.group]}
        activeId={workspace.activeId}
        replace
      />
      <MemoryDetail workspace={workspace} layout="pane" />
    </div>
  );
}
