import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdReferenceVideoCanvas, type AdReferenceVideoCanvasProps } from "./AdReferenceVideoCanvas";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useActiveResourceIds, useLatestTasksByResource, useTasksStore } from "@/stores/tasks-store";
import type { AdReferenceUnit, AdShot } from "@/types";

vi.mock("@/api", () => ({
  API: {
    listAdReferenceUnits: vi.fn(),
    deriveAdReferenceUnits: vi.fn(),
    generateReferenceVideoUnit: vi.fn(),
    getFileUrl: vi.fn(() => "http://file/E1U1.mp4"),
  },
}));

const mockedAPI = vi.mocked(API);

// useActiveResourceIds / useLatestTasksByResource 默认包裹真实实现，仅在个别用例里
// 冻结返回值模拟"响应式信号尚未追上真实 store"的场景，验证提交 handler 不依赖它们、
// 独立用 getState() 新鲜读 store。
const mockHolder = vi.hoisted(() => ({
  realActiveResourceIds: undefined as unknown as typeof import("@/stores/tasks-store").useActiveResourceIds,
  realLatestTasksByResource:
    undefined as unknown as typeof import("@/stores/tasks-store").useLatestTasksByResource,
}));
vi.mock("@/stores/tasks-store", async () => {
  const actual = await vi.importActual<typeof import("@/stores/tasks-store")>("@/stores/tasks-store");
  mockHolder.realActiveResourceIds = actual.useActiveResourceIds;
  mockHolder.realLatestTasksByResource = actual.useLatestTasksByResource;
  return {
    ...actual,
    useActiveResourceIds: vi.fn(actual.useActiveResourceIds),
    useLatestTasksByResource: vi.fn(actual.useLatestTasksByResource),
  };
});

function makeShot(shotId: string, duration: number): AdShot {
  return {
    shot_id: shotId,
    section: "hook",
    duration_seconds: duration,
    voiceover_text: `口播 ${shotId}`,
    image_prompt: {
      scene: "画面",
      composition: { shot_type: "Close-up", lighting: "顶光", ambiance: "清爽" },
    },
    video_prompt: { action: "动作", camera_motion: "Static", ambiance_audio: "", dialogue: [] },
    transition_to_next: "cut",
  };
}

function makeUnit(overrides: Partial<AdReferenceUnit> = {}): AdReferenceUnit {
  return {
    unit_id: "E1U1",
    shot_ids: ["E1S1", "E1S2"],
    references: [{ type: "product", name: "按摩仪" }],
    generated_assets: { video_clip: null, status: "pending" },
    ...overrides,
  };
}

const SHOTS = [makeShot("E1S1", 3), makeShot("E1S2", 2)];

function renderCanvas(
  props: {
    shots?: AdShot[];
    hasScript?: boolean;
    onUpdatePrompt?: AdReferenceVideoCanvasProps["onUpdatePrompt"];
    durationOptions?: number[];
  } = {},
) {
  return render(
    <AdReferenceVideoCanvas
      projectName="demo"
      episode={1}
      episodeTitle="广告片"
      shots={props.shots ?? SHOTS}
      hasScript={props.hasScript ?? true}
      scriptFile="episode_1.json"
      onUpdatePrompt={props.onUpdatePrompt}
      durationOptions={props.durationOptions}
    />,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(useActiveResourceIds).mockImplementation(mockHolder.realActiveResourceIds);
  vi.mocked(useLatestTasksByResource).mockImplementation(mockHolder.realLatestTasksByResource);
  // 乐观标记由入队动作层写入且跨测试共享同一 store 实例，须一并重置
  useTasksStore.setState({
    tasks: [],
    optimisticActive: new Set(),
    optimisticActiveScriptFile: new Set(),
  });
  useAppStore.setState(useAppStore.getInitialState(), true);
});

