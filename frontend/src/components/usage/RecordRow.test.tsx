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
        record={usageRecordToView(
          makeUsageRecord({
            id: 42,
            segment_id: "E1S10",
            segment_ref: {
              episode_title: "",
              episode_position: 1,
              item_id: "S10",
              item_position: 3,
              item_excerpt: "推门而入",
              item_thumbnail: null,
            },
          }),
        )}
        layout="compact"
        providerLabel={providerLabel}
        onOpenDetail={onOpenDetail}
      />,
    );

    const row = screen.getByRole("button");
    expect(row).toHaveTextContent(/3\s*第 1 集 · 推门而入/);
    expect(row).not.toHaveTextContent("S10");
    expect(row).toHaveTextContent("Google · imagen-4");
    // 项目名不占位置：悬浮层里整栏都属于同一个项目。
    expect(row).not.toHaveTextContent("星海列车");

    await userEvent.click(row);
    expect(onOpenDetail).toHaveBeenCalledWith(42);
  });

  it("shows the item's position badge apart from the episode name and excerpt, never its id", () => {
    const { container, rerender } = render(
      <RecordRow
        record={usageRecordToView(
          makeUsageRecord({
            segment_id: "E7S02",
            segment_ref: {
              episode_title: "山门",
              episode_position: 1,
              item_id: "S02",
              item_position: 5,
              item_excerpt: "雨夜推门",
              item_thumbnail: "storyboards/scene_E7S02.png",
            },
          }),
        )}
        layout="compact"
        providerLabel={providerLabel}
      />,
    );
    // 位置角标叠在缩略图上、不截断；集名与正文摘录放不下时才截断。
    expect(screen.getByRole("img", { name: "分镜 5" })).toBeInTheDocument();
    expect(screen.getByText("山门 · 雨夜推门")).toBeInTheDocument();
    expect(container.querySelector("img")).toHaveAttribute("src", expect.stringContaining("scene_E7S02.png"));
    expect(screen.queryByText(/S02/)).not.toBeInTheDocument();

    rerender(
      <RecordRow
        record={usageRecordToView(
          makeUsageRecord({
            segment_id: "E3U01",
            segment_ref: {
              episode_title: "",
              episode_position: 2,
              item_id: "U01",
              item_position: 4,
              item_excerpt: "",
              item_thumbnail: null,
            },
          }),
        )}
        layout="compact"
        providerLabel={providerLabel}
      />,
    );
    expect(screen.getByRole("img", { name: "视频单元 4" })).toBeInTheDocument();
    expect(screen.getByText("第 2 集")).toBeInTheDocument();

    rerender(
      <RecordRow
        record={usageRecordToView(
          makeUsageRecord({
            segment_id: "E3U01",
            segment_ref: {
              episode_title: "",
              episode_position: 2,
              item_id: "U01",
              item_position: null,
              item_excerpt: "",
              item_thumbnail: null,
            },
          }),
        )}
        layout="compact"
        providerLabel={providerLabel}
      />,
    );
    // 条目已不在剧本里：写「已删除」，不回退成条目 ID。
    expect(screen.getByRole("img", { name: "已删除" })).toBeInTheDocument();
    expect(screen.queryByText(/U01/)).not.toBeInTheDocument();

    rerender(
      <RecordRow
        record={usageRecordToView(
          makeUsageRecord({
            segment_id: "E3G01",
            segment_ref: {
              episode_title: "",
              episode_position: 2,
              item_id: "G01",
              item_position: null,
              item_excerpt: "",
              item_thumbnail: null,
            },
          }),
        )}
        layout="compact"
        providerLabel={providerLabel}
      />,
    );
    // 宫格不按位置指称，仍以集内 ID 开头。
    expect(screen.getByText("G01")).toBeInTheDocument();
    expect(screen.queryByRole("img", { name: /分镜|已删除/ })).not.toBeInTheDocument();
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
