import { fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ShotDetail } from "./ShotDetail";
import { useTasksStore } from "@/stores/tasks-store";
import type { DramaScene } from "@/types";

/**
 * 逐个分镜时长编辑器：候选取经联动约束收窄后的集合，已保存的越界值不静默改写、
 * 按成因给警告并引导重选。
 */

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
    transition_to_next: "cut",
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
      onUpdatePrompt={() => {}}
      durationOptions={[8]}
      {...props}
    />,
  );
}

/** 时长 pill 是唯一带秒数文案的按钮；越界时它带 aria-label 的 ⚠ 兄弟节点。 */
function warningLabel(): string | null {
  return screen.queryByText("⚠")?.getAttribute("aria-label") ?? null;
}

describe("ShotDetail 时长候选与越界提示", () => {
  it("只呈现收窄后的候选，越界的已保存值仍原样显示、不被改写", () => {
    const onUpdatePrompt = vi.fn();
    renderDetail({ onUpdatePrompt });

    // 存值 4 秒照常显示——静默改写会让用户在不知情下丢掉自己的设置
    expect(screen.getByRole("button", { name: /4 秒/ })).toBeInTheDocument();
    expect(onUpdatePrompt).not.toHaveBeenCalled();

    // 展开后只有收窄后的 8 秒可选
    fireEvent.click(screen.getByRole("button", { name: /4 秒/ }));
    const radios = screen.getAllByRole("radio");
    expect(radios).toHaveLength(1);
    expect(radios[0]).toHaveTextContent("8 秒");
  });

  it("重选写回选中的候选值", () => {
    const onUpdatePrompt = vi.fn();
    renderDetail({ onUpdatePrompt });
    fireEvent.click(screen.getByRole("button", { name: /4 秒/ }));
    fireEvent.click(screen.getByRole("radio", { name: /8 秒/ }));
    expect(onUpdatePrompt).toHaveBeenCalledWith("E1S01", "duration_seconds", 8);
  });

  it("候选内的值不告警", () => {
    renderDetail({}, 8);
    expect(warningLabel()).toBeNull();
  });

  // 成因决定提示把用户引向哪里：分辨率 / 参考图两条改对应设置也能解决，
  // 说成「模型不支持」会把用户引去换模型。
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

    // 未接线成因判定的调用点（如未来新增的画布）退回通用文案，而不是显示成 undefined key
    renderDetail();
    expect(warningLabel()).toContain("模型支持范围");
  });
});

/**
 * 占用感知型控件三项检查：在跑的任务已捕获旧时长，此时改时长会让任务产物与剧本存值不一致。
 * 兄弟控件（重生成分镜 / 视频）本就按同一对占用态接线，此处覆盖打开时与提交时两道校验。
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
      expect(pill).toHaveAttribute("title", "该分镜正在生成中，暂不能修改时长");
      unmount();
    }
  });

  it("面板打开后任务才启动时收起面板，且提交被拒不写回", () => {
    const onUpdatePrompt = vi.fn();
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

    // 面板已打开，此刻该分镜的视频任务启动：只查打开时刻的实现会在这里放过写入
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
    // 提交时刻复核的存在理由：prop 反映的是上次渲染，store 更新到重渲染提交之间用户仍可能
    // 点下去。故走 tasks-store 的 isResourceBusy 新鲜读，而不是只看 busy prop。
    const onUpdatePrompt = vi.fn();
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
    expect(onUpdatePrompt).not.toHaveBeenCalled();
  });

  it("同集宫格任务在跑时提交被拒（grid 按 scriptFile 判，归不进分镜粒度）", () => {
    // grid 任务的 resource_id 是 grid_id，isResourceBusy 的分镜粒度判定看不到它；
    // 而切割阶段会覆写本集多个分镜、与改时长并发写同一份剧本。
    const onUpdatePrompt = vi.fn();
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
    expect(onUpdatePrompt).not.toHaveBeenCalled();
  });

  it("prop 还没跟上、但 store 已记录该分镜在跑时，面板打不开", () => {
    // 打开时刻同样要新鲜复核：渲染完成到用户点击之间任务可能才启动，此时 busy prop
    // 还停留在上次渲染，只看它会让面板照常展开。
    const onUpdatePrompt = vi.fn();
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
    // 只派生可见性（open && !locked）会在 locked 回落时让旧面板连同未提交草稿一起回来。
    // 转入锁定态必须真正清掉 open 与 draftSeconds。
    const onUpdatePrompt = vi.fn();
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

    // 任务结束：面板须保持关闭，等用户自己再点开
    rerender(<ShotDetail {...props} />);
    expect(screen.queryByRole("radio")).not.toBeInTheDocument();
    expect(onUpdatePrompt).not.toHaveBeenCalled();
  });
});
