import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { OverviewCanvas } from "./OverviewCanvas";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import type { ProjectData } from "@/types";

vi.mock("./WelcomeCanvas", () => ({
  WelcomeCanvas: () => <div data-testid="welcome-canvas">welcome</div>,
}));

vi.mock("./AdInitCanvas", () => ({
  AdInitCanvas: () => <div data-testid="ad-init-canvas">ad-init</div>,
}));

function makeProjectData(overrides: Partial<ProjectData> = {}): ProjectData {
  return {
    title: "Demo",
    content_mode: "narration",
    style: "Anime",
    style_description: "old description",
    overview: {
      synopsis: "summary",
      genre: "fantasy",
      theme: "growth",
      world_setting: "palace",
    },
    episodes: [{ episode: 1, title: "EP1", script_file: "scripts/episode_1.json" }],
    characters: {},
    scenes: {},
    props: {},
    ...overrides,
  };
}

describe("OverviewCanvas", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    vi.restoreAllMocks();
    vi.stubGlobal("confirm", vi.fn(() => true));
  });

  it("renders the project title and content mode", () => {
    render(<OverviewCanvas projectName="demo" projectData={makeProjectData()} />);
    expect(screen.getByText("Demo")).toBeInTheDocument();
  });

  it("shows welcome canvas when there is no overview and no episodes", () => {
    render(
      <OverviewCanvas
        projectName="demo"
        projectData={makeProjectData({ overview: undefined, episodes: [] })}
      />,
    );
    expect(screen.getByTestId("welcome-canvas")).toBeInTheDocument();
  });

  it("regenerates overview on button click", async () => {
    vi.spyOn(API, "generateOverview").mockResolvedValue(undefined as never);
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: {},
    });

    render(<OverviewCanvas projectName="demo" projectData={makeProjectData()} />);

    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));
    await waitFor(() => {
      expect(API.generateOverview).toHaveBeenCalledWith("demo");
    });
  }, 10_000);

  it("edits the four overview fields and saves via API.updateOverview", async () => {
    vi.spyOn(API, "updateOverview").mockResolvedValue(undefined as never);
    vi.spyOn(API, "getProject").mockResolvedValue({
      project: makeProjectData(),
      scripts: {},
    });

    render(<OverviewCanvas projectName="demo" projectData={makeProjectData()} />);

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText("故事梗概"), { target: { value: "新梗概" } });
    fireEvent.change(screen.getByLabelText("世界观设定"), { target: { value: "新世界观" } });
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    await waitFor(() => {
      expect(API.updateOverview).toHaveBeenCalledWith(
        "demo",
        expect.objectContaining({ synopsis: "新梗概", world_setting: "新世界观" }),
      );
    });
  });

  it("reverts overview edits on cancel", () => {
    render(<OverviewCanvas projectName="demo" projectData={makeProjectData()} />);

    fireEvent.click(screen.getByRole("button", { name: "编辑" }));
    fireEvent.change(screen.getByLabelText("故事梗概"), { target: { value: "临时改动" } });
    fireEvent.click(screen.getByRole("button", { name: "取消" }));

    // 退出编辑：表单消失，显示原 synopsis 文本
    expect(screen.queryByLabelText("故事梗概")).toBeNull();
    expect(screen.getByText("summary")).toBeInTheDocument();
  });

  it("offers a create-overview entry when overview is absent but episodes exist", () => {
    render(
      <OverviewCanvas
        projectName="demo"
        projectData={makeProjectData({ overview: undefined })}
      />,
    );
    expect(screen.getByRole("button", { name: "创建概述" })).toBeInTheDocument();
  });
});

describe("OverviewCanvas ad mode", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    vi.restoreAllMocks();
  });

  it("hides episode semantics for ad projects", () => {
    render(
      <OverviewCanvas
        projectName="ad-demo"
        projectData={makeProjectData({
          content_mode: "ad",
          target_duration: 60,
          brief: "卖点",
          episodes: [{ episode: 1, title: "", script_file: "scripts/episode_1.json" }],
        })}
      />,
    );
    // 不出现「集」概念：无 E1 徽标、无「剧集」标题
    expect(screen.queryByText("E1")).not.toBeInTheDocument();
    expect(screen.queryByText("剧集")).not.toBeInTheDocument();
    // 改为「视频」区块标题
    expect(screen.getByText("视频")).toBeInTheDocument();
  });

  it("keeps episode semantics for narration projects", () => {
    render(<OverviewCanvas projectName="demo" projectData={makeProjectData()} />);
    expect(screen.getByText("E1")).toBeInTheDocument();
  });

  it("shows ad init canvas when ad project has no products and no brief", () => {
    render(
      <OverviewCanvas
        projectName="ad-demo"
        projectData={makeProjectData({
          content_mode: "ad",
          overview: undefined,
          target_duration: 60,
          brief: "",
          products: {},
          episodes: [{ episode: 1, title: "", script_file: "scripts/episode_1.json" }],
        })}
      />,
    );
    expect(screen.getByTestId("ad-init-canvas")).toBeInTheDocument();
  });

  it("skips ad init canvas once brief or products exist", () => {
    render(
      <OverviewCanvas
        projectName="ad-demo"
        projectData={makeProjectData({
          content_mode: "ad",
          target_duration: 60,
          brief: "卖点",
          episodes: [{ episode: 1, title: "", script_file: "scripts/episode_1.json" }],
        })}
      />,
    );
    expect(screen.queryByTestId("ad-init-canvas")).not.toBeInTheDocument();
  });

  it("never shows ad init canvas for narration projects", () => {
    render(
      <OverviewCanvas
        projectName="demo"
        projectData={makeProjectData({ overview: undefined, episodes: [] })}
      />,
    );
    expect(screen.queryByTestId("ad-init-canvas")).not.toBeInTheDocument();
    expect(screen.getByTestId("welcome-canvas")).toBeInTheDocument();
  });
});
