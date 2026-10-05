import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import type { CharacterDerivativeStatus } from "@/types";
import { CharacterDerivativeRow, type CharacterDerivativeRowProps } from "./CharacterDerivativeRow";

const SHEET_PATH = "characters/derivatives/阿岚/战斗装.png";
const IMAGE_ALT = "衍生「阿岚/战斗装」的资产图";

function renderRow(status: CharacterDerivativeStatus | undefined, overrides: Partial<CharacterDerivativeRowProps> = {}) {
  render(
    <ul>
      <CharacterDerivativeRow
        projectName="demo"
        characterName="阿岚"
        name="战斗装"
        derivative={{ description: status?.description ?? "换上黑色重甲", character_sheet: status?.character_sheet }}
        status={status}
        ownerHasSheet
        readOnly={false}
        busy={false}
        onView={vi.fn()}
        onRenamed={vi.fn()}
        onSheetsChanged={vi.fn()}
        {...overrides}
      />
    </ul>,
  );
}

describe("CharacterDerivativeRow", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
    useProjectsStore.setState({ assetFingerprints: {} });
    vi.restoreAllMocks();
  });

  it("marks the sheet outdated once the base look moved on", () => {
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: true });

    expect(screen.getByAltText(IMAGE_ALT)).toHaveAttribute("src", expect.stringContaining(SHEET_PATH));
    expect(screen.getByText("已过期")).toBeInTheDocument();
  });

  it("refuses to generate before the character has a sheet of its own", () => {
    renderRow({ description: "换上黑色重甲", character_sheet: "", stale: false }, { ownerHasSheet: false });

    expect(screen.getByText("请先生成本体资产图")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成衍生图" })).toBeDisabled();
  });

  it("treats a registered but unreadable derivative image as pending", () => {
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: false, artifact_status: "missing" });

    expect(screen.queryByAltText(IMAGE_ALT)).not.toBeInTheDocument();
    expect(screen.getByText("待生成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成衍生图" })).toBeEnabled();
  });

  it("shows the registered derivative image while its status is still loading", () => {
    renderRow(undefined, { derivative: { description: "换上黑色重甲", character_sheet: SHEET_PATH } });

    expect(screen.getByAltText(IMAGE_ALT)).toHaveAttribute("src", expect.stringContaining(SHEET_PATH));
    expect(screen.queryByText("待生成")).not.toBeInTheDocument();
  });

  it("enqueues a regeneration addressed by the compound resource id after confirming the impact", async () => {
    vi.spyOn(API, "getAssetRegenerationImpact").mockResolvedValue({ stale: true, storyboards: 2, videos: 1, derivatives: 0 });
    const spy = vi
      .spyOn(API, "generateCharacterDerivative")
      .mockResolvedValue({ success: true, task_id: "task-1", deduped: false, message: "已提交" });
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: true });

    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));

    const confirm = await screen.findByRole("alertdialog");
    expect(confirm).toHaveTextContent("2 张分镜图、1 段视频");
    expect(spy).not.toHaveBeenCalled();
    fireEvent.click(within(confirm).getByRole("button", { name: "重新生成" }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith("demo", "阿岚", "战斗装"));
  });

  it("retries the thumbnail once the sheet is replaced", async () => {
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: false });

    fireEvent.error(screen.getByAltText(IMAGE_ALT));
    await waitFor(() => expect(screen.queryByAltText(IMAGE_ALT)).not.toBeInTheDocument());

    act(() => useProjectsStore.getState().updateAssetFingerprints({ [SHEET_PATH]: 99 }));

    await waitFor(() => expect(screen.getByAltText(IMAGE_ALT)).toHaveAttribute("src", expect.stringContaining("v=99")));
  });

  it("rechecks the busy slot at submit time and drops the click", async () => {
    const spy = vi.spyOn(API, "generateCharacterDerivative");
    vi.spyOn(API, "getAssetRegenerationImpact").mockResolvedValue({ stale: false, storyboards: 0, videos: 0, derivatives: 0 });
    renderRow({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: false });
    // 渲染之后本体被另一次生成占用：提交时刻新鲜读复核应当拦下
    useTasksStore.setState({
      tasks: [makeTask({ project_name: "demo", task_type: "character", resource_id: "阿岚" })],
      optimisticActive: new Set(),
    });

    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));

    await waitFor(() => expect(useAppStore.getState().toast?.text).toBe("生成或编辑进行中，暂无法修改衍生"));
    expect(spy).not.toHaveBeenCalled();
  });
});
