import { act, fireEvent, render, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useCapabilitiesStore } from "@/stores/capabilities-store";
import { type RefreshProjectResult, useProjectsStore } from "@/stores/projects-store";
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
    voice_consistency: "soft",
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

// mock 的结果值用 satisfies 钉在 RefreshProjectResult 上：vi.fn() 本身不受 setState 的
// 类型约束，联合成员改名时若不钉住，这里会静默停留在过期字面量上而测试照常通过。
const refreshProject = vi.fn().mockResolvedValue("success" satisfies RefreshProjectResult);

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

describe("EndFrameRow 摘要", () => {
  it("未设置尾帧时摘要为「未设置」，展开只给「选择图片」", async () => {
    const { getByRole, queryByRole, findByText } = renderRow();
    await findByText("未设置");

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    expect(getByRole("button", { name: "选择图片" })).toBeInTheDocument();
    expect(queryByRole("button", { name: "清除" })).not.toBeInTheDocument();
  });

  it("已设置尾帧时摘要为「已设置」，展开给「更换图片」与「清除」", async () => {
    const { getByRole, findByText } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    await findByText("已设置");

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    expect(getByRole("button", { name: "更换图片" })).toBeInTheDocument();
    expect(getByRole("button", { name: "清除" })).toBeInTheDocument();
  });

  it("模型不支持不改写摘要：已设尾帧仍报「已设置」", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(false));
    const { findByText } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    // 摘要只说尾帧设没设，能力维度交给警告条，否则「已设置」被盖掉、用户看不出有东西要清。
    expect(await findByText("已设置")).toBeInTheDocument();
  });
});

