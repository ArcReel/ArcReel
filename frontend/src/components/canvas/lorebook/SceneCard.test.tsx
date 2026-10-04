import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { SceneCard } from "./SceneCard";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";

describe("SceneCard", () => {
  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  const scene = { description: "阴森古朴" };

  it("opens a prompt preview for the current description draft", async () => {
    const preview = vi.spyOn(API, "previewAssetPrompt").mockResolvedValue({
      text: "最终提示词：草稿外观", unavailable: null, is_text_form: true, warnings: [],
    });
    render(<SceneCard name="庭院" scene={{ description: "旧描述" }}
      projectName="demo" onUpdate={vi.fn()} onGenerate={vi.fn()} />);
    fireEvent.change(screen.getByDisplayValue("旧描述"), { target: { value: "草稿外观" } });
    fireEvent.click(screen.getByRole("button", { name: "查看提示词" }));
    expect(await screen.findByText("最终提示词：草稿外观")).toBeInTheDocument();
    expect(preview).toHaveBeenCalledWith("demo", "scene", "庭院", "草稿外观", { signal: expect.any(AbortSignal) });
  });

  it("renders name and description", () => {
    render(
      <SceneCard
        name="庙宇"
        scene={scene}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );
    expect(screen.getByText("庙宇")).toBeInTheDocument();
    expect(screen.getByDisplayValue("阴森古朴")).toBeInTheDocument();
  });

  it("invokes onGenerate when generate button clicked", () => {
    const onGenerate = vi.fn();
    render(
      <SceneCard
        name="A"
        scene={scene}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={onGenerate}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /生成/ }));
    expect(onGenerate).toHaveBeenCalledWith("A");
  });

  it("shows save button only when dirty", () => {
    render(
      <SceneCard
        name="A"
        scene={scene}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );
    expect(screen.queryByRole("button", { name: /保存/ })).not.toBeInTheDocument();

    const textarea = screen.getByDisplayValue("阴森古朴");
    fireEvent.change(textarea, { target: { value: "新描述" } });
    expect(screen.getByRole("button", { name: /保存/ })).toBeInTheDocument();
  });

  it("calls onUpdate when save button clicked", () => {
    const onUpdate = vi.fn();
    render(
      <SceneCard
        name="A"
        scene={scene}
        projectName="demo"
        onUpdate={onUpdate}
        onGenerate={vi.fn()}
      />,
    );

    const textarea = screen.getByDisplayValue("阴森古朴");
    fireEvent.change(textarea, { target: { value: "新描述" } });
    fireEvent.click(screen.getByRole("button", { name: /保存/ }));
    expect(onUpdate).toHaveBeenCalledWith("A", { description: "新描述" });
  });

  it("does not render importance or type badges", () => {
    render(
      <SceneCard
        name="A"
        scene={scene}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );
    expect(screen.queryByText(/major|minor|主要|次要|location|场景类型/i)).not.toBeInTheDocument();
  });

  describe("read-only", () => {
    const renderReadOnly = () =>
      render(
        <SceneCard
          name="A"
          scene={scene}
          projectName="demo"
          onUpdate={vi.fn()}
          onGenerate={vi.fn()}
          readOnly
        />,
      );

    it("still shows the content", () => {
      renderReadOnly();
      expect(screen.getByText("A")).toBeInTheDocument();
      expect(screen.getByDisplayValue("阴森古朴")).toBeInTheDocument();
    });

    it("drops the generate, upload and version entries", () => {
      renderReadOnly();
      expect(screen.queryByRole("button", { name: /生成/ })).not.toBeInTheDocument();
      expect(screen.queryByTestId("version-time-machine")).not.toBeInTheDocument();
      expect(screen.queryByRole("button")).not.toBeInTheDocument();
    });

    it("keeps the description from being edited", () => {
      renderReadOnly();
      const textarea = screen.getByDisplayValue("阴森古朴");
      expect(textarea).toHaveAttribute("readonly");
      fireEvent.change(textarea, { target: { value: "新描述" } });
      expect(screen.queryByRole("button", { name: /保存/ })).not.toBeInTheDocument();
    });
  });

  it("always shows generate button (not gated on importance)", () => {
    render(
      <SceneCard
        name="A"
        scene={{ description: "" }}
        projectName="demo"
        onUpdate={vi.fn()}
        onGenerate={vi.fn()}
      />,
    );
    expect(screen.getByRole("button", { name: /生成/ })).toBeInTheDocument();
  });
});

