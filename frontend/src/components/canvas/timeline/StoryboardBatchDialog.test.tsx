import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { isResourceBusy } from "@/stores/tasks-store";
import type { WorkflowAdmission } from "@/types";
import { StoryboardBatchDialog } from "./StoryboardBatchDialog";

const BLOCKED: WorkflowAdmission = {
  decision: "blocked",
  operation: "generate_videos",
  selection: "missing_only",
  units: [
    {
      unit_id: "E1S02",
      admitted: false,
      problems: [{ code: "reference_asset_missing", message: "角色「Alice」还没有资产图" }],
    },
    { unit_id: "E1S01", admitted: true, withheld: true, problems: [] },
  ],
};

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("StoryboardBatchDialog", () => {
  it("lists targets, skips with reasons and cost, then submits the storyboard batch", async () => {
    vi.spyOn(API, "previewStoryboardBatch").mockResolvedValue({
      targets: [{ unit_id: "E1S01" }, { unit_id: "E1S02" }],
      skipped: [
        { unit_id: "E1S03", reason: "missing_prompt" },
        { unit_id: "E1S04", reason: "generating" },
      ],
      estimated_cost: { USD: 0.13 },
    });
    const submit = vi.spyOn(API, "submitStoryboardBatch").mockResolvedValue({
      batch_id: "b1",
      task_ids_by_unit: { E1S01: "t1", E1S02: "t2" },
      skipped: [],
      enqueue_failures: [],
    });
    const onClose = vi.fn();

    render(<StoryboardBatchDialog projectName="demo" episode={3} kind="storyboards" onClose={onClose} />);

    expect(await screen.findByText("将生成 2 个分镜")).toBeInTheDocument();
    expect(screen.getByText("跳过 2 个分镜")).toBeInTheDocument();
    expect(screen.getByText(/缺提示词/)).toBeInTheDocument();
    expect(screen.getByText(/生成中/)).toBeInTheDocument();
    expect(screen.getByText("预估费用：$0.13")).toBeInTheDocument();
    expect(screen.getByText(/过期的分镜图不进批量/)).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "开始生成" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(submit).toHaveBeenCalledWith("demo", 3, "storyboards");
    expect(useAppStore.getState().toast?.text).toBe("已提交 2 个分镜图生成任务");
    expect(isResourceBusy("storyboard", "demo", "E1S01")).toBe(true);
  });

  it("states why a blocked video batch is refused and does not allow submitting", async () => {
    vi.spyOn(API, "previewStoryboardBatch").mockResolvedValue({
      targets: [{ unit_id: "E1S01" }, { unit_id: "E1S02" }],
      skipped: [{ unit_id: "E1S03", reason: "missing_storyboard" }],
      estimated_cost: null,
      admission: BLOCKED,
    });
    const submit = vi.spyOn(API, "submitStoryboardBatch");

    render(<StoryboardBatchDialog projectName="demo" episode={1} kind="videos" onClose={vi.fn()} />);

    expect(await screen.findByText(/缺分镜图/)).toBeInTheDocument();
    expect(screen.getByText("角色「Alice」还没有资产图")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始生成" })).toBeDisabled();
    expect(submit).not.toHaveBeenCalled();
  });

  it("keeps the dialog open with the reasons when the video batch is refused on submit", async () => {
    vi.spyOn(API, "previewStoryboardBatch").mockResolvedValue({
      targets: [{ unit_id: "E1S01" }, { unit_id: "E1S02" }],
      skipped: [],
      estimated_cost: { USD: 1.2 },
      admission: { ...BLOCKED, decision: "admitted", units: [] },
    });
    vi.spyOn(API, "submitStoryboardBatch").mockResolvedValue({
      batch_id: null,
      task_ids_by_unit: {},
      skipped: [],
      enqueue_failures: [],
      admission: BLOCKED,
    });
    const onClose = vi.fn();

    render(<StoryboardBatchDialog projectName="demo" episode={1} kind="videos" onClose={onClose} />);
    await screen.findByText("预估费用：$1.20");
    fireEvent.click(screen.getByRole("button", { name: "开始生成" }));

    expect(await screen.findByText("角色「Alice」还没有资产图")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始生成" })).toBeDisabled();
    expect(onClose).not.toHaveBeenCalled();
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("keeps the confirm disabled when nothing needs generating", async () => {
    vi.spyOn(API, "previewStoryboardBatch").mockResolvedValue({ targets: [], skipped: [], estimated_cost: null });

    render(<StoryboardBatchDialog projectName="demo" episode={1} kind="videos" onClose={vi.fn()} />);

    expect(await screen.findByText("本集没有需要生成的分镜")).toBeInTheDocument();
    expect(screen.getByText(/过期的视频不进批量/)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始生成" })).toBeDisabled();
  });
});
