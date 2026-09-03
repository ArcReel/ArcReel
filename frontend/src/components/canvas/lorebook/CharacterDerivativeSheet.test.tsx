import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { CharacterDerivativeSheet } from "./CharacterDerivativeSheet";
import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import { useProjectsStore } from "@/stores/projects-store";
import { useTasksStore } from "@/stores/tasks-store";
import { makeTask } from "@/test/factories";
import type { CharacterDerivativeStatus } from "@/types";

const SHEET_PATH = "characters/derivatives/阿岚/战斗装.png";

function renderSheet(
  status: CharacterDerivativeStatus | undefined,
  overrides: Partial<Parameters<typeof CharacterDerivativeSheet>[0]> = {},
) {
  render(
    <CharacterDerivativeSheet
      projectName="demo"
      characterName="阿岚"
      derivativeName="战斗装"
      status={status}
      ownerHasSheet
      {...overrides}
    />,
  );
}

describe("CharacterDerivativeSheet", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  afterEach(() => {
    useTasksStore.setState({ tasks: [], optimisticActive: new Set() });
    useProjectsStore.setState({ assetFingerprints: {} });
    vi.restoreAllMocks();
  });

  it("shows the generated sheet without a staleness badge", () => {
    renderSheet({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: false });

    expect(screen.getByAltText("阿岚/战斗装")).toHaveAttribute(
      "src",
      expect.stringContaining(SHEET_PATH),
    );
    expect(screen.queryByText("已过期")).not.toBeInTheDocument();
  });

  it("marks the sheet outdated once the ontology moved on", () => {
    renderSheet({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: true });

    expect(screen.getByText("已过期")).toBeInTheDocument();
  });

  it("refuses to generate before the ontology has a sheet of its own", () => {
    renderSheet({ description: "换上黑色重甲", character_sheet: "", stale: false }, { ownerHasSheet: false });

    expect(screen.getByText("请先生成本体资产图")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "生成衍生图" })).toBeDisabled();
  });

  it("enqueues a regeneration addressed by the compound resource id", async () => {
    const spy = vi
      .spyOn(API, "generateCharacterDerivative")
      .mockResolvedValue({ success: true, task_id: "task-1", deduped: false, message: "已提交" });
    renderSheet({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: true });

    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));

    await waitFor(() => expect(spy).toHaveBeenCalledWith("demo", "阿岚", "战斗装"));
    expect(useTasksStore.getState().optimisticActive.size).toBeGreaterThan(0);
  });

  it("retries the image once the sheet is replaced", async () => {
    // 加载失败是那一张图的事实：重生成换掉图（指纹变化）之后要重新试，
    // 否则占位内容会一直顶到组件卸载。
    renderSheet({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: false });

    fireEvent.error(screen.getByAltText("阿岚/战斗装"));
    await waitFor(() => expect(screen.getByText("还没有衍生图")).toBeInTheDocument());

    act(() => useProjectsStore.getState().updateAssetFingerprints({ [SHEET_PATH]: 99 }));

    await waitFor(() =>
      expect(screen.getByAltText("阿岚/战斗装")).toHaveAttribute("src", expect.stringContaining("v=99")),
    );
  });

  it("rechecks the busy slot at submit time and drops the click", async () => {
    const spy = vi.spyOn(API, "generateCharacterDerivative");
    renderSheet({ description: "换上黑色重甲", character_sheet: SHEET_PATH, stale: false });
    // 面板停留期间该衍生被另一次生成占用：提交时刻新鲜读复核应当拦下。
    useTasksStore.setState({
      tasks: [
        makeTask({ project_name: "demo", task_type: "character_derivative", resource_id: "阿岚/战斗装" }),
      ],
      optimisticActive: new Set(),
    });

    fireEvent.click(screen.getByRole("button", { name: "重新生成" }));

    await waitFor(() =>
      expect(useAppStore.getState().toast?.text).toBe("生成或编辑进行中，暂无法修改衍生"),
    );
    expect(spy).not.toHaveBeenCalled();
  });
});
