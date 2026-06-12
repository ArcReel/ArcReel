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
  useTasksStore.setState({ tasks: [] });
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
});
