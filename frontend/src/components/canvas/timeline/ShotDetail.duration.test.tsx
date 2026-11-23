import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShotDetail } from "./ShotDetail";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
import type { DramaScene } from "@/types";


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

function warningLabel(): string | null {
  const name = screen.getByRole("button", { name: /^\d+ 秒/ }).textContent ?? "";
  return name.replace(/^\d+ 秒/, "") || null;
}

describe("ShotDetail 时长候选与越界提示", () => {
  it("只呈现收窄后的候选，越界的已保存值仍原样显示、不被改写", () => {
    const onUpdatePrompt = saved();
    renderDetail({ onUpdatePrompt });

    expect(screen.getByRole("button", { name: /4 秒/ })).toBeInTheDocument();
    expect(onUpdatePrompt).not.toHaveBeenCalled();

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

    renderDetail();
    expect(warningLabel()).toContain("模型支持范围");
  });
});

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

    rerender(<ShotDetail {...props} />);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(onUpdatePrompt).not.toHaveBeenCalled();
  });

  it("空档位的两种成因各说各的话", () => {
    renderDetail({ durationOptions: [] });

    expect(screen.getByRole("button", { name: /4 秒/ })).toHaveAccessibleDescription("当前模型未配置可用时长，无法修改");
  });

  it("时长由端点固定时不说成「未配置」", () => {
    renderDetail({ durationOptions: [], durationEndpointFixed: true });

    expect(screen.getByRole("button", { name: /4 秒/ })).toHaveAccessibleDescription(
      "时长由端点固定：每段成片多长由 workflow 决定。",
    );
  });
});


it("挂尾帧后保留越界秒数并显示尾帧成因，只提供带尾帧档位", async () => {
  vi.spyOn(API, "getVideoCapabilities").mockResolvedValue({
    provider_id: "gemini", model: "omni", supported_durations: [4, 6, 8], max_duration: 8,
    max_reference_images: 3, first_frame: true, last_frame: true, source: "registry", voice_consistency: "soft",
    duration_constraints: { resolution: null, uses_reference_images: false, allowed: [4, 6, 8], excluded: {} },
    end_frame_unit_durations: {
      without_end_frame: [4, 6, 8], with_end_frame: [4, 6], excluded: { "8": "end_frame" }, problem: null,
    },
  });
  const onUpdatePrompt = saved();
  renderDetail({ segment: { ...makeScene(8), end_frame_image: "end_frames/E1S01.png" }, onUpdatePrompt, durationOptions: [4, 6, 8] });
  await waitFor(() => expect(screen.getByRole("button", { name: /^8 秒/ })).toHaveTextContent("首尾帧"));
  expect(onUpdatePrompt).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: /^8 秒/ }));
  expect(screen.queryByRole("radio", { name: /8 秒/ })).not.toBeInTheDocument();
  expect(screen.getByRole("radio", { name: /6 秒/ })).toBeInTheDocument();
});

