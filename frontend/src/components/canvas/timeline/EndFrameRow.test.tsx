import { fireEvent, render, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import type { TaskItem, VideoCapabilities } from "@/types";
import { EndFrameRow } from "./EndFrameRow";

const PROJECT = "demo";
const SHOT = "E1S01";
const SCRIPT = "episode_1.json";

function caps(lastFrame: boolean): VideoCapabilities {
  return {
    provider_id: "gemini",
    model: "veo-3",
    supported_durations: [5, 8],
    max_duration: 8,
    max_reference_images: 3,
    first_frame: true,
    last_frame: lastFrame,
    source: "registry",
  };
}

function videoTask(status: TaskItem["status"]): TaskItem {
  return {
    task_id: "t1",
    project_name: PROJECT,
    task_type: "video",
    media_type: "video",
    resource_id: SHOT,
    resource_type: null,
    script_file: SCRIPT,
    payload: {},
    status,
    result: null,
    error_message: null,
    cancelled_by: null,
    provider_id: null,
    provider_job_id: null,
    source: "webui",
    queued_at: "2026-01-01T00:00:00Z",
    started_at: null,
    finished_at: null,
    updated_at: "2026-01-01T00:00:00Z",
  };
}

function renderRow(props: Partial<Parameters<typeof EndFrameRow>[0]> = {}) {
  return render(
    <EndFrameRow
      projectName={PROJECT}
      segmentId={SHOT}
      scriptFile={SCRIPT}
      contentMode="narration"
      aspectRatio="9:16"
      endFramePath={null}
      videoBackend="gemini"
      {...props}
    />,
  );
}

const refreshProject = vi.fn().mockResolvedValue("success");

beforeEach(() => {
  vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(true));
  vi.spyOn(API, "listGrids").mockResolvedValue([]);
  useProjectsStore.setState({
    currentProjectName: PROJECT,
    currentScripts: {
      [SCRIPT]: {
        episode: 1,
        title: "第一集",
        segments: [
          {
            segment_id: SHOT,
            novel_text: "",
            image_prompt: "",
            video_prompt: "",
            generated_assets: { storyboard_image: "storyboards/E1S01_v1.png" },
          },
        ],
         
      } as any,
    },
    refreshProject,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  refreshProject.mockClear();
  useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  useProjectsStore.setState(useProjectsStore.getInitialState(), true);
});

describe("EndFrameRow 三态摘要", () => {
  it("未设置尾帧时摘要为「未设置」，展开只给「选择图片」", async () => {
    const { getByRole, queryByRole, findByText } = renderRow();
    await findByText("未设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    expect(getByRole("button", { name: "选择图片" })).toBeInTheDocument();
    expect(queryByRole("button", { name: "清除" })).toBeNull();
  });

  it("已设置尾帧时摘要为「已设置」，展开给「更换图片」与「清除」", async () => {
    const { getByRole, findByText } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    await findByText("已设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    expect(getByRole("button", { name: "更换图片" })).toBeInTheDocument();
    expect(getByRole("button", { name: "清除" })).toBeInTheDocument();
  });

  it("last_frame 生效值为否时摘要为「模型不支持」，更换灰化给出原因、清除仍可点", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(false));
    const { getByRole, findByText } = renderRow({
      endFramePath: "end_frames/scene_E1S01.png",
    });
    await findByText("模型不支持");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    expect(getByRole("button", { name: /尾帧/ })).toHaveAttribute("aria-expanded", "true");
    await findByText(/当前视频模型不支持尾帧/);
    // 灰化而非隐藏：更换在位且禁用，hover 提示给出模型级原因。
    const replaceBtn = getByRole("button", { name: "更换图片" });
    expect(replaceBtn).toBeDisabled();
    expect(replaceBtn).toHaveAttribute("title", expect.stringMatching(/当前视频模型不支持尾帧/));
    // 清除不受模型能力门控：清掉一张已设置的尾帧不需要模型支持该能力。
    expect(getByRole("button", { name: "清除" })).toBeEnabled();
  });

  it("换模型后重新解析能力，门控随之更新", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(true));
    const { rerender, findByText } = renderRow();
    await findByText("未设置");

    spy.mockResolvedValue(caps(false));
    rerender(
      <EndFrameRow
        projectName={PROJECT}
        segmentId={SHOT}
        scriptFile={SCRIPT}
        contentMode="narration"
        aspectRatio="9:16"
        endFramePath={null}
        videoBackend="ark"
      />,
    );
    await findByText("模型不支持");
  });
});