describe("AdReferenceVideoCanvas", () => {
  it("未派生时展示派生入口", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [] });

    renderCanvas();

    expect(await screen.findByRole("button", { name: /派生分组/ })).toBeInTheDocument();
    expect(mockedAPI.listAdReferenceUnits).toHaveBeenCalledWith("demo", 1, {
      signal: expect.any(AbortSignal) as AbortSignal,
    });
  });

  it("剧本未生成时不拉取分组并给出指引", async () => {
    renderCanvas({ shots: [], hasScript: false });

    expect(await screen.findByText(/剧本尚未生成/)).toBeInTheDocument();
    expect(mockedAPI.listAdReferenceUnits).not.toHaveBeenCalled();
  });

  it("点击派生后展示分组卡片（成员镜头与总时长按本地剧本水合）", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [] });
    mockedAPI.deriveAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });

    renderCanvas();
    await userEvent.click(await screen.findByRole("button", { name: /派生分组/ }));

    expect(await screen.findByText("E1U1")).toBeInTheDocument();
    expect(screen.getByText(/E1S1\s*–\s*E1S2/)).toBeInTheDocument();
    // 成员镜头逐条列出，正文取本地剧本口播
    expect(screen.getByText("口播 E1S1")).toBeInTheDocument();
    expect(screen.getByText("口播 E1S2")).toBeInTheDocument();
    // 分组时长 = 成员镜头时长之和
    expect(screen.getAllByText("5s").length).toBeGreaterThan(0);
  });

  it("不提供分镜图与 Image Prompt 等参考直出下不生效的入口", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });

    renderCanvas();
    await screen.findByText("E1U1");

    expect(screen.queryByRole("button", { name: /生成分镜|上传/ })).not.toBeInTheDocument();
    expect(screen.queryByLabelText(/Image Prompt/i)).not.toBeInTheDocument();
  });

  it("逐分组生成调用生成 API", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    mockedAPI.generateReferenceVideoUnit.mockResolvedValue({ task_id: "t1", deduped: false });

    renderCanvas();
    await userEvent.click(await screen.findByRole("button", { name: /生成视频/ }));

    await waitFor(() =>
      expect(mockedAPI.generateReferenceVideoUnit).toHaveBeenCalledWith("demo", 1, "E1U1"),
    );
  });

  it("任务进行中时禁用该分组的生成按钮", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    useTasksStore.setState({
      tasks: [
        {
          task_id: "t1",
          project_name: "demo",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "running",
          updated_at: "2026-06-12T10:00:00Z",
        },
      ] as never,
    });

    renderCanvas();

    expect(await screen.findByRole("button", { name: /生成中/ })).toBeDisabled();
  });

  it("已完成的分组展示成片预览与视频链接", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({
      units: [
        makeUnit({
          generated_assets: { video_clip: "reference_videos/E1U1.mp4", status: "completed" },
        }),
      ],
    });

    renderCanvas();

    const link = await screen.findByRole("link", { name: /查看视频/ });
    expect(link).toHaveAttribute("href", "http://file/E1U1.mp4");
    expect(screen.getByLabelText(/分组 E1U1 的成片/)).toBeInTheDocument();
    // 已有成片时主按钮转为重新生成
    expect(screen.getByRole("button", { name: /重新生成/ })).toBeInTheDocument();
  });

  it("任务失败时展示失败原因并允许重试", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    useTasksStore.setState({
      tasks: [
        {
          task_id: "t1",
          project_name: "demo",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "failed",
          error_message: "供应商拒绝",
          updated_at: "2026-06-12T10:00:00Z",
        },
      ] as never,
    });

    renderCanvas();

    expect(await screen.findByText("供应商拒绝")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重试生成/ })).toBeInTheDocument();
  });

  it("重试失败分组后在真实任务行落库前即禁用按钮", async () => {
    // 旧失败行始终在，status 的乐观分支不生效；禁用须直接取占用集，
    // 否则入队到任务行落库之间可重复点击。
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    mockedAPI.generateReferenceVideoUnit.mockResolvedValue({
      task_id: "t2",
      deduped: false,
    } as never);
    useTasksStore.setState({
      tasks: [
        {
          task_id: "t1",
          project_name: "demo",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "failed",
          error_message: "供应商拒绝",
          updated_at: "2026-06-12T10:00:00Z",
        },
      ] as never,
    });

    renderCanvas();

    const retry = await screen.findByRole("button", { name: /重试生成/ });
    await userEvent.click(retry);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /生成中/ })).toBeDisabled();
    });
  });

  it("重新生成已完成分组后在真实任务行落库前即禁用按钮", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({
      units: [
        makeUnit({ generated_assets: { video_clip: "videos/E1U1.mp4", status: "completed" } }),
      ],
    });
    mockedAPI.generateReferenceVideoUnit.mockResolvedValue({
      task_id: "t3",
      deduped: false,
    } as never);
    // 已成功的历史任务行：queueRow 非空使 status 的乐观分支失效，
    // 重新生成的乐观窗口只能靠占用集兜住
    useTasksStore.setState({
      tasks: [
        {
          task_id: "t1",
          project_name: "demo",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "succeeded",
          updated_at: "2026-06-12T10:00:00Z",
        },
      ] as never,
    });

    renderCanvas();

    const regenerate = await screen.findByRole("button", { name: /重新生成/ });
    await userEvent.click(regenerate);

    await waitFor(() => {
      expect(screen.getByRole("button", { name: /生成中/ })).toBeDisabled();
    });
  });

  it("索引悬空的分组提示需重新派生并禁用生成", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({
      units: [makeUnit({ shot_ids: ["E1S1", "E1S9"] })],
    });

    renderCanvas();

    expect(await screen.findByText(/需重新派生/)).toBeInTheDocument();
    expect(screen.getByText(/镜头已删除/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /生成视频/ })).toBeDisabled();
  });

  it("加载失败展示错误而非空态提示", async () => {
    mockedAPI.listAdReferenceUnits.mockRejectedValue(new Error("加载炸了"));

    renderCanvas();

    expect(await screen.findByRole("alert")).toHaveTextContent("加载炸了");
    expect(screen.queryByText(/先派生分组/)).not.toBeInTheDocument();
  });

  it("批量生成时前一分组的失败不被后续调用清掉", async () => {
    const units = [makeUnit(), makeUnit({ unit_id: "E1U2", shot_ids: ["E1S2"] })];
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units });
    mockedAPI.deriveAdReferenceUnits.mockResolvedValue({ units });
    mockedAPI.generateReferenceVideoUnit
      .mockRejectedValueOnce(new Error("U1 入队失败"))
      .mockResolvedValueOnce({ task_id: "t2", deduped: false });

    renderCanvas();
    await userEvent.click(await screen.findByRole("button", { name: /全部生成/ }));

    await waitFor(() => expect(mockedAPI.generateReferenceVideoUnit).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("alert")).toHaveTextContent("U1 入队失败");
  });

  it("重新生成已完成分组失败后展示失败原因而非仍报已完成", async () => {
    // 最新任务行落库为 failed 时必须信任它——旧的 !clip 判定会被已有成片盖成 ready，
    // 隐藏这次重新生成的失败原因。
    mockedAPI.listAdReferenceUnits.mockResolvedValue({
      units: [makeUnit({ generated_assets: { video_clip: "videos/E1U1.mp4", status: "completed" } })],
    });
    useTasksStore.setState({
      tasks: [
        {
          task_id: "t2",
          project_name: "demo",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "failed",
          error_message: "重新生成失败",
          updated_at: "2026-06-12T11:00:00Z",
        },
      ] as never,
    });

    renderCanvas();

    expect(await screen.findByText("重新生成失败")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重试生成/ })).toBeInTheDocument();
  });

  it("首次分组加载完成前禁用派生入口，避免派生结果被迟到的旧列表覆盖", async () => {
    let resolveList!: (v: { units: AdReferenceUnit[] }) => void;
    mockedAPI.listAdReferenceUnits.mockReturnValue(
      new Promise((resolve) => {
        resolveList = resolve;
      }),
    );

    renderCanvas();

    const deriveButton = await screen.findByRole("button", { name: /派生分组/ });
    expect(deriveButton).toBeDisabled();

    resolveList({ units: [] });
    await waitFor(() => expect(deriveButton).not.toBeDisabled());
  });

  it("首次加载失败后不永久禁用派生入口，可点击重试", async () => {
    mockedAPI.listAdReferenceUnits.mockRejectedValue(new Error("加载炸了"));

    renderCanvas();

    await screen.findByRole("alert");
    expect(await screen.findByRole("button", { name: /派生分组/ })).not.toBeDisabled();
  });

  it("分组仍有任务运行时禁用重新派生，避免任务完成后把成片挂到重派生后的新分组", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    useTasksStore.setState({
      tasks: [
        {
          task_id: "t1",
          project_name: "demo",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "running",
          updated_at: "2026-06-12T10:00:00Z",
        },
      ] as never,
    });

    renderCanvas();

    expect(await screen.findByRole("button", { name: /重新派生/ })).toBeDisabled();
  });

  it("响应式占用信号尚未追上真实 store 时，生成提交仍被 getState() 新鲜读拦截", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    // 冻结两个 reactive hook 的返回值——模拟卡片渲染期捕获的 busy/status 未能
    // 反映随后落库的真实任务行，只有 getState() 新鲜读才能看见它。
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    renderCanvas();

    const generateBtn = await screen.findByRole("button", { name: /生成视频/ });
    expect(generateBtn).not.toBeDisabled();

    // 渲染之后、点击之前，另一入口已把该 unit 占用——响应式 busy 仍是渲染期的旧值
    useTasksStore.setState({
      tasks: [
        {
          task_id: "t1",
          project_name: "demo",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "running",
          updated_at: "2026-06-12T10:00:00Z",
        },
      ] as never,
    });

    await userEvent.click(generateBtn);

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("该分组正在生成中，请稍后再试", "error");
    });
    expect(mockedAPI.generateReferenceVideoUnit).not.toHaveBeenCalled();
  });

  it("响应式占用信号尚未追上真实 store 时，重新派生提交仍被 getState() 新鲜读拦截", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    renderCanvas();

    const rederiveBtn = await screen.findByRole("button", { name: /重新派生/ });
    expect(rederiveBtn).not.toBeDisabled();

    useTasksStore.setState({
      tasks: [
        {
          task_id: "t1",
          project_name: "demo",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "running",
          updated_at: "2026-06-12T10:00:00Z",
        },
      ] as never,
    });

    await userEvent.click(rederiveBtn);

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("有分组正在生成中，暂不可派生", "error");
    });
    expect(mockedAPI.deriveAdReferenceUnits).not.toHaveBeenCalled();
  });

  it("任务取消中时不展示为生成中，按钮维持禁用", async () => {
    // busy（占用谓词）计入 cancelling，但卡片不应把取消中误报成生成中——
    // 展示层需沿用取消前的既有状态（此处为已完成）。
    mockedAPI.listAdReferenceUnits.mockResolvedValue({
      units: [makeUnit({ generated_assets: { video_clip: "videos/E1U1.mp4", status: "completed" } })],
    });
    useTasksStore.setState({
      tasks: [
        {
          task_id: "t1",
          project_name: "demo",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "cancelling",
          updated_at: "2026-06-12T10:00:00Z",
        },
      ] as never,
    });

    renderCanvas();

    const regenerate = await screen.findByRole("button", { name: /重新生成/ });
    expect(regenerate).toBeDisabled();
    expect(screen.queryByRole("button", { name: /生成中/ })).not.toBeInTheDocument();
  });

  it("批量生成按实时任务状态跳过已入队的分组", async () => {
    const units = [makeUnit(), makeUnit({ unit_id: "E1U2", shot_ids: ["E1S2"] })];
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units });
    mockedAPI.deriveAdReferenceUnits.mockImplementation(async () => {
      // 派生期间另一入口已把 E1U1 入队（模拟批量循环开始前 store 更新）
      useTasksStore.setState({
        tasks: [
          {
            task_id: "t1",
            project_name: "demo",
            task_type: "reference_video",
            resource_id: "E1U1",
            status: "queued",
            updated_at: "2026-06-12T10:00:00Z",
          },
        ] as never,
      });
      return { units };
    });
    mockedAPI.generateReferenceVideoUnit.mockResolvedValue({ task_id: "t2", deduped: false });

    renderCanvas();
    await userEvent.click(await screen.findByRole("button", { name: /全部生成/ }));

    await waitFor(() =>
      expect(mockedAPI.generateReferenceVideoUnit).toHaveBeenCalledWith("demo", 1, "E1U2"),
    );
    expect(mockedAPI.generateReferenceVideoUnit).toHaveBeenCalledTimes(1);
  });

  it("未传 onUpdatePrompt（只读）时镜头明细不渲染编辑控件", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });

    renderCanvas();
    await screen.findByText("E1U1");

    expect(screen.queryByRole("combobox", { name: /E1S1 时长/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("textbox", { name: /E1S1 口播文案/ })).not.toBeInTheDocument();
  });

  it("传入 onUpdatePrompt 时可编辑镜头时长（即选即提交）与口播文案（失焦提交）", async () => {
    const onUpdatePrompt = vi.fn();
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });

    renderCanvas({ onUpdatePrompt, durationOptions: [3, 5, 7] });
    await screen.findByText("E1U1");

    const durationSelect = await screen.findByRole("combobox", { name: /E1S1 时长/ });
    await userEvent.selectOptions(durationSelect, "7");
    expect(onUpdatePrompt).toHaveBeenCalledWith(
      "E1S1",
      { duration_seconds: 7 },
      undefined,
      "episode_1.json",
    );

    const voiceoverInput = screen.getByRole("textbox", { name: /E1S1 口播文案/ });
    await userEvent.clear(voiceoverInput);
    await userEvent.type(voiceoverInput, "新文案");
    await userEvent.tab();
    expect(onUpdatePrompt).toHaveBeenCalledWith(
      "E1S1",
      { voiceover_text: "新文案" },
      undefined,
      "episode_1.json",
    );
  });

  it("已生成的分组展示需重新生成才能生效的提示", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({
      units: [makeUnit({ generated_assets: { video_clip: "reference_videos/E1U1.mp4", status: "completed" } })],
    });

    renderCanvas({ onUpdatePrompt: vi.fn() });

    expect(await screen.findByText(/该分组已生成.*需重新生成才能生效/)).toBeInTheDocument();
  });

  it("生成中的分组镜头编辑控件禁用", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    useTasksStore.setState({
      tasks: [
        {
          task_id: "t1",
          project_name: "demo",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "running",
          updated_at: "2026-06-12T10:00:00Z",
        },
      ] as never,
    });

    renderCanvas({ onUpdatePrompt: vi.fn() });

    expect(await screen.findByRole("combobox", { name: /E1S1 时长/ })).toBeDisabled();
    expect(screen.getByRole("textbox", { name: /E1S1 口播文案/ })).toBeDisabled();
  });

  it("响应式占用信号尚未追上真实 store 时，镜头编辑提交仍被 getState() 新鲜读拦截", async () => {
    const onUpdatePrompt = vi.fn();
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    renderCanvas({ onUpdatePrompt, durationOptions: [3, 7] });
    const durationSelect = await screen.findByRole("combobox", { name: /E1S1 时长/ });
    expect(durationSelect).not.toBeDisabled();

    // 渲染之后、提交之前，另一入口（如批量生成）已把该 unit 占用
    useTasksStore.setState({
      tasks: [
        {
          task_id: "t1",
          project_name: "demo",
          task_type: "reference_video",
          resource_id: "E1U1",
          status: "running",
          updated_at: "2026-06-12T10:00:00Z",
        },
      ] as never,
    });

    await userEvent.selectOptions(durationSelect, "7");

    await waitFor(() => {
      expect(pushToast).toHaveBeenCalledWith("该分组正在生成中，请稍后再试", "error");
    });
    expect(onUpdatePrompt).not.toHaveBeenCalled();
  });
});
