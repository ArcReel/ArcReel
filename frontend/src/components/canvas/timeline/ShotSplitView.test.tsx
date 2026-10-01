import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotSplitView } from "./ShotSplitView";
import type { NarrationSegment } from "@/types";
import { makeNarrationSegment } from "@/test/factories";

vi.mock("./ShotList", () => ({
  ShotList: ({ onMove }: { onMove?: (itemId: string, afterId: string | null) => void }) => (
    <button type="button" onClick={() => void onMove?.("E1S03", null)}>
      drag-last-to-front
    </button>
  ),
}));
vi.mock("./ShotDetail", () => ({
  ShotDetail: ({
    segmentId,
    onNext,
    onInsertShot,
    onRemoveShot,
    onMoveShot,
  }: {
    segmentId: string;
    onNext: () => void;
    onMoveShot?: (shotId: string, direction: "earlier" | "later") => void;
    onInsertShot?: (afterId: string) => Promise<boolean>;
    onRemoveShot?: (itemId: string) => Promise<boolean>;
  }) => (
    <div data-testid="detail" data-segment-id={segmentId}>
      <button type="button" onClick={onNext}>next</button>
      <button type="button" onClick={() => void onInsertShot?.(segmentId)}>insert</button>
      <button type="button" onClick={() => void onRemoveShot?.(segmentId)}>remove</button>
      <button type="button" onClick={() => onMoveShot?.(segmentId, "earlier")}>earlier</button>
      <button type="button" onClick={() => onMoveShot?.(segmentId, "later")}>later</button>
    </div>
  ),
}));

const segments = (...ids: string[]): NarrationSegment[] => ids.map((id) => makeNarrationSegment({ segment_id: id }));

function view(items: NarrationSegment[], props: Partial<Parameters<typeof ShotSplitView>[0]> = {}) {
  return (
    <ShotSplitView segments={items} contentMode="narration" aspectRatio="9:16" projectName="demo" {...props} />
  );
}

describe("ShotSplitView 新增 / 移除分镜", () => {
  it("新增成功后选中紧随当前分镜的新分镜", async () => {
    const onInsertShot = vi.fn().mockResolvedValue(true);
    const { rerender } = render(view(segments("E1S01", "E1S02"), { onInsertShot }));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "insert" }));
    });
    rerender(view(segments("E1S01", "E1S03", "E1S02"), { onInsertShot }));

    expect(onInsertShot).toHaveBeenCalledWith("E1S01", undefined);
    expect(screen.getByTestId("detail")).toHaveAttribute("data-segment-id", "E1S03");
  });

  it("新增失败时选中不动", async () => {
    const onInsertShot = vi.fn().mockResolvedValue(false);
    render(view(segments("E1S01", "E1S02"), { onInsertShot }));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "insert" }));
    });

    expect(screen.getByTestId("detail")).toHaveAttribute("data-segment-id", "E1S01");
  });

  it("移除末条分镜后选中夹紧到新的末条", async () => {
    const onRemoveShot = vi.fn().mockResolvedValue(true);
    const { rerender } = render(view(segments("E1S01", "E1S02"), { onRemoveShot }));
    fireEvent.click(screen.getByRole("button", { name: "next" }));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "remove" }));
    });
    rerender(view(segments("E1S01"), { onRemoveShot }));

    expect(onRemoveShot).toHaveBeenCalledWith("E1S02");
    expect(screen.getByTestId("detail")).toHaveAttribute("data-segment-id", "E1S01");
  });
});

describe("ShotSplitView 改序", () => {
  it("拖拽改序成功后选中仍跟随原来的分镜", async () => {
    const onMoveShot = vi.fn().mockResolvedValue(true);
    const { rerender } = render(view(segments("E1S01", "E1S02", "E1S03"), { onMoveShot }));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "drag-last-to-front" }));
    });
    rerender(view(segments("E1S03", "E1S01", "E1S02"), { onMoveShot }));

    expect(onMoveShot).toHaveBeenCalledWith("E1S03", null);
    expect(screen.getByTestId("detail")).toHaveAttribute("data-segment-id", "E1S01");
  });

  it("详情前移第二条即移到最前，后移落到下一条之后", async () => {
    const onMoveShot = vi.fn().mockResolvedValue(true);
    const { rerender } = render(view(segments("E1S01", "E1S02", "E1S03"), { onMoveShot }));
    fireEvent.click(screen.getByRole("button", { name: "next" }));

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "earlier" }));
    });
    rerender(view(segments("E1S02", "E1S01", "E1S03"), { onMoveShot }));
    expect(screen.getByTestId("detail")).toHaveAttribute("data-segment-id", "E1S02");

    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "later" }));
    });

    expect(onMoveShot.mock.calls).toEqual([
      ["E1S02", null],
      ["E1S02", "E1S01"],
    ]);
  });
});
