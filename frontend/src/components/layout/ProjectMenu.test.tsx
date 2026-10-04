import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { Router } from "wouter";
import { memoryLocation } from "wouter/memory-location";
import { API } from "@/api";
import { DEMO_PROJECT_NAME } from "@/onboarding/demo-project";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectSummary } from "@/types";
import { ProjectMenu } from "./ProjectMenu";

function summary(name: string, title: string): ProjectSummary {
  return { name, title, style: "", thumbnail: null, status: {}, last_activity_at: null };
}

function renderMenu() {
  const location = memoryLocation({ path: "/characters", record: true });
  render(
    <Router hook={location.hook}>
      <ProjectMenu />
    </Router>,
  );
  return location;
}

async function openSwitcher() {
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /切换项目/ }));
  return user;
}

describe("ProjectMenu", () => {
  beforeEach(() => {
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useProjectsStore.setState({
      currentProjectName: "alpha",
      currentProjectData: { title: "春日来信", content_mode: "drama", style: "", episodes: [], characters: {}, scenes: {}, props: {} },
    });
    vi.restoreAllMocks();
    vi.spyOn(API, "listProjects").mockResolvedValue({
      projects: [summary("alpha", "春日来信"), summary("beta", "夏夜追凶"), summary("gamma", "秋风集")],
    });
  });

  it("searches all projects by title and switches on selection", async () => {
    const location = renderMenu();
    const user = await openSwitcher();

    await screen.findByRole("option", { name: "秋风集" });
    await user.keyboard("夏夜");
    await waitFor(() => expect(screen.queryByRole("option", { name: "秋风集" })).not.toBeInTheDocument());
    await user.keyboard("{Enter}");

    await waitFor(() => expect(location.history?.at(-1)).toBe("/app/projects/beta"));
  });

  it("says so when no project matches", async () => {
    renderMenu();
    const user = await openSwitcher();
    await screen.findByRole("option", { name: "秋风集" });

    await user.keyboard("不存在的项目");

    expect(await screen.findByText("没有匹配的项目")).toBeInTheDocument();
    // 底部入口不随搜索词过滤
    expect(screen.getByRole("link", { name: "全部项目" })).toBeInTheDocument();
  });

  it("opens project settings, and the create dialog in the lobby, from the footer", async () => {
    const location = renderMenu();
    let user = await openSwitcher();
    await user.click(await screen.findByRole("link", { name: "项目设置" }));
    expect(location.history?.at(-1)).toBe("/app/projects/alpha/settings");

    user = await openSwitcher();
    await user.click(await screen.findByRole("button", { name: "新建项目…" }));
    expect(location.history?.at(-1)).toBe("/app/projects");
    expect(useProjectsStore.getState().showCreateModal).toBe(true);
  });

  it("has no project settings entry in the demo project", async () => {
    useProjectsStore.setState({ currentProjectName: DEMO_PROJECT_NAME });
    renderMenu();
    await openSwitcher();

    expect(await screen.findByRole("link", { name: "全部项目" })).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "项目设置" })).not.toBeInTheDocument();
  });
});
