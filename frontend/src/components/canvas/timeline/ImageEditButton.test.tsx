import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { TaskItem } from "@/types";
import { ImageEditButton } from "./ImageEditButton";

function makeTask(overrides: Partial<TaskItem> = {}): TaskItem {
  return {
    task_id: "t-edit-1",
    project_name: "demo",
    task_type: "storyboard",
    media_type: "image",
    resource_id: "E1S1",
    resource_type: null,
    script_file: null,
    payload: {},
    status: "running",
    result: null,
    error_message: null,
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
    source: "webui",
    queued_at: "2026-07-24T00:00:00Z",
    started_at: "2026-07-24T00:00:00Z",
    finished_at: null,
    updated_at: "2026-07-24T00:00:01Z",
    ...overrides,
  };
}

describe("ImageEditButton", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
    useTasksStore.setState({ tasks: [], optimisticActive: new Set(), optimisticActiveScriptFile: new Set() });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("提交时被 getState() 新鲜读拦截：弹窗停留期间响应式 busy prop 未追上真实 store 的占用变化", async () => {
    const editSpy = vi.spyOn(API, "editImage");
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");

    // busy 一直是 false（打开时刻的响应式信号），弹窗停留期间该分镜进入占用态
    // 只写进了 tasks store，未反映到这个 prop 上——提交必须靠 store 新鲜读兜底。
    render(
      <ImageEditButton
        projectName="demo"
        resourceType="storyboard"
        resourceId="E1S1"
        scriptFile="episode_1.json"
        hasImage
        busy={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑图片" }));
    const instructionField = await screen.findByLabelText("编辑指令");
    fireEvent.change(instructionField, { target: { value: "把背景改成夜晚" } });

    useTasksStore.setState({ tasks: [makeTask()] });

    fireEvent.click(screen.getByRole("button", { name: "提交编辑" }));

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("该资源刚被其他任务占用，请稍后再试", "error");
    });
    expect(editSpy).not.toHaveBeenCalled();
  });

  it("占用状态不存在时提交正常入队", async () => {
    const editSpy = vi.spyOn(API, "editImage").mockResolvedValue({
      success: true,
      task_id: "t-1",
      deduped: false,
      message: "已提交",
    });

    render(
      <ImageEditButton
        projectName="demo"
        resourceType="storyboard"
        resourceId="E1S1"
        scriptFile="episode_1.json"
        hasImage
        busy={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑图片" }));
    const instructionField = await screen.findByLabelText("编辑指令");
    fireEvent.change(instructionField, { target: { value: "把背景改成夜晚" } });

    fireEvent.click(screen.getByRole("button", { name: "提交编辑" }));

    await waitFor(() => {
      expect(editSpy).toHaveBeenCalledWith("demo", {
        resourceType: "storyboard",
        resourceId: "E1S1",
        instruction: "把背景改成夜晚",
        scriptFile: "episode_1.json",
      });
    });
  });

  it("提交时被 getState() 新鲜读拦截：busy prop 未追上时，宫格模式下本集 grid 任务占用靠 scriptFile 新鲜读兜底", async () => {
    const editSpy = vi.spyOn(API, "editImage");
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");

    // busy 全程为 false（父组件的响应式信号未追上）；grid 任务只写进了 tasks store，
    // 其 resource_id 是 grid_id，不落进 storyboard 占用集，只能靠 scriptFile 维度的
    // 新鲜读拦住——验证 handleSubmit 里补的这层复核。
    render(
      <ImageEditButton
        projectName="demo"
        resourceType="storyboard"
        resourceId="E1S1"
        scriptFile="episode_1.json"
        hasImage
        busy={false}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "编辑图片" }));
    const instructionField = await screen.findByLabelText("编辑指令");
    fireEvent.change(instructionField, { target: { value: "把背景改成夜晚" } });

    useTasksStore.setState({
      tasks: [
        makeTask({
          task_id: "t-grid-1",
          task_type: "grid",
          resource_id: "grid-1",
          script_file: "episode_1.json",
        }),
      ],
    });

    fireEvent.keyDown(instructionField, { key: "Enter", metaKey: true });

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("该资源刚被其他任务占用，请稍后再试", "error");
    });
    expect(editSpy).not.toHaveBeenCalled();
  });

  it("busy 维度仍拦截键盘提交：本资源占用集之外的占用（宫格模式下本集 grid 任务在跑）只反映在 busy prop 上", async () => {
    const editSpy = vi.spyOn(API, "editImage");

    const props = {
      projectName: "demo",
      resourceType: "storyboard" as const,
      resourceId: "E1S1",
      scriptFile: "episode_1.json",
      hasImage: true,
    };
    const { rerender } = render(<ImageEditButton {...props} busy={false} />);

    fireEvent.click(screen.getByRole("button", { name: "编辑图片" }));
    const instructionField = await screen.findByLabelText("编辑指令");
    fireEvent.change(instructionField, { target: { value: "把背景改成夜晚" } });

    // tasks store 里没有这张分镜的任务行——grid 任务的 resource_id 是 grid_id，
    // 归不进 storyboard 占用集，只能靠 busy 这层判定拦住。
    rerender(<ImageEditButton {...props} busy />);

    // 键盘快捷键提交绕过按钮的 disabled 属性，直接进 handleSubmit
    fireEvent.keyDown(instructionField, { key: "Enter", metaKey: true });

    await waitFor(() => {
      expect(screen.getByRole("button", { name: "提交编辑" })).toBeDisabled();
    });
    expect(editSpy).not.toHaveBeenCalled();
  });
});
