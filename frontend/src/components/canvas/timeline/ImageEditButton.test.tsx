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
});