describe("EndFrameRow 能力警告", () => {
  it("模型不支持 + 已设尾帧：警告可见，清除可用且调用 clear 端点", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(false));
    const clear = vi.spyOn(API, "clearEndFrame").mockResolvedValue({ success: true });
    const { findByRole, getByRole } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });

    // 收起状态即可见：不必展开就知道这条尾帧会让生成被拒。
    const alert = await findByRole("alert");
    expect(alert).toHaveTextContent(/当前模型不支持尾帧/);
    expect(getByRole("button", { name: /^尾帧/ })).toHaveAttribute("aria-expanded", "false");

    const clearBtn = within(alert).getByRole("button", { name: "清除尾帧" });
    expect(clearBtn).toBeEnabled();
    fireEvent.click(clearBtn);
    await waitFor(() => {
      expect(clear).toHaveBeenCalledWith(PROJECT, SHOT, SCRIPT);
    });
  });

  it("模型不支持不禁用写入控件", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(false));
    const { getByRole, findByRole } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    await findByRole("alert");

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    // 能力不足靠警告表达，不靠灰化控件：后端硬失败仍在兜底。
    expect(getByRole("button", { name: "更换图片" })).toBeEnabled();
  });

  it("模型不支持时新设尾帧全程可走通，无半禁用残留", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(false));
    const select = vi
      .spyOn(API, "selectEndFrame")
      .mockResolvedValue({ success: true, end_frame_image: "end_frames/scene_E1S01.png" });
    const { getByRole, findByText, findByRole } = renderRow({ endFramePath: null });
    await findByText("未设置");

    // 入口可点
    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    const chooseBtn = getByRole("button", { name: "选择图片" });
    expect(chooseBtn).toBeEnabled();
    fireEvent.click(chooseBtn);

    // 选图器内部同样不得残留禁用：候选可选、确认可点。
    const candidate = await findByRole("button", { name: /分镜 E1S01/ });
    expect(candidate).toBeEnabled();
    fireEvent.click(candidate);
    const confirmBtn = getByRole("button", { name: "设为尾帧" });
    expect(confirmBtn).toBeEnabled();
    fireEvent.click(confirmBtn);

    // 提交时刻的复核只看占用态，不再因能力不支持而拦截。
    await waitFor(() => {
      expect(select).toHaveBeenCalledWith(PROJECT, SHOT, SCRIPT, "storyboards/E1S01_v1.png");
    });
  });

  it("能力查询尚未落地时也不禁用写入控件", async () => {
    // 能力维度整体不参与门控：「不支持」不拦，「还不知道支不支持」更没有可拦的理由，
    // 否则换模型后凭能力管线又会短暂灰掉写入控件——这正是要清掉的半禁用残留。
    vi.spyOn(API, "getVideoCapabilities").mockReturnValue(new Promise(() => {}));
    const { getByRole, findByText } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    await findByText("检查中…");

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    expect(getByRole("button", { name: "更换图片" })).toBeEnabled();
    expect(getByRole("button", { name: "清除" })).toBeEnabled();
  });

  it("模型支持尾帧时无警告", async () => {
    const { findByText, queryByRole } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    await findByText("已设置");
    expect(queryByRole("alert")).not.toBeInTheDocument();
  });

  it("未设尾帧的分镜即使模型不支持也不出警告", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(false));
    const { findByText, queryByRole } = renderRow({ endFramePath: null });
    await findByText("未设置");
    // 没有会被拒绝的东西，不打扰。
    expect(queryByRole("alert")).not.toBeInTheDocument();
  });

  it("换模型后警告随最新能力结果出现", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(true));
    const { rerender, findByText, findByRole, queryByRole } = renderRow({
      endFramePath: "end_frames/scene_E1S01.png",
    });
    await findByText("已设置");
    expect(queryByRole("alert")).not.toBeInTheDocument();

    spy.mockResolvedValue(caps(false));
    rerender(
      <EndFrameRow
        projectName={PROJECT}
        segmentId={SHOT}
        scriptFile={SCRIPT}
        contentMode="narration"
        aspectRatio="9:16"
        endFramePath="end_frames/scene_E1S01.png"
        videoBackend="ark"
      />,
    );
    expect(await findByRole("alert")).toHaveTextContent(/当前模型不支持尾帧/);
  });

  it("改能力覆盖后收起态警告自动出现，无需展开面板", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(true));
    const { findByText, findByRole, queryByRole } = renderRow({
      endFramePath: "end_frames/scene_E1S01.png",
    });
    await findByText("已设置");
    expect(queryByRole("alert")).not.toBeInTheDocument();

    // 能力覆盖写在供应商配置上、不落任何项目字段：没有 props 会变，靠失效信号驱动重取。
    spy.mockResolvedValue(caps(false));
    act(() => useCapabilitiesStore.getState().invalidate());
    expect(await findByRole("alert")).toHaveTextContent(/当前模型不支持尾帧/);
  });

  it("改能力覆盖把尾帧放开后，收起态警告自动消失", async () => {
    const spy = vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(false));
    const { findByRole, queryByRole } = renderRow({
      endFramePath: "end_frames/scene_E1S01.png",
    });
    await findByRole("alert");

    spy.mockResolvedValue(caps(true));
    act(() => useCapabilitiesStore.getState().invalidate());
    await waitFor(() => expect(queryByRole("alert")).not.toBeInTheDocument());
  });

  it("能力查询失败时不谎报不支持", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockRejectedValue(new Error("boom"));
    const { findByText, queryByRole } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    await findByText("已设置");
    expect(queryByRole("alert")).not.toBeInTheDocument();
  });

  it("警告里的清除按占用态同步禁用", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(false));
    useTasksStore.setState({ tasks: [videoTask("running")] });
    const { findByRole } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    const alert = await findByRole("alert");
    expect(within(alert).getByRole("button", { name: "清除尾帧" })).toBeDisabled();
  });

  it("只读上下文只给警告文本，不给清除入口", async () => {
    vi.spyOn(API, "getVideoCapabilities").mockResolvedValue(caps(false));
    const { findByRole } = renderRow({
      endFramePath: "end_frames/scene_E1S01.png",
      readOnly: true,
    });
    const alert = await findByRole("alert");
    expect(alert).toHaveTextContent(/当前模型不支持尾帧/);
    expect(within(alert).queryByRole("button")).not.toBeInTheDocument();
  });
});

