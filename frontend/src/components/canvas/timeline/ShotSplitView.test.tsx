import { act, fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ShotSplitView } from "./ShotSplitView";
import type { NarrationSegment } from "@/types";
import { makeNarrationSegment } from "@/test/factories";

vi.mock("./ShotList", () => ({ ShotList: () => null }));
vi.mock("./ShotDetail", () => ({
  ShotDetail: ({
    segmentId,
    onNext,
    onInsertShot,
    onRemoveShot,
  }: {
    segmentId: string;
    onNext: () => void;
    onInsertShot?: (afterId: string) => Promise<boolean>;
    onRemoveShot?: (itemId: string) => Promise<boolean>;
  }) => (
    <div data-testid="detail" data-segment-id={segmentId}>
      <button type="button" onClick={onNext}>next</button>
      <button type="button" onClick={() => void onInsertShot?.(segmentId)}>insert</button>
      <button type="button" onClick={() => void onRemoveShot?.(segmentId)}>remove</button>
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
