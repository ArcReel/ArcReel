import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { AdReferenceUnitsPanel } from "./AdReferenceUnitsPanel";
import { API } from "@/api";
import { useTasksStore } from "@/stores/tasks-store";
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

function makeShot(shotId: string, duration: number): AdShot {
  return {
    shot_id: shotId,
    section: "hook",
    duration_seconds: duration,
    voiceover_text: "口播",
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

function renderPanel() {
  return render(<AdReferenceUnitsPanel projectName="demo" episode={1} shots={SHOTS} />);
}

beforeEach(() => {
  vi.clearAllMocks();
  // 乐观标记由入队动作层写入且跨测试共享同一 store 实例，须一并重置
  useTasksStore.setState({
    tasks: [],
    optimisticActive: new Set(),
    optimisticActiveScriptFile: new Set(),
  });
});

describe("AdReferenceUnitsPanel", () => {
  it("未派生时展示派生入口", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [] });

    renderPanel();

    expect(await screen.findByRole("button", { name: /派生分组/ })).toBeInTheDocument();
    expect(mockedAPI.listAdReferenceUnits).toHaveBeenCalledWith("demo", 1);
  });

  it("点击派生后展示 unit 列表（成员镜头范围与总时长按本地剧本水合）", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [] });
    mockedAPI.deriveAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });

    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /派生分组/ }));

    expect(await screen.findByText("E1U1")).toBeInTheDocument();
    expect(screen.getByText(/E1S1\s*–\s*E1S2/)).toBeInTheDocument();
    expect(screen.getByText(/5s/)).toBeInTheDocument();
  });

  it("逐 unit 生成调用生成 API", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units: [makeUnit()] });
    mockedAPI.generateReferenceVideoUnit.mockResolvedValue({ task_id: "t1", deduped: false });

    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /生成视频/ }));

    await waitFor(() =>
      expect(mockedAPI.generateReferenceVideoUnit).toHaveBeenCalledWith("demo", 1, "E1U1"),
    );
  });

  it("任务进行中时禁用该 unit 的生成按钮", async () => {
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

    renderPanel();

    expect(await screen.findByRole("button", { name: /生成中/ })).toBeDisabled();
  });

  it("已完成的 unit 展示视频链接", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({
      units: [makeUnit({ generated_assets: { video_clip: "reference_videos/E1U1.mp4", status: "completed" } })],
    });

    renderPanel();

    const link = await screen.findByRole("link", { name: /查看视频/ });
    expect(link).toHaveAttribute("href", "http://file/E1U1.mp4");
  });

  it("索引悬空的 unit 提示需重新派生", async () => {
    mockedAPI.listAdReferenceUnits.mockResolvedValue({
      units: [makeUnit({ shot_ids: ["E1S1", "E1S9"] })],
    });

    renderPanel();

    expect(await screen.findByText(/需重新派生/)).toBeInTheDocument();
  });

  it("加载失败展示错误而非空态提示", async () => {
    mockedAPI.listAdReferenceUnits.mockRejectedValue(new Error("加载炸了"));

    renderPanel();

    expect(await screen.findByRole("alert")).toHaveTextContent("加载炸了");
    expect(screen.queryByText(/先派生分组/)).not.toBeInTheDocument();
  });

  it("批量生成时前一 unit 的失败不被后续调用清掉", async () => {
    const units = [makeUnit(), makeUnit({ unit_id: "E1U2", shot_ids: ["E1S2"] })];
    mockedAPI.listAdReferenceUnits.mockResolvedValue({ units });
    mockedAPI.deriveAdReferenceUnits.mockResolvedValue({ units });
    mockedAPI.generateReferenceVideoUnit
      .mockRejectedValueOnce(new Error("U1 入队失败"))
      .mockResolvedValueOnce({ task_id: "t2", deduped: false });

    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /全部生成/ }));

    await waitFor(() => expect(mockedAPI.generateReferenceVideoUnit).toHaveBeenCalledTimes(2));
    expect(await screen.findByRole("alert")).toHaveTextContent("U1 入队失败");
  });

  it("批量生成按实时任务状态跳过已入队的 unit", async () => {
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

    renderPanel();
    await userEvent.click(await screen.findByRole("button", { name: /全部生成/ }));

    await waitFor(() =>
      expect(mockedAPI.generateReferenceVideoUnit).toHaveBeenCalledWith("demo", 1, "E1U2"),
    );
    expect(mockedAPI.generateReferenceVideoUnit).toHaveBeenCalledTimes(1);
  });
});
