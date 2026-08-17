import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it } from "vitest";

import { MigrationRepairBanner } from "./MigrationRepairBanner";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData, ProjectStatus } from "@/types/project";

const HEALTHY: ProjectStatus = {
  current_phase: "production",
  phase_progress: 0.5,
  needs_repair: false,
  repair_reason: null,
  characters: { total: 1, completed: 1 },
  scenes: { total: 1, completed: 1 },
  props: { total: 0, completed: 0 },
  episodes_summary: { total: 1, scripted: 1, in_production: 1, completed: 0 },
};

function setProjectStatus(status: ProjectStatus) {
  useProjectsStore.setState({
    currentProjectData: { name: "demo", status } as unknown as ProjectData,
  });
}

describe("MigrationRepairBanner", () => {
  beforeEach(() => {
    useProjectsStore.setState({ currentProjectData: null });
    useAssistantStore.setState({ input: "" });
    useAppStore.setState({ assistantPanelOpen: false });
  });

  it("stays out of the way while the project is healthy", () => {
    setProjectStatus(HEALTHY);
    render(<MigrationRepairBanner />);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("shows the raw failure reason when the project needs repair", () => {
    setProjectStatus({
      ...HEALTHY,
      needs_repair: true,
      repair_reason: "episode script scripts/episode_1.json item 2 has no identity",
    });
    render(<MigrationRepairBanner />);
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(
      screen.getByText("episode script scripts/episode_1.json item 2 has no identity"),
    ).toBeInTheDocument();
  });

  it("prefills the assistant input without sending, and opens the panel", async () => {
    setProjectStatus({ ...HEALTHY, needs_repair: true, repair_reason: "boom" });
    render(<MigrationRepairBanner />);

    await userEvent.click(screen.getByRole("button"));

    expect(useAssistantStore.getState().input).not.toBe("");
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
  });
});