it("已有尾帧的分镜在能力查询未返回或失败时禁用时长选择，不回落父级普通档位", async () => {
  let resolveCaps: (value: Awaited<ReturnType<typeof API.getVideoCapabilities>>) => void = () => {};
  vi.spyOn(API, "getVideoCapabilities").mockReturnValueOnce(
    new Promise((resolve) => {
      resolveCaps = resolve;
    }),
  );
  const { unmount } = renderDetail(
    { segment: { ...makeScene(8), end_frame_image: "end_frames/E1S01.png" }, durationOptions: [4, 6, 8] },
    8,
  );
  const loadingPill = screen.getByRole("button", { name: /^8 秒/ });
  expect(loadingPill).toBeDisabled();
  expect(loadingPill).toHaveAccessibleDescription("检查中…");
  fireEvent.click(loadingPill);
  expect(screen.queryByRole("radio")).not.toBeInTheDocument();

  resolveCaps({
    provider_id: "gemini",
    model: "omni",
    supported_durations: [4, 6, 8],
    max_duration: 8,
    max_reference_images: 3,
    first_frame: true,
    last_frame: true,
    source: "registry",
    voice_consistency: "soft",
    duration_constraints: { resolution: null, uses_reference_images: false, allowed: [4, 6, 8], excluded: {} },
    end_frame_unit_durations: {
      without_end_frame: [4, 6, 8],
      with_end_frame: [4, 6],
      excluded: { "8": "end_frame" },
      problem: null,
    },
  });
  await waitFor(() => expect(screen.getByRole("button", { name: /^8 秒/ })).toBeEnabled());
  unmount();

  vi.spyOn(API, "getVideoCapabilities").mockRejectedValueOnce(new Error("network down"));
  renderDetail(
    { segment: { ...makeScene(8), end_frame_image: "end_frames/E1S01.png" }, durationOptions: [4, 6, 8] },
    8,
  );
  await waitFor(() =>
    expect(screen.getByRole("button", { name: /^8 秒/ })).toHaveAccessibleDescription(/未能获取首尾帧可用时长/),
  );
  expect(screen.getByRole("button", { name: /^8 秒/ })).toBeDisabled();
});

it("尾帧与分辨率交集为空时保持禁用，同时显示尾帧越界成因与移除尾帧指引", async () => {
  vi.spyOn(API, "getVideoCapabilities").mockResolvedValue({
    provider_id: "gemini",
    model: "omni",
    supported_durations: [4, 6, 8],
    max_duration: 8,
    max_reference_images: 3,
    first_frame: true,
    last_frame: true,
    source: "registry",
    voice_consistency: "soft",
    duration_constraints: {
      resolution: "1080p",
      uses_reference_images: false,
      allowed: [8],
      excluded: { "4": "resolution", "6": "resolution" },
    },
    end_frame_unit_durations: {
      without_end_frame: [8],
      with_end_frame: [],
      excluded: { "4": "resolution", "6": "resolution", "8": "end_frame" },
      problem: { code: "video_end_frame_durations_incompatible", params: {}, action: "fix_input" },
    },
  });
  renderDetail(
    { segment: { ...makeScene(8), end_frame_image: "end_frames/E1S01.png" }, durationOptions: [8] },
    8,
  );
  await waitFor(() => expect(screen.getByRole("button", { name: /^8 秒/ })).toHaveTextContent("移除尾帧"));
  const pill = screen.getByRole("button", { name: /^8 秒/ });
  expect(pill).toBeDisabled();
  expect(pill).toHaveAccessibleDescription(/移除尾帧/);
  expect(pill).not.toHaveAccessibleDescription(/未配置可用时长/);
});

it("尾帧与分辨率交集为空但已存秒数本就被分辨率排除时保留分辨率成因", async () => {
  vi.spyOn(API, "getVideoCapabilities").mockResolvedValue({
    provider_id: "gemini",
    model: "omni",
    supported_durations: [4, 6, 8],
    max_duration: 8,
    max_reference_images: 3,
    first_frame: true,
    last_frame: true,
    source: "registry",
    voice_consistency: "soft",
    duration_constraints: {
      resolution: "1080p",
      uses_reference_images: false,
      allowed: [8],
      excluded: { "4": "resolution", "6": "resolution" },
    },
    end_frame_unit_durations: {
      without_end_frame: [8],
      with_end_frame: [],
      excluded: { "4": "resolution", "6": "resolution", "8": "end_frame" },
      problem: { code: "video_end_frame_durations_incompatible", params: {}, action: "fix_input" },
    },
  });
  renderDetail(
    { segment: { ...makeScene(4), end_frame_image: "end_frames/E1S01.png" }, durationOptions: [8] },
    4,
  );
  await waitFor(() => expect(screen.getByRole("button", { name: /^4 秒/ })).toHaveTextContent("当前分辨率"));
  const pill = screen.getByRole("button", { name: /^4 秒/ });
  expect(pill).toBeDisabled();
  expect(pill).toHaveAccessibleDescription(/当前分辨率/);
});
