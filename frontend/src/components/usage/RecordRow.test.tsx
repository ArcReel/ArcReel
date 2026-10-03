import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { RecordRow } from "./RecordRow";
import { makeUsageRecord } from "./usage-fixtures";
import { usageRecordToView } from "./usage-record-view";

const providerLabel = (provider: string | null) =>
  provider === "google" ? "Google" : (provider ?? "—");

describe("RecordRow compact layout", () => {
  it("stacks target and model and opens the detail from anywhere on the row", async () => {
    const onOpenDetail = vi.fn();
    render(
      <RecordRow
        record={usageRecordToView(makeUsageRecord({ id: 42, segment_id: "E1S10" }))}
        layout="compact"
        providerLabel={providerLabel}
        onOpenDetail={onOpenDetail}
      />,
    );

    const row = screen.getByRole("button");
    expect(row).toHaveTextContent(/S10\s*未命名集/);
    expect(row).toHaveTextContent("Google · imagen-4");
    // 项目名不占位置：悬浮层里整栏都属于同一个项目。
    expect(row).not.toHaveTextContent("星海列车");

    await userEvent.click(row);
    expect(onOpenDetail).toHaveBeenCalledWith(42);
  });

  it("keeps the in-episode id apart from the episode name it is shown with", () => {
    const { rerender } = render(
      <RecordRow
        record={usageRecordToView(
          makeUsageRecord({
            segment_id: "E7S02",
            segment_ref: { episode_title: "山门", episode_position: 1, item_id: "S02" },
          }),
        )}
        layout="compact"
        providerLabel={providerLabel}
      />,
    );
    // 分镜号与集名分开渲染：集名放不下时只截断集名，分镜号始终完整。
    expect(screen.getByText("S02")).toBeInTheDocument();
    expect(screen.getByText("山门")).toBeInTheDocument();

    rerender(
      <RecordRow
        record={usageRecordToView(
          makeUsageRecord({
            segment_id: "E3U01",
            segment_ref: { episode_title: "", episode_position: 2, item_id: "U01" },
          }),
        )}
        layout="compact"
        providerLabel={providerLabel}
      />,
    );
    expect(screen.getByText("U01")).toBeInTheDocument();
    expect(screen.getByText("第 2 集")).toBeInTheDocument();
  });

  it("puts a trailing action beside the row instead of nesting it in the button", async () => {
    const onRetry = vi.fn();
    render(
      <RecordRow
        record={usageRecordToView(makeUsageRecord({ id: 42 }))}
        layout="compact"
        providerLabel={providerLabel}
        onOpenDetail={vi.fn()}
        trailing={
          <button type="button" onClick={onRetry}>
            重试下载
          </button>
        }
      />,
    );

    await userEvent.click(screen.getByRole("button", { name: "重试下载" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("keeps the row inert when the record has no id to open", () => {
    render(
      <RecordRow
        record={{
          ...usageRecordToView(makeUsageRecord()),
          recordId: null,
          status: "pending",
          model: null,
        }}
        layout="compact"
        providerLabel={providerLabel}
        onOpenDetail={vi.fn()}
      />,
    );

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
    expect(screen.getByText(/待解析/)).toBeInTheDocument();
  });
});
