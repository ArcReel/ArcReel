import { render } from "@testing-library/react";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";

import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { useUsageHeaderStore } from "@/stores/usage-header-store";
import { UsageHeaderEntry } from "./UsageHeaderEntry";

export const HEADER_PROJECT = "星海列车";

/** 每个用例前把顶栏入口读到的四个 store 复位。 */
export function resetHeaderStores(): void {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useProjectsStore.setState(useProjectsStore.getInitialState(), true);
  useTasksStore.setState(useTasksStore.getInitialState(), true);
  useUsageHeaderStore.setState(useUsageHeaderStore.getInitialState(), true);
}

export function renderUsageHeaderEntry(projectName = HEADER_PROJECT) {
  const location = memoryLocation({ path: "/characters", record: true });
  return {
    ...render(
      <Router hook={location.hook} searchHook={location.searchHook}>
        <UsageHeaderEntry projectName={projectName} />
      </Router>,
    ),
    location,
  };
}
