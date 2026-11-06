import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useAssetSheetBatchStore } from "@/stores/asset-sheet-batch-store";
import { useTasksStore } from "@/stores/tasks-store";
import { AssetSheetBatchDialog } from "./AssetSheetBatchDialog";

beforeEach(() => {
  useAppStore.setState(useAppStore.getInitialState(), true);
  useAssetSheetBatchStore.setState({ batches: {}, trackedTaskIds: new Set() });
  useTasksStore.setState({ tasks: [], optimisticActive: new Set(), optimisticActiveScriptFile: new Set() });
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("AssetSheetBatchDialog", () => {
  it("lists targets by type with skips and cost, then submits and tracks the batch", async () => {
    vi.spyOn(API, "previewAssetSheetBatch").mockResolvedValue({
      targets: [
        { unit_id: "character/Alice", asset_type: "character", name: "Alice", derivative: null, depends_on: null },
        {
          unit_id: "character/Alice/战损",
          asset_type: "character",
          name: "Alice",
          derivative: "战损",
          depends_on: "character/Alice",
        },
        { unit_id: "scene/庭院", asset_type: "scene", name: "庭院", derivative: null, depends_on: null },
      ],
      skipped: [
        { unit_id: "character/Bob/雨夜", asset_type: "character", name: "Bob", derivative: "雨夜", reason: "owner_sheet_missing" },
      ],
      estimated_cost: { USD: 0.12 },
      unpriced_count: 0,
    });
    const submit = vi.spyOn(API, "submitAssetSheetBatch").mockResolvedValue({
      batch_id: "b1",
      members: [
        { unit_id: "character/Alice", asset_type: "character", name: "Alice", derivative: null, task_id: "t1", deduped: false, status: "queued" },
        { unit_id: "character/Alice/战损", asset_type: "character", name: "Alice", derivative: "战损", task_id: "t2", deduped: false, status: "queued" },
        { unit_id: "scene/庭院", asset_type: "scene", name: "庭院", derivative: null, task_id: null, deduped: false, status: "failed" },
        { unit_id: "character/Bob/雨夜", asset_type: "character", name: "Bob", derivative: "雨夜", task_id: null, deduped: false, status: "blocked" },
      ],
    });
    const onClose = vi.fn();

    render(<AssetSheetBatchDialog projectName="demo" scope={{ episode_id: 2 }} onClose={onClose} />);

    expect(await screen.findByText("将生成 3 张")).toBeInTheDocument();
    expect(screen.getByText(/等本体「Alice」的资产图生成后提交/)).toBeInTheDocument();
    expect(screen.getByText(/本体资产图未生成/)).toBeInTheDocument();
    expect(screen.getByText("预估费用：$0.12")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "开始生成" }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(submit).toHaveBeenCalledWith("demo", { episode_id: 2 });
    expect(useAssetSheetBatchStore.getState().batches.b1?.members.map((member) => member.task_id)).toEqual(["t1", "t2", null]);
    expect(useAssetSheetBatchStore.getState().trackedTaskIds).toEqual(new Set(["t1", "t2"]));
  });

  it("shows the priced total and how many sheets are unpriced", async () => {
    vi.spyOn(API, "previewAssetSheetBatch").mockResolvedValue({
      targets: [
        { unit_id: "prop/玉佩", asset_type: "prop", name: "玉佩", derivative: null, depends_on: null },
        { unit_id: "prop/折扇", asset_type: "prop", name: "折扇", derivative: null, depends_on: null },
      ],
      skipped: [],
      estimated_cost: { USD: 0.06 },
      unpriced_count: 1,
    });

    render(<AssetSheetBatchDialog projectName="demo" scope={{ asset_type: "prop" }} onClose={vi.fn()} />);

    expect(await screen.findByText("预估费用：$0.06")).toBeInTheDocument();
    expect(screen.getByText("另有 1 项未定价，未计入预估费用")).toBeInTheDocument();
  });

  it("tracks an entirely unqueued failed batch without a success toast", async () => {
    vi.spyOn(API, "previewAssetSheetBatch").mockResolvedValue({
      targets: [{ unit_id: "prop/玉佩", asset_type: "prop", name: "玉佩", derivative: null, depends_on: null }],
      skipped: [],
      estimated_cost: null,
      unpriced_count: 0,
    });
    vi.spyOn(API, "submitAssetSheetBatch").mockResolvedValue({
      batch_id: "failed-batch",
      members: [{ unit_id: "prop/玉佩", asset_type: "prop", name: "玉佩", derivative: null, task_id: null, deduped: false, status: "failed" }],
    });
    render(<AssetSheetBatchDialog projectName="demo" scope={{ asset_type: "prop" }} onClose={vi.fn()} />);
    await screen.findByText("将生成 1 张");
    fireEvent.click(screen.getByRole("button", { name: "开始生成" }));

    await waitFor(() => expect(useAssetSheetBatchStore.getState().batches["failed-batch"]?.members).toHaveLength(1));
    expect(useAppStore.getState().toast).toBeNull();
  });

  it("keeps the confirm disabled when nothing needs generating", async () => {
    vi.spyOn(API, "previewAssetSheetBatch").mockResolvedValue({ targets: [], skipped: [], estimated_cost: null, unpriced_count: 0 });

    render(<AssetSheetBatchDialog projectName="demo" scope={{ asset_type: "prop" }} onClose={vi.fn()} />);

    expect(await screen.findByText("没有需要生成的资产图")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "开始生成" })).toBeDisabled();
  });

  it("目标在打开期间被占用时禁用提交，且不发请求", async () => {
    vi.spyOn(API, "previewAssetSheetBatch").mockResolvedValue({
      targets: [{ unit_id: "character/Alice", asset_type: "character", name: "Alice", derivative: null, depends_on: null }],
      skipped: [],
      estimated_cost: null,
      unpriced_count: 0,
    });
    const submit = vi.spyOn(API, "submitAssetSheetBatch");
    // 另一标签页或 Agent 抢先占用了这张资产图（乐观占用标记即代表「生成或编辑在途」）
    useTasksStore.getState().beginOptimisticActive("demo", "character", "Alice", "asset_sheet");

    render(<AssetSheetBatchDialog projectName="demo" scope={{ asset_type: "character" }} onClose={vi.fn()} />);

    expect(await screen.findByText("将生成 1 张")).toBeInTheDocument();
    const confirm = screen.getByRole("button", { name: "开始生成" });
    await waitFor(() => expect(confirm).toBeDisabled());
    // 禁用状态必须给出原因：按钮点不动，提交时的 toast 永远触发不了。
    expect(screen.getByTestId("asset-sheet-batch-busy-hint")).toHaveTextContent(
      "部分资产正在生成或修改，请等结束后再提交",
    );
    fireEvent.click(confirm);
    expect(submit).not.toHaveBeenCalled();
    expect(useAppStore.getState().toast).toBeNull();
  });
});
