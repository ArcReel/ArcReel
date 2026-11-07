import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShotDetail } from "./ShotDetail";
import { useTasksStore } from "@/stores/tasks-store";
import type { DramaScene } from "@/types";

/**
 * Per-shot duration editor uses narrowed candidates; never silently rewrite saved out-of-range values,
 * explain causes/guide reselection. Selected durations enter unsaved edits and save with shots.
 */

const saved = () => vi.fn().mockResolvedValue(true);

function makeScene(durationSeconds: number): DramaScene {
  return {
    scene_id: "E1S01",
    duration_seconds: durationSeconds,
    segment_break: false,
    characters_in_scene: [],
    scenes: [],
    props: [],
    image_prompt: {
      scene: "重逢",
      composition: { shot_type: "Medium Shot", lighting: "暖光", ambiance: "怀旧" },
    },
    video_prompt: { action: "推门而入", camera_motion: "Static", ambiance_audio: "", dialogue: [] },
    utterances: [],
  };
}

function baseProps(onUpdatePrompt: Parameters<typeof ShotDetail>[0]["onUpdatePrompt"]) {
  return {
    segment: makeScene(4),
    segmentId: "E1S01",
    contentMode: "drama" as const,
    aspectRatio: "9:16" as const,
    projectName: "demo",
    scriptFile: "episode_1.json",
    selectedIndex: 0,
    totalCount: 1,
    onPrev: () => {},
    onNext: () => {},
    onUpdatePrompt,
    durationOptions: [8],
  };
}

function renderDetail(props: Partial<Parameters<typeof ShotDetail>[0]> = {}, seconds = 4) {
  return render(
    <ShotDetail
      segment={makeScene(seconds)}
      segmentId="E1S01"
      contentMode="drama"
      aspectRatio="9:16"
      projectName="demo"
      scriptFile="episode_1.json"
      selectedIndex={0}
      totalCount={1}
      onPrev={() => {}}
      onNext={() => {}}
      onUpdatePrompt={saved()}
      durationOptions={[8]}
      {...props}
    />,
  );
}

/** Duration is the sole seconds-prefixed button; out-of-range seconds append screen-reader-readable warnings. */
function warningLabel(): string | null {
  const name = screen.getByRole("button", { name: /^\d+ 秒/ }).textContent ?? "";
  return name.replace(/^\d+ 秒/, "") || null;
}

