import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { API, ConflictError } from "@/api";
import { OverviewCanvas } from "./OverviewCanvas";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useCostStore } from "@/stores/cost-store";
import type { ProjectData } from "@/types";

vi.mock("./WelcomeCanvas", () => ({
  WelcomeCanvas: ({ onUpload }: { onUpload: (file: File) => void }) => (
    <button data-testid="welcome-canvas" onClick={() => onUpload(new File(["x"], "source.txt"))}>
      welcome
    </button>
  ),
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
    useCostStore.setState(useCostStore.getInitialState(), true);
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

  it("dismisses a pending conflict prompt once the canvas turns read-only", async () => {
    vi.spyOn(API, "uploadFile").mockRejectedValue(
      new ConflictError("existing.txt", "existing (1).txt", "conflict"),
    );

    const { rerender } = render(
      <OverviewCanvas
        projectName="demo"
        projectData={makeProjectData({ overview: undefined, episodes: [] })}
      />,
    );

    fireEvent.click(screen.getByTestId("welcome-canvas"));
    expect(await screen.findByText("同名文件已存在")).toBeInTheDocument();

    // 切到只读态（如工作台切到演示项目复用同一路由实例）——悬挂的冲突弹窗须一并清空，
    // 不能带着「保留两者/替换」这类写操作继续留在只读页面上。
    rerender(
      <OverviewCanvas
        projectName="demo"
        projectData={makeProjectData({ overview: undefined, episodes: [] })}
        readOnly
      />,
    );

    expect(screen.queryByText("同名文件已存在")).not.toBeInTheDocument();
  });

  it("does not trigger the agent handoff prompt when switching to a read-only project", () => {
    useAppStore.setState({ assistantPanelOpen: false });

    // 真实项目停在欢迎页（wasWelcomeRef 记为 true）
    const { rerender } = render(
      <OverviewCanvas
        projectName="demo"
        projectData={makeProjectData({ overview: undefined, episodes: [] })}
      />,
    );

    // 切到只读态（如工作台切到演示项目复用同一路由实例）——演示数据自带 overview/episodes，
    // 之前会被误判成「欢迎页 → 完成」触发交接提示，强行打开演示态并不挂载的助手面板。
    rerender(
      <OverviewCanvas
        projectName="demo"
        projectData={makeProjectData()}
        readOnly
      />,
    );

    expect(useAppStore.getState().assistantPanelOpen).toBe(false);
  });

  it("does not replay a stale handoff trigger after switching to a read-only demo project", () => {
    useAppStore.setState({ assistantPanelOpen: false });

    // 真实项目内先完成一次「欢迎页 → 完成」，使 handoffTrigger 变为非零并已消费过一次
    const { rerender } = render(
      <OverviewCanvas
        projectName="real-project"
        projectData={makeProjectData({ overview: undefined, episodes: [] })}
      />,
    );
    rerender(
      <OverviewCanvas projectName="real-project" projectData={makeProjectData()} />,
    );
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);

    // 复位后再切到只读的演示项目——storageScope 变化不应让残留的非零 trigger
    // 被 AgentHandoffHint 当成新事件重新触发一次
    useAppStore.setState({ assistantPanelOpen: false });
    rerender(
      <OverviewCanvas
        projectName="onboarding_demo"
        projectData={makeProjectData()}
        readOnly
      />,
    );

    expect(useAppStore.getState().assistantPanelOpen).toBe(false);
  });

  it("does not reopen the conflict prompt if a stale upload resolves after switching to read-only", async () => {
    let rejectUpload: ((err: unknown) => void) | undefined;
    vi.spyOn(API, "uploadFile").mockImplementation(
      () =>
        new Promise((_resolve, reject) => {
          rejectUpload = reject;
        }),
    );

    const { rerender } = render(
      <OverviewCanvas
        projectName="real-project"
        projectData={makeProjectData({ overview: undefined, episodes: [] })}
      />,
    );

    fireEvent.click(screen.getByTestId("welcome-canvas"));

    // 上传仍在途时切到只读态（如导航到演示项目复用同一路由实例）
    rerender(
      <OverviewCanvas
        projectName="onboarding_demo"
        projectData={makeProjectData()}
        readOnly
      />,
    );

    // 真实项目的旧上传这时才返回冲突——不该在只读页面上重新弹出弹窗
    rejectUpload?.(new ConflictError("existing.txt", "existing (1).txt", "conflict"));

    await waitFor(() => {
      expect(screen.queryByText("同名文件已存在")).not.toBeInTheDocument();
    });
  });

  it("does not push a stale success toast if a slow upload resolves after switching to read-only", async () => {
    let resolveUpload: ((res: Awaited<ReturnType<typeof API.uploadFile>>) => void) | undefined;
    vi.spyOn(API, "uploadFile").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveUpload = resolve;
        }),
    );
    const pushToastSpy = vi.spyOn(useAppStore.getState(), "pushToast");

    const { rerender } = render(
      <OverviewCanvas
        projectName="real-project"
        projectData={makeProjectData({ overview: undefined, episodes: [] })}
      />,
    );

    fireEvent.click(screen.getByTestId("welcome-canvas"));
    await waitFor(() => expect(resolveUpload).toBeDefined());

    // 上传仍在途时切到只读态（如导航到演示项目复用同一路由实例）
    rerender(
      <OverviewCanvas
        projectName="onboarding_demo"
        projectData={makeProjectData()}
        readOnly
      />,
    );

    // 真实项目的旧上传这时才成功返回——不该在只读页面上展示过期的成功提示
    resolveUpload?.({ success: true, path: "source.txt", url: "/source.txt", filename: "source.txt" });

    await waitFor(() => {
      expect(pushToastSpy).not.toHaveBeenCalled();
    });
  });

  it("does not fetch or display cost data on a read-only project", () => {
    const getCostEstimateSpy = vi.spyOn(API, "getCostEstimate");
    // 模拟仍带着上一个真实项目的费用残留（如同一路由实例复用时的短暂窗口）——
    // 只读态下即便 store 里有数据也不该展示
    useCostStore.setState({
      costData: {
        project_name: "real-project",
        models: { image: { provider: "p", model: "m" }, video: { provider: "p", model: "m" } },
        episodes: [],
        project_totals: {
          estimate: { image: { usd: 1 } },
          actual: { image: { usd: 1 } },
        },
      },
    });

    render(
      <OverviewCanvas
        projectName="onboarding_demo"
        projectData={makeProjectData()}
        readOnly
      />,
    );

    expect(screen.queryByText("项目总费用")).not.toBeInTheDocument();
    expect(getCostEstimateSpy).not.toHaveBeenCalled();
  });

  it("cancels a real project's queued cost request when switching to the read-only demo project", async () => {
    vi.useFakeTimers();
    const getCostEstimateSpy = vi.spyOn(API, "getCostEstimate");
    try {
      const { rerender } = render(
        <OverviewCanvas projectName="real-project" projectData={makeProjectData()} />,
      );

      // 切到只读演示项目——此前真实项目排队的 500ms 防抖任务应被费用 store 的
      // isDemoProject 分支取消，而不是遗留下来在之后照常触发
      rerender(
        <OverviewCanvas
          projectName="onboarding_demo"
          projectData={makeProjectData()}
          readOnly
        />,
      );

      await vi.advanceTimersByTimeAsync(600);

      expect(getCostEstimateSpy).not.toHaveBeenCalled();
      expect(useCostStore.getState().costData).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("OverviewCanvas ad mode", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useProjectsStore.setState(useProjectsStore.getInitialState(), true);
    useCostStore.setState(useCostStore.getInitialState(), true);
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
