import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssistantStore } from "@/stores/assistant-store";
import { EditTimelineEmptyState } from "./EditTimelineEmptyState";

describe("EditTimelineEmptyState", () => {
  beforeEach(() => {
    useAssistantStore.setState({ input: "" });
    useAppStore.setState({ assistantPanelOpen: false, toast: null });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("按脚本新建剪辑时间线，不打开 Agent 面板", async () => {
    const created = { timeline: { id: "tl-1", name: "初剪", episode: 2 }, revision: 1 };
    const create = vi.spyOn(API, "createEditTimelineFromScript").mockResolvedValue(created);
    const onCreated = vi.fn();

    render(<EditTimelineEmptyState projectName="demo" episode={2} hasUsableVideo onCreated={onCreated} />);
    await userEvent.click(screen.getByRole("button", { name: "新建剪辑时间线" }));

    await waitFor(() => expect(onCreated).toHaveBeenCalledWith(created));
    expect(create).toHaveBeenCalledWith("demo", 2, "初剪");
    expect(useAppStore.getState().assistantPanelOpen).toBe(false);
    expect(useAppStore.getState().toast?.text).toBe("已新建剪辑时间线「初剪」");
  });

  it("交给 Agent 剪辑只预填请求并打开面板", async () => {
    const create = vi.spyOn(API, "createEditTimelineFromScript");

    render(<EditTimelineEmptyState projectName="demo" episode={3} hasUsableVideo onCreated={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "交给 Agent 剪辑" }));

    expect(useAssistantStore.getState().input).toBe("请为第 3 集剪辑一版。");
    expect(useAppStore.getState().assistantPanelOpen).toBe(true);
    expect(create).not.toHaveBeenCalled();
  });

  it("本集没有可用视频时两个按钮都不可点，并说明需要先生成视频", () => {
    render(<EditTimelineEmptyState projectName="demo" episode={1} hasUsableVideo={false} onCreated={vi.fn()} />);

    expect(screen.getByRole("button", { name: "交给 Agent 剪辑" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "新建剪辑时间线" })).toBeDisabled();
    expect(screen.getByText("需要先生成视频")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "新建剪辑时间线" }).parentElement).toHaveAttribute(
      "title",
      "需要先生成视频",
    );
  });

  it("新建失败时提示原因", async () => {
    vi.spyOn(API, "createEditTimelineFromScript").mockRejectedValue(new Error("第 1 集已有名为「初剪」的剪辑时间线"));
    const onCreated = vi.fn();

    render(<EditTimelineEmptyState projectName="demo" episode={1} hasUsableVideo onCreated={onCreated} />);
    await userEvent.click(screen.getByRole("button", { name: "新建剪辑时间线" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast?.text).toBe(
        "新建剪辑时间线失败：第 1 集已有名为「初剪」的剪辑时间线",
      ),
    );
    expect(onCreated).not.toHaveBeenCalled();
  });
});