describe("EndFrameRow 占用态", () => {
  it("本分镜视频任务在途时兄弟控件同步禁用", async () => {
    useTasksStore.setState({ tasks: [videoTask("running")] });
    const { getByRole, findByText } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    await findByText("已设置");

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    expect(getByRole("button", { name: "更换图片" })).toBeDisabled();
    expect(getByRole("button", { name: "清除" })).toBeDisabled();
  });

  it("分镜任务在途不禁用尾帧控件", async () => {
    useTasksStore.setState({
      tasks: [{ ...videoTask("running"), task_type: "storyboard", media_type: "image" }],
    });
    const { getByRole, findByText } = renderRow({ endFramePath: "end_frames/scene_E1S01.png" });
    await findByText("已设置");

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    expect(getByRole("button", { name: "清除" })).toBeEnabled();
  });

  it("选图器打开后本分镜被入队：提交时刻复核占用态并拒绝", async () => {
    const select = vi.spyOn(API, "selectEndFrame");
    const { getByRole, findByText, findByRole } = renderRow();
    await findByText("未设置");

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    fireEvent.click(getByRole("button", { name: "选择图片" }));

    // 选中本集分镜图（项目内通道）
    fireEvent.click(await findByRole("button", { name: /分镜 E1S01/ }));

    // 打开选图器之后该分镜才被入队——只查开窗时刻会漏掉这个窗口
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

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    fireEvent.click(getByRole("button", { name: "选择图片" }));
    fireEvent.click(await findByRole("button", { name: /分镜 E1S01/ }));
    fireEvent.click(getByRole("button", { name: "设为尾帧" }));

    await waitFor(() => {
      expect(select).toHaveBeenCalledWith(PROJECT, SHOT, SCRIPT, "storyboards/E1S01_v1.png");
    });
    await waitFor(() => {
      expect(refreshProject).toHaveBeenCalledWith(PROJECT);
    });
  });

  it("写入成功但刷新项目失败：提示刷新失败而非写入失败", async () => {
    refreshProject.mockResolvedValueOnce("failed" satisfies RefreshProjectResult);
    vi
      .spyOn(API, "selectEndFrame")
      .mockResolvedValue({ success: true, end_frame_image: "end_frames/scene_E1S01.png" });
    const { getByRole, findByText, findByRole } = renderRow();
    await findByText("未设置");

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    fireEvent.click(getByRole("button", { name: "选择图片" }));
    fireEvent.click(await findByRole("button", { name: /分镜 E1S01/ }));
    fireEvent.click(getByRole("button", { name: "设为尾帧" }));

    await waitFor(() => {
      expect(useAppStore.getState().toast?.text).toMatch(/页面数据刷新失败/);
    });
  });

  it("写入成功但刷新恰好被项目切换取消：不误报刷新失败", async () => {
    refreshProject.mockResolvedValueOnce("cancelled" satisfies RefreshProjectResult);
    vi
      .spyOn(API, "selectEndFrame")
      .mockResolvedValue({ success: true, end_frame_image: "end_frames/scene_E1S01.png" });
    const { getByRole, findByText, findByRole } = renderRow();
    await findByText("未设置");

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    fireEvent.click(getByRole("button", { name: "选择图片" }));
    fireEvent.click(await findByRole("button", { name: /分镜 E1S01/ }));
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

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    fireEvent.click(getByRole("button", { name: "选择图片" }));
    fireEvent.click(await findByRole("button", { name: /分镜 E1S01/ }));
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

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
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

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    expect(getByRole("button", { name: "更换图片" })).toBeDisabled();
    expect(getByRole("button", { name: "清除" })).toBeDisabled();
  });

  it("只读上下文不给写入入口", async () => {
    const { getByRole, queryByRole, findByText } = renderRow({
      endFramePath: "end_frames/scene_E1S01.png",
      readOnly: true,
    });
    await findByText("已设置");

    fireEvent.click(getByRole("button", { name: /^尾帧/ }));
    expect(queryByRole("button", { name: "更换图片" })).not.toBeInTheDocument();
    expect(queryByRole("button", { name: "清除" })).not.toBeInTheDocument();
  });
});