describe("SceneCard 资产图状态", () => {
  const status = {
    unit_id: "scene/庭院",
    asset_type: "scene" as const,
    name: "庭院",
    derivative: null,
    description_missing: false,
    image_to_image: false,
  };

  it("shows a registered but unreadable sheet as pending", () => {
    render(
      <SceneCard name="庭院" scene={{ description: "古朴", scene_sheet: "scenes/庭院.png" }}
        projectName="demo" onUpdate={vi.fn()} onGenerate={vi.fn()}
        sheetStatus={{ ...status, status: "missing" }} />,
    );

    expect(screen.queryByRole("img", { name: /庭院/ })).not.toBeInTheDocument();
    expect(screen.getByText("待生成")).toBeInTheDocument();
  });

  it("marks a missing description and disables generation until it is filled", () => {
    const onGenerate = vi.fn();
    render(
      <SceneCard name="庭院" scene={{ description: "  " }}
        projectName="demo" onUpdate={vi.fn()} onGenerate={onGenerate} />,
    );

    expect(screen.getByText("缺描述")).toBeInTheDocument();
    const button = screen.getByRole("button", { name: "生成资产图" });
    expect(button).toBeDisabled();
    expect(button.parentElement).toHaveAttribute("title", "需要先填写描述");
  });

  it("confirms the knock-on impact before regenerating a stale sheet", async () => {
    const impact = vi.spyOn(API, "getAssetRegenerationImpact").mockResolvedValue({
      stale: true,
      storyboards: 3,
      videos: 1,
      derivatives: 0,
    });
    const onGenerate = vi.fn();
    render(
      <SceneCard name="庭院" scene={{ description: "古朴", scene_sheet: "scenes/庭院.png" }}
        projectName="demo" onUpdate={vi.fn()} onGenerate={onGenerate}
        sheetStatus={{ ...status, status: "stale" }} />,
    );

    expect(screen.getByText("已过期")).toHaveAttribute("title", expect.stringContaining("已经改变"));
    fireEvent.click(screen.getByRole("button", { name: "重新生成资产图" }));

    expect(await screen.findByText("重新生成后，用到它的 3 张分镜图、1 段视频会转为过期。")).toBeInTheDocument();
    expect(onGenerate).not.toHaveBeenCalled();
    expect(impact).toHaveBeenCalledWith("demo", "scene", "庭院");
    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));
    expect(onGenerate).toHaveBeenCalledWith("庭院");
  });
  it("asks the server before regenerating when the local status has not caught up", async () => {
    // 描述刚改、状态行还没刷新时本地仍是 current；以服务端此刻的判定为准。
    vi.spyOn(API, "getAssetRegenerationImpact").mockResolvedValue({
      stale: true,
      storyboards: 2,
      videos: 0,
      derivatives: 0,
    });
    const onGenerate = vi.fn();
    render(
      <SceneCard name="庭院" scene={{ description: "新描述", scene_sheet: "scenes/庭院.png" }}
        projectName="demo" onUpdate={vi.fn()} onGenerate={onGenerate}
        sheetStatus={{ ...status, status: "current" }} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "重新生成资产图" }));

    expect(await screen.findByText("重新生成后，用到它的 2 张分镜图、0 段视频会转为过期。")).toBeInTheDocument();
    expect(onGenerate).not.toHaveBeenCalled();
  });

  it("regenerates without confirmation when the server says the sheet is current", async () => {
    vi.spyOn(API, "getAssetRegenerationImpact").mockResolvedValue({
      stale: false,
      storyboards: 2,
      videos: 0,
      derivatives: 0,
    });
    const onGenerate = vi.fn();
    render(
      <SceneCard name="庭院" scene={{ description: "古朴", scene_sheet: "scenes/庭院.png" }}
        projectName="demo" onUpdate={vi.fn()} onGenerate={onGenerate} />,
    );

    fireEvent.click(screen.getByRole("button", { name: "重新生成资产图" }));

    await waitFor(() => expect(onGenerate).toHaveBeenCalledWith("庭院"));
    expect(screen.queryByText(/会转为过期/)).not.toBeInTheDocument();
  });
});
