import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MigrationRepairBanner } from "./MigrationRepairBanner";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData, ProjectStatus } from "@/types/project";

const HEALTHY: ProjectStatus = {
  phase: "production",
  phase_progress: 0.5,
  needs_repair: false,
  repair_reason: null,
  assets: {
    character: { total: 1, available: 1, stale: 0 },
    scene: { total: 1, available: 1, stale: 0 },
    prop: { total: 0, available: 0, stale: 0 },
  },
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
    useAssistantStore.setState({ input: "", sending: false });
    useAppStore.setState({ assistantPanelOpen: false });
  });

  afterEach(() => {
    vi.restoreAllMocks();
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
    // 发送走 useAssistantSession 的网络调用，条幅不该碰它：spy 住 fetch 才能把
    // 「只预填」与「预填并发出」区分开——只断言 input 非空的话两者都会通过
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<MigrationRepairBanner />);

    await userEvent.click(screen.getByRole("button"));

    expect(useAssistantStore.getState().input).not.toBe("");
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
    expect(fetchSpy).not.toHaveBeenCalled();
    expect(useAssistantStore.getState().sending).toBe(false);
  });
});