describe("ShotDetail 时长候选与越界提示", () => {
  it("只呈现收窄后的候选，越界的已保存值仍原样显示、不被改写", () => {
    const onUpdatePrompt = saved();
    renderDetail({ onUpdatePrompt });

    // Keep saved 4s visible; silent changes would discard user settings unknowingly.
    expect(screen.getByRole("button", { name: /4 秒/ })).toBeInTheDocument();
    expect(onUpdatePrompt).not.toHaveBeenCalled();

    // Expanded choices contain only narrowed 8s.
    fireEvent.click(screen.getByRole("button", { name: /4 秒/ }));
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(1);
    expect(radios[0]).toHaveTextContent("8 秒");
  });

  it("重选的时长进入未保存修改，保存时写回", async () => {
    const onUpdatePrompt = saved();
    renderDetail({ onUpdatePrompt });
    fireEvent.click(screen.getByRole("button", { name: /4 秒/ }));
    fireEvent.click(screen.getByRole("radio", { name: /8 秒/ }));
    expect(onUpdatePrompt).not.toHaveBeenCalled();

    fireEvent.click(screen.getByRole("button", { name: "保存" }));
    await waitFor(() => expect(onUpdatePrompt).toHaveBeenCalledWith("E1S01", { duration_seconds: 8 }));
  });

  it("改完时长后分镜开始生成：保存被拒并说明原因，不写回", async () => {
    const onUpdatePrompt = saved();
    const { rerender } = render(<ShotDetail {...baseProps(onUpdatePrompt)} />);
    fireEvent.click(screen.getByRole("button", { name: /4 秒/ }));
    fireEvent.click(screen.getByRole("radio", { name: /8 秒/ }));

    rerender(<ShotDetail {...baseProps(onUpdatePrompt)} generatingVideo />);
    fireEvent.click(screen.getByRole("button", { name: "保存" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("该分镜正在生成中，暂不能修改时长");
    expect(onUpdatePrompt).not.toHaveBeenCalled();
  });

  it("候选内的值不告警", () => {
    renderDetail({}, 8);
    expect(warningLabel()).toBeNull();
  });

  // Cause-specific hints direct resolution/reference settings when appropriate;
  // claiming model unsupported would instead send users to model switches.
  it("成因为分辨率时说清是分辨率，不说成模型不支持", () => {
    renderDetail({ durationWarningReason: () => "resolution" });
    expect(warningLabel()).toContain("当前分辨率");
  });

  it("成因为参考图路径时说清是该模式", () => {
    renderDetail({ durationWarningReason: () => "reference" });
    expect(warningLabel()).toContain("参考生视频");
  });

  it("成因为模型全集不含该值、或未传成因判定时用通用文案", () => {
    const { unmount } = renderDetail({ durationWarningReason: () => "model" });
    expect(warningLabel()).toContain("模型支持范围");
    unmount();

    // Unwired future callers fall back to generic wording, never undefined keys.
    renderDetail();
    expect(warningLabel()).toContain("模型支持范围");
  });
});

/**
 * Three occupancy checks: active tasks captured old durations, so edits would mismatch artifacts/scripts.
 * Sibling storyboard/video regeneration already shares occupancy; cover opening/submission checks here.
 */
describe("ShotDetail 时长编辑的占用态门控", () => {
  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
  });

  it("该分镜生成中时禁用时长 pill 并说明原因", () => {
    for (const busyProp of ["generatingStoryboard", "generatingVideo"] as const) {
      const { unmount } = renderDetail({ [busyProp]: true }, 8);
      const pill = screen.getByRole("button", { name: /8 秒/ });
      expect(pill).toBeDisabled();
      expect(pill).toHaveAccessibleDescription("该分镜正在生成中，暂不能修改时长");
      unmount();
    }
  });

  it("面板打开后任务才启动时收起面板，且提交被拒不写回", () => {
    const onUpdatePrompt = saved();
    const { rerender } = render(
      <ShotDetail
        segment={makeScene(4)}
        segmentId="E1S01"
        contentMode="drama"
        aspectRatio="9:16"
        projectName="demo"
        scriptFile="episode_1.json"
        selectedIndex={0}
        totalCount={1}
        onPrev={() => {}}
        onNext={() => {}}
        onUpdatePrompt={onUpdatePrompt}
        durationOptions={[8]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /4 秒/ }));
    expect(screen.getAllByRole("radio")).toHaveLength(1);

    // After panel opens, video tasks start; opening-only implementations would allow writes.
    rerender(
      <ShotDetail
        segment={makeScene(4)}
        segmentId="E1S01"
        contentMode="drama"
        aspectRatio="9:16"
        projectName="demo"
        scriptFile="episode_1.json"
        selectedIndex={0}
        totalCount={1}
        onPrev={() => {}}
        onNext={() => {}}
        onUpdatePrompt={onUpdatePrompt}
        durationOptions={[8]}
        generatingVideo
      />,
    );
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(onUpdatePrompt).not.toHaveBeenCalled();
  });

  it("prop 还没跟上、但 store 已记录该分镜在跑时，提交仍被拒", () => {
    // Submit-time rechecks exist because props show last renders, while users can still click between
    // store changes and committed rerenders. Read fresh tasks-store isResourceBusy, not busy props alone.
    const onUpdatePrompt = saved();
    renderDetail({ onUpdatePrompt });
    fireEvent.click(screen.getByRole("button", { name: /4 秒/ }));

    useTasksStore.setState({
      tasks: [
        {
          project_name: "demo",
          task_type: "video",
          media_type: "video",
          resource_id: "E1S01",
          resource_type: null,
          script_file: null,
          payload: {},
          task_id: "t1",
          status: "running",
          result: null,
          error_message: null,
          cancelled_by: null,
          provider_id: null,
          provider_job_id: null,
          source: "webui",
          queued_at: "2026-07-28T00:00:00Z",
          started_at: null,
          finished_at: null,
          updated_at: "2026-07-28T00:00:00Z",
        },
      ],
    });

    fireEvent.click(screen.getByRole("radio", { name: /8 秒/ }));
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
  });

  it("同集宫格任务在跑时提交被拒（grid 按 scriptFile 判，归不进分镜粒度）", () => {
    // Grid resource_id is grid_id, invisible to shot-level isResourceBusy;
    // splitting overwrites multiple episode shots, concurrently writing the same script as duration edits.
    const onUpdatePrompt = saved();
    renderDetail({ onUpdatePrompt });
    fireEvent.click(screen.getByRole("button", { name: /4 秒/ }));

    useTasksStore.setState({
      tasks: [
        {
          project_name: "demo",
          task_type: "grid",
          media_type: "image",
          resource_id: "grid-1",
          resource_type: null,
          script_file: "episode_1.json",
          payload: {},
          task_id: "g1",
          status: "running",
          result: null,
          error_message: null,
          cancelled_by: null,
          provider_id: null,
          provider_job_id: null,
          source: "webui",
          queued_at: "2026-07-28T00:00:00Z",
          started_at: null,
          finished_at: null,
          updated_at: "2026-07-28T00:00:00Z",
        },
      ],
    });

    fireEvent.click(screen.getByRole("radio", { name: /8 秒/ }));
    expect(screen.queryByRole("button", { name: "保存" })).not.toBeInTheDocument();
  });

  it("prop 还没跟上、但 store 已记录该分镜在跑时，面板打不开", () => {
    // Opening also rechecks fresh: tasks may start between render/click while busy
    // retains old render state; relying on it would open panels.
    const onUpdatePrompt = saved();
    renderDetail({ onUpdatePrompt });

    useTasksStore.setState({
      tasks: [
        {
          project_name: "demo",
          task_type: "storyboard",
          media_type: "image",
          resource_id: "E1S01",
          resource_type: null,
          script_file: null,
          payload: {},
          task_id: "t2",
          status: "running",
          result: null,
          error_message: null,
          cancelled_by: null,
          provider_id: null,
          provider_job_id: null,
          source: "webui",
          queued_at: "2026-07-28T00:00:00Z",
          started_at: null,
          finished_at: null,
          updated_at: "2026-07-28T00:00:00Z",
        },
      ],
    });

    fireEvent.click(screen.getByRole("button", { name: /4 秒/ }));
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(onUpdatePrompt).not.toHaveBeenCalled();
  });

  it("任务结束后旧面板不自行重现", () => {
    // Derived visibility alone (open && !locked) would revive old panels/unsaved drafts after unlocking.
    // Locking must actually clear open/draftSeconds.
    const onUpdatePrompt = saved();
    const props = {
      segment: makeScene(4),
      segmentId: "E1S01",
      contentMode: "drama" as const,
      aspectRatio: "9:16" as const,
      projectName: "demo",
      scriptFile: "episode_1.json",
      selectedIndex: 0,
      totalCount: 1,
      onPrev: () => {},
      onNext: () => {},
      onUpdatePrompt,
      durationOptions: [8],
    };
    const { rerender } = render(<ShotDetail {...props} />);
    fireEvent.click(screen.getByRole("button", { name: /4 秒/ }));
    expect(screen.getAllByRole("radio")).toHaveLength(1);

    rerender(<ShotDetail {...props} generatingVideo />);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();

    // Tasks finish: remain closed until users explicitly reopen.
    rerender(<ShotDetail {...props} />);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(onUpdatePrompt).not.toHaveBeenCalled();
  });

  it("空档位的两种成因各说各的话", () => {
    renderDetail({ durationOptions: [] });

    expect(screen.getByRole("button", { name: /4 秒/ })).toHaveAccessibleDescription("当前模型未配置可用时长，无法修改");
  });

  it("时长由端点固定时不说成「未配置」", () => {
    // Workflow configuration is complete; its clip length is simply not driven by ArcReel.
    renderDetail({ durationOptions: [], durationEndpointFixed: true });

    expect(screen.getByRole("button", { name: /4 秒/ })).toHaveAccessibleDescription(
      "时长由端点固定：每段成片多长由 workflow 决定。",
    );
  });
});
