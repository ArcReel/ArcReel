import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdReferenceVideoCanvas, type AdReferenceVideoCanvasProps } from "./AdReferenceVideoCanvas";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useActiveResourceIds, useLatestTasksByResource, useTasksStore } from "@/stores/tasks-store";
import type { AdReferenceUnit, AdShot, ProjectData } from "@/types";

vi.mock("@/api", () => ({
  API: {
    listAdReferenceUnits: vi.fn(),
    deriveAdReferenceUnits: vi.fn(),
    generateReferenceVideoUnit: vi.fn(),
    precheckReferenceVideoDuration: vi.fn(),
    updateCharacter: vi.fn(),
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

const STUB_PROJECT: ProjectData = {
  title: "p",
  content_mode: "ad",
  style: "",
  episodes: [],
  characters: {},
  scenes: {},
  props: {},
};

function renderCanvas(
  props: {
    shots?: AdShot[];
    hasScript?: boolean;
    onUpdatePrompt?: AdReferenceVideoCanvasProps["onUpdatePrompt"];
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
  useProjectsStore.setState({ currentProjectName: "demo", currentProjectData: STUB_PROJECT });
  // 时长取档预检默认「与剧本编排一致」：生成入口无确认步骤，与改动前的路径等价
  mockedAPI.precheckReferenceVideoDuration.mockResolvedValue({
    needs_confirmation: false,
    script_duration: 5,
    request_duration: 5,
    adjustment: "exact",
  });
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

  // ad 与通用路径共用同一条时长闸门：分组求和落在档位之间时也要先确认，
  // 否则 ad 入口会成为绕过确认的旁路。
  it("分组时长与剧本编排不一致时先确认，确认后才入队", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    mockedAPI.generateReferenceVideoUnit.mockResolvedValue({ task_id: "t1", deduped: false });
    mockedAPI.precheckReferenceVideoDuration.mockResolvedValue({
      needs_confirmation: true,
      script_duration: 5,
      request_duration: 8,
      adjustment: "up",
    });

    renderCanvas();
    await userEvent.click(await screen.findByRole("button", { name: /生成视频/ }));

    const confirm = await screen.findByRole("button", { name: /按此时长生成/ });
    expect(mockedAPI.generateReferenceVideoUnit).not.toHaveBeenCalled();

    await userEvent.click(confirm);
    await waitFor(() =>
      expect(mockedAPI.generateReferenceVideoUnit).toHaveBeenCalledWith("demo", 1, "E1U1"),
    );
  });

  // 批量入口整批走一次闸门：逐个走会让后一个分组覆盖前一个尚未确认的弹窗，
  // 除最后一个外的分组既不入队也无提示。
  it("全部生成时多个分组聚合成一次确认，确认后全部入队", async () => {
    const units = [makeUnit(), makeUnit({ unit_id: "E1U2", shot_ids: ["E1S1"] })];
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units });
    mockedAPI.deriveAdReferenceUnits.mockResolvedValue({ units });
    mockedAPI.generateReferenceVideoUnit.mockResolvedValue({ task_id: "t1", deduped: false });
    mockedAPI.precheckReferenceVideoDuration.mockResolvedValue({
      needs_confirmation: true,
      script_duration: 5,
      request_duration: 8,
      adjustment: "up",
    });

    renderCanvas();
    await screen.findByText("E1U1");
    await userEvent.click(screen.getByRole("button", { name: /全部生成/ }));

    // 两个分组列在同一个确认框里，而不是弹两次或只剩最后一个
    const confirm = await screen.findByRole("button", { name: /按此时长生成/ });
    const dialog = confirm.closest('[role="dialog"]') ?? confirm.parentElement!;
    expect(dialog.textContent).toContain("E1U1");
    expect(dialog.textContent).toContain("E1U2");
    expect(mockedAPI.generateReferenceVideoUnit).not.toHaveBeenCalled();

    await userEvent.click(confirm);
    await waitFor(() => expect(mockedAPI.generateReferenceVideoUnit).toHaveBeenCalledTimes(2));
    expect(mockedAPI.generateReferenceVideoUnit).toHaveBeenCalledWith("demo", 1, "E1U1");
    expect(mockedAPI.generateReferenceVideoUnit).toHaveBeenCalledWith("demo", 1, "E1U2");
  });

  it("全部生成时取消确认则一个都不入队", async () => {
    const units = [makeUnit(), makeUnit({ unit_id: "E1U2", shot_ids: ["E1S1"] })];
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units });
    mockedAPI.deriveAdReferenceUnits.mockResolvedValue({ units });
    mockedAPI.generateReferenceVideoUnit.mockResolvedValue({ task_id: "t1", deduped: false });
    mockedAPI.precheckReferenceVideoDuration.mockResolvedValue({
      needs_confirmation: true,
      script_duration: 5,
      request_duration: 8,
      adjustment: "up",
    });

    renderCanvas();
    await screen.findByText("E1U1");
    await userEvent.click(screen.getByRole("button", { name: /全部生成/ }));

    await screen.findByRole("button", { name: /按此时长生成/ });
    await userEvent.click(screen.getByRole("button", { name: /取消/ }));

    expect(mockedAPI.generateReferenceVideoUnit).not.toHaveBeenCalled();
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

  it("stale 分组展示剧本已变更角标且生成入口保持可用", async () => {
    // 与索引悬空不同：stale 只是产物落后于剧本编排的提示，重新生成正是建议动作，不禁用
    mockedAPI.listAdReferenceUnits.mockResolvedValue({
      units: [
        makeUnit({
          stale: true,
          generated_assets: { video_clip: "reference_videos/E1U1.mp4", status: "completed" },
        }),
      ],
    });

    renderCanvas();

    expect(await screen.findByText(/剧本已变更，建议重新生成/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /重新生成/ })).toBeEnabled();
  });

  it("非 stale 分组不展示剧本已变更角标", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({
      units: [makeUnit({ generated_assets: { video_clip: "reference_videos/E1U1.mp4", status: "completed" } })],
    });

    renderCanvas();

    await screen.findByRole("button", { name: /重新生成/ });
    expect(screen.queryByText(/剧本已变更，建议重新生成/)).not.toBeInTheDocument();
  });

  it("加载失败展示错误而非空态提示", async () => {
    mockedAPI.listAdReferenceUnits.mockRejectedValue(new Error("加载炸了"));

    renderCanvas();

    expect(await screen.findByRole("alert")).toHaveTextContent("加载炸了");
    expect(screen.queryByText(/先派生分组/)).not.toBeInTheDocument();
  });

  it("首次加载失败后，任务完成触发的重拉成功即清掉旧错误", async () => {
    mockedAPI.listAdReferenceUnits.mockRejectedValueOnce(new Error("加载炸了"));
    renderCanvas();
    expect(await screen.findByRole("alert")).toHaveTextContent("加载炸了");

    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    act(() => {
      useAppStore.getState().invalidateReferenceVideoUnits();
    });

    await waitFor(() => expect(screen.queryByRole("alert")).not.toBeInTheDocument());
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

  it("重新派生不被终态重拉的迟到旧列表覆盖", async () => {
    // 任务完成触发的重拉在途、且任务已不占用（派生入口因此可点）时用户重新派生：那次 GET
    // 读的是派生之前的分组，迟到写回会把刚派生出的新分组撤销。
    mockedAPI.listAdReferenceUnits.mockResolvedValueOnce({ units: [makeUnit()] });
    renderCanvas();
    await screen.findByText(/E1U1/);

    let resolveStale!: (v: { units: AdReferenceUnit[] }) => void;
    mockedAPI.listAdReferenceUnits.mockReturnValueOnce(
      new Promise((resolve) => {
        resolveStale = resolve;
      }),
    );
    act(() => {
      useAppStore.getState().invalidateReferenceVideoUnits();
    });

    const derived = [makeUnit({ unit_id: "E1U7", shot_ids: ["E1S1"] })];
    mockedAPI.deriveAdReferenceUnits.mockResolvedValueOnce({ units: derived });
    await userEvent.click(await screen.findByRole("button", { name: /重新派生/ }));
    await screen.findByText(/E1U7/);

    // 迟到的旧列表落定：新分组必须留在界面上。
    await act(async () => {
      resolveStale({ units: [makeUnit()] });
    });
    expect(screen.getByText(/E1U7/)).toBeInTheDocument();
    expect(screen.queryByText(/E1U1/)).not.toBeInTheDocument();
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

  // 派生会按位置重算 unit_id 的成员镜头；镜头字段写入未落库时落库顺序与派生顺序不确定，
  // 与「任务运行中禁止重新派生」（上一条用例）同一类风险，禁用条件与提交时复核须一并覆盖。
  it("镜头字段写入未落库期间禁用重新派生，避免与 PATCH 落库顺序不确定", async () => {
    let resolveUpdate: (committed: boolean) => void = () => {};
    const onUpdatePrompt = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          resolveUpdate = resolve;
        }),
    );
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    mockedAPI.deriveAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });

    renderCanvas({ onUpdatePrompt });
    const durationSelect = await screen.findByRole("combobox", { name: /E1S1 时长/ });
    const rederiveButton = await screen.findByRole("button", { name: /重新派生/ });
    expect(rederiveButton).not.toBeDisabled();

    await userEvent.selectOptions(durationSelect, "7");

    await waitFor(() => {
      expect(rederiveButton).toBeDisabled();
    });

    resolveUpdate(true);

    await waitFor(() => {
      expect(rederiveButton).not.toBeDisabled();
    });
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

    renderCanvas({ onUpdatePrompt });
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

  it("镜头时长选项是 1-15 自由整数，不取供应商 supported_durations", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });

    renderCanvas({ onUpdatePrompt: vi.fn() });

    const durationSelect = await screen.findByRole("combobox", { name: /E1S1 时长/ });
    const values = Array.from(durationSelect.querySelectorAll("option")).map((o) => o.value);
    expect(values).toEqual(Array.from({ length: 15 }, (_, i) => String(i + 1)));
  });

  it("剧本里的区间外时长仍如实回显，不被显示成首个选项", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit({ shot_ids: ["E1S1"] })] });

    renderCanvas({ onUpdatePrompt: vi.fn(), shots: [makeShot("E1S1", 20)] });

    const durationSelect = await screen.findByRole("combobox", { name: /E1S1 时长/ });
    expect((durationSelect as HTMLSelectElement).value).toBe("20");
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

  // 反向同理于「写入未落库期间禁用重新派生」：派生会按位置重算 unit_id 的成员镜头，
  // 派生进行中若仍接受新的镜头字段写入，落库顺序与派生顺序不确定。
  it("重新派生进行中时禁用镜头编辑控件", async () => {
    let resolveDerive: (result: { units: AdReferenceUnit[] }) => void = () => {};
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    mockedAPI.deriveAdReferenceUnits.mockReturnValue(
      new Promise((resolve) => {
        resolveDerive = resolve;
      }),
    );

    renderCanvas({ onUpdatePrompt: vi.fn() });
    const durationSelect = await screen.findByRole("combobox", { name: /E1S1 时长/ });
    const voiceoverInput = screen.getByRole("textbox", { name: /E1S1 口播文案/ });
    expect(durationSelect).not.toBeDisabled();

    await userEvent.click(screen.getByRole("button", { name: /重新派生/ }));

    await waitFor(() => {
      expect(durationSelect).toBeDisabled();
    });
    expect(voiceoverInput).toBeDisabled();

    resolveDerive({ units: [makeUnit()] });

    await waitFor(() => {
      expect(durationSelect).not.toBeDisabled();
    });
  });

  it("响应式占用信号尚未追上真实 store 时，镜头编辑提交仍被 getState() 新鲜读拦截", async () => {
    const onUpdatePrompt = vi.fn();
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    const pushToast = vi.spyOn(useAppStore.getState(), "pushToast");
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    renderCanvas({ onUpdatePrompt });
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

  it("占用拦截时保留用户已输入的口播草稿，不静默清空", async () => {
    const onUpdatePrompt = vi.fn();
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    vi.mocked(useActiveResourceIds).mockReturnValue(new Set());
    vi.mocked(useLatestTasksByResource).mockReturnValue(new Map());

    renderCanvas({ onUpdatePrompt });
    const voiceoverInput = await screen.findByRole("textbox", { name: /E1S1 口播文案/ });
    expect(voiceoverInput).not.toBeDisabled();

    await userEvent.clear(voiceoverInput);
    await userEvent.type(voiceoverInput, "被占用时的草稿");

    // 打字之后、失焦提交之前，另一入口（如批量生成）已把该 unit 占用
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

    await userEvent.tab();

    await waitFor(() => {
      expect(onUpdatePrompt).not.toHaveBeenCalled();
    });
    expect(voiceoverInput).toHaveValue("被占用时的草稿");
  });

  // onUpdatePrompt 契约：内部吞掉异常并转 toast，失败时以返回 false（而非抛出）通知调用方——
  // 不能靠「未抛出」推断已持久化，否则失败的 PATCH 也会被当作成功清空草稿。
  it("写入失败（onUpdatePrompt 返回 false）时保留口播草稿，不当作已提交清空", async () => {
    const onUpdatePrompt = vi.fn().mockResolvedValue(false);
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });

    renderCanvas({ onUpdatePrompt });
    const voiceoverInput = await screen.findByRole("textbox", { name: /E1S1 口播文案/ });

    await userEvent.clear(voiceoverInput);
    await userEvent.type(voiceoverInput, "写入失败时的草稿");
    await userEvent.tab();

    await waitFor(() => {
      expect(onUpdatePrompt).toHaveBeenCalled();
    });
    expect(voiceoverInput).toHaveValue("写入失败时的草稿");
  });

  it("镜头字段写入未落库期间禁用同分组生成入口，避免与写入并发乱序", async () => {
    let resolveUpdate: (committed: boolean) => void = () => {};
    const onUpdatePrompt = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          resolveUpdate = resolve;
        }),
    );
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });

    renderCanvas({ onUpdatePrompt });
    const durationSelect = await screen.findByRole("combobox", { name: /E1S1 时长/ });
    const generateButton = await screen.findByRole("button", { name: /生成视频/ });
    expect(generateButton).not.toBeDisabled();

    await userEvent.selectOptions(durationSelect, "7");

    await waitFor(() => {
      expect(generateButton).toBeDisabled();
    });

    resolveUpdate(true);

    await waitFor(() => {
      expect(generateButton).not.toBeDisabled();
    });
  });

  // 只禁用生成入口不够：写入未落库期间若编辑控件仍可用，用户能重新聚焦输入新草稿，
  // 先前请求异步落地时的清空逻辑会把这份更新的草稿一并覆盖丢失。
  it("镜头字段写入未落库期间禁用同分组的编辑控件本身", async () => {
    let resolveUpdate: (committed: boolean) => void = () => {};
    const onUpdatePrompt = vi.fn(
      () =>
        new Promise<boolean>((resolve) => {
          resolveUpdate = resolve;
        }),
    );
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });

    renderCanvas({ onUpdatePrompt });
    const durationSelect = await screen.findByRole("combobox", { name: /E1S1 时长/ });
    const voiceoverInput = screen.getByRole("textbox", { name: /E1S1 口播文案/ });
    expect(voiceoverInput).not.toBeDisabled();

    await userEvent.selectOptions(durationSelect, "7");

    await waitFor(() => {
      expect(voiceoverInput).toBeDisabled();
    });
    expect(durationSelect).toBeDisabled();

    resolveUpdate(true);

    await waitFor(() => {
      expect(voiceoverInput).not.toBeDisabled();
    });
  });

  it("参考生视频分组失效信号自增时重拉分组，展示新落地的成片", async () => {
    // 生成完成的任务终态经项目事件 SSE 推来 → invalidateReferenceVideoUnits →
    // 本画布重拉分组，用户无需手动重新派生/刷新即可看到成片。
    mockedAPI.listAdReferenceUnits.mockResolvedValueOnce({ units: [makeUnit()] });

    renderCanvas();
    await waitFor(() => {
      expect(mockedAPI.listAdReferenceUnits).toHaveBeenCalledTimes(1);
    });

    mockedAPI.listAdReferenceUnits.mockResolvedValueOnce({
      units: [makeUnit({ generated_assets: { video_clip: "videos/E1U1.mp4", status: "completed" } })],
    });
    act(() => {
      useAppStore.getState().invalidateReferenceVideoUnits();
    });

    await waitFor(() => {
      expect(mockedAPI.listAdReferenceUnits).toHaveBeenCalledTimes(2);
    });
  });

  describe("存量声音过渡横幅", () => {
    // ad + reference_video 的完成态 unit 经同一个 apply_unit_video_assets 落盘点戳
    // video_generated_at，与 narration/drama 画布（ReferenceVideoCanvas）共用同一套
    // 判定逻辑（computeVoiceLegacyNotice），横幅不应只挂在其中一个画布上。
    const VOICE_UPDATED_AT = "2026-06-01T00:00:00+00:00";

    function staleUnit(): AdReferenceUnit {
      return makeUnit({
        references: [{ type: "character", name: "王" }],
        generated_assets: { video_clip: "videos/E1U1.mp4", status: "completed", video_generated_at: null },
      });
    }

    function mountWithStaleClip() {
      useProjectsStore.setState({
        currentProjectName: "demo",
        currentProjectData: {
          ...STUB_PROJECT,
          characters: { 王: { description: "", voice_updated_at: VOICE_UPDATED_AT } },
        },
      });
      mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [staleUnit()] });
      renderCanvas();
    }

    it("展示存量过渡横幅", async () => {
      mountWithStaleClip();
      expect(await screen.findByRole("button", { name: /Got it|知道了/ })).toBeInTheDocument();
    });

    it("关闭时写回角色自己的 voice_updated_at，而不是本机当前时间", async () => {
      const patch = vi.mocked(API.updateCharacter).mockResolvedValue({} as never);
      mountWithStaleClip();

      const dismiss = await screen.findByRole("button", { name: /Got it|知道了/ });
      await userEvent.click(dismiss);

      await waitFor(() =>
        expect(patch).toHaveBeenCalledWith("demo", "王", { voice_notice_dismissed_at: VOICE_UPDATED_AT }),
      );
    });

    it("关闭请求失败时提示用户，不静默留下未关闭的横幅", async () => {
      vi.mocked(API.updateCharacter).mockRejectedValue(new Error("boom"));
      mountWithStaleClip();

      const dismiss = await screen.findByRole("button", { name: /Got it|知道了/ });
      await userEvent.click(dismiss);

      await waitFor(() => expect(useAppStore.getState().toast?.tone).toBe("error"));
    });
  });
});