describe("EndFrameRow 占用态", () => {
  it("本镜头视频任务在途时兄弟控件同步禁用", async () => {
    useTasksStore.setState({ tasks: [videoTask("running")] });
    const { getByRole, findByText } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    await findByText("已设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    expect(getByRole("button", { name: "更换图片" })).toBeDisabled();
    expect(getByRole("button", { name: "清除" })).toBeDisabled();
  });

  it("分镜任务在途不禁用尾帧控件", async () => {
    useTasksStore.setState({
      tasks: [{ ...videoTask("running"), task_type: "storyboard", media_type: "image" }],
    });
    const { getByRole, findByText } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    await findByText("已设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    expect(getByRole("button", { name: "清除" })).toBeEnabled();
  });

  it("选图器打开后本镜头被入队：提交时刻复核占用态并拒绝", async () => {
    const select = vi.spyOn(API, "selectEndFrame");
    const { getByRole, findByText, findByRole } = renderRow();
    await findByText("未设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    fireEvent.click(getByRole("button", { name: "选择图片" }));

    // 选中本集分镜图（项目内通道）
    fireEvent.click(await findByRole("button", { name: /镜头 E1S01/ }));

    // 打开选图器之后该镜头才被入队——只查开窗时刻会漏掉这个窗口
    useTasksStore.setState({ tasks: [videoTask("queued")] });

    fireEvent.click(getByRole("button", { name: "设为尾帧" }));
    await waitFor(() => {
      expect(select).not.toHaveBeenCalled();
    });
  });

  it("空闲时选图提交调用 select 端点并刷新项目以拿到新指纹", async () => {
    const select = vi
      .spyOn(API, "selectEndFrame")
      .mockResolvedValue({ success: true, end_frame_image: "end_frames/scene_E1S01.png" });
    const { getByRole, findByText, findByRole } = renderRow();
    await findByText("未设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    fireEvent.click(getByRole("button", { name: "选择图片" }));
    fireEvent.click(await findByRole("button", { name: /镜头 E1S01/ }));
    fireEvent.click(getByRole("button", { name: "设为尾帧" }));

    await waitFor(() => {
      expect(select).toHaveBeenCalledWith(PROJECT, SHOT, SCRIPT, "storyboards/E1S01_v1.png");
    });
    await waitFor(() => {
      expect(refreshProject).toHaveBeenCalledWith(PROJECT);
    });
  });

  it("选图器打开后能力变为不支持：提交时刻复核并拒绝", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(true));
    const select = vi.spyOn(API, "selectEndFrame");
    const { getByRole, findByText, findByRole, rerender } = renderRow();
    await findByText("未设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    fireEvent.click(getByRole("button", { name: "选择图片" }));
    fireEvent.click(await findByRole("button", { name: /镜头 E1S01/ }));

    // 打开选图器之后能力才被判定为不支持——只查开窗时刻会漏掉这个窗口
    spy.mockResolvedValue(caps(false));
    rerender(
      <EndFrameRow
        projectName={PROJECT}
        segmentId={SHOT}
        scriptFile={SCRIPT}
        contentMode="narration"
        aspectRatio="9:16"
        endFramePath={null}
        videoBackend="ark"
      />,
    );
    await findByText("模型不支持");

    fireEvent.click(getByRole("button", { name: "设为尾帧" }));
    await waitFor(() => {
      expect(select).not.toHaveBeenCalled();
    });
  });

  it("写入成功但刷新项目失败：提示刷新失败而非写入失败", async () => {
    refreshProject.mockResolvedValueOnce("failed");
    vi
      .spyOn(API, "selectEndFrame")
      .mockResolvedValue({ success: true, end_frame_image: "end_frames/scene_E1S01.png" });
    const { getByRole, findByText, findByRole } = renderRow();
    await findByText("未设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    fireEvent.click(getByRole("button", { name: "选择图片" }));
    fireEvent.click(await findByRole("button", { name: /镜头 E1S01/ }));
    fireEvent.click(getByRole("button", { name: "设为尾帧" }));

    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toMatch(/页面数据刷新失败/);
    });
  });

  it("写入成功但刷新恰好被项目切换取消：不误报刷新失败", async () => {
    refreshProject.mockResolvedValueOnce("cancelled");
    vi
      .spyOn(API, "selectEndFrame")
      .mockResolvedValue({ success: true, end_frame_image: "end_frames/scene_E1S01.png" });
    const { getByRole, findByText, findByRole } = renderRow();
    await findByText("未设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    fireEvent.click(getByRole("button", { name: "选择图片" }));
    fireEvent.click(await findByRole("button", { name: /镜头 E1S01/ }));
    fireEvent.click(getByRole("button", { name: "设为尾帧" }));

    await waitFor(() => {
      expect(refreshProject).toHaveBeenCalledWith(PROJECT);
    });
    // 写入成功的提示保留，不被追加或覆盖为刷新失败提示。
    expect(useAppStore.getState().toast?.text).not.toMatch(/页面数据刷新失败/);
    expect(useAppStore.getState().toast?.tone).toBe("success");
  });

  it("提交在途状态经 onSubmittingChange 回传父级", async () => {
    let resolveSelect: (v: { success: boolean; end_frame_image: string }) => void = () => {};
    vi.spyOn(API, "selectEndFrame").mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveSelect = resolve;
        }),
    );
    const onSubmittingChange = vi.fn();
    const { getByRole, findByText, findByRole } = renderRow({ onSubmittingChange });
    await findByText("未设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    fireEvent.click(getByRole("button", { name: "选择图片" }));
    fireEvent.click(await findByRole("button", { name: /镜头 E1S01/ }));
    fireEvent.click(getByRole("button", { name: "设为尾帧" }));

    await waitFor(() => {
      expect(onSubmittingChange).toHaveBeenCalledWith(true);
    });

    resolveSelect({ success: true, end_frame_image: "end_frames/scene_E1S01.png" });
    await waitFor(() => {
      expect(onSubmittingChange).toHaveBeenCalledWith(false);
    });
  });

  it("清除调用 clear 端点", async () => {
    const clear = vi.spyOn(API, "clearEndFrame").mockResolvedValue({ success: true });
    const { getByRole, findByText } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    await findByText("已设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    fireEvent.click(getByRole("button", { name: "清除" }));

    await waitFor(() => {
      expect(clear).toHaveBeenCalledWith(PROJECT, SHOT, SCRIPT);
    });
  });

  it("视频卡手动上传在途时反向禁用本行写入控件", async () => {
    const { getByRole, findByText } = renderRow({
      endFramePath: "end_frames/scene_E1S01.png",
      videoUploadBusy: true,
    });
    await findByText("已设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    expect(getByRole("button", { name: "更换图片" })).toBeDisabled();
    expect(getByRole("button", { name: "清除" })).toBeDisabled();
  });

  it("只读上下文不给写入入口", async () => {
    const { getByRole, queryByRole, findByText } = renderRow({
      endFramePath: "end_frames/scene_E1S01.png",
      readOnly: true,
    });
    await findByText("已设置");

    fireEvent.click(getByRole("button", { name: /尾帧/ }));
    expect(queryByRole("button", { name: "更换图片" })).toBeNull();
    expect(queryByRole("button", { name: "清除" })).toBeNull();
  });
});
