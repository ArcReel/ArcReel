import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { API } from "@/api";
import { useAppStore } from "@/stores/app-store";
import type { WorkflowMigrationReport } from "@/types/workflow";

import { MigrationReportNotice } from "./MigrationReportNotice";

function report(overrides: Partial<WorkflowMigrationReport> = {}): WorkflowMigrationReport {
  return {
    schema_version: 2,
    migrated_at: "2026-10-13T00:00:00Z",
    from_schema_version: 14,
    to_schema_version: 18,
    complete: true,
    registered: {},
    adjusted: [
      {
        code: "draft_name_conflict_resolved",
        episode: 7,
        file: "drafts/episode_7/script_plan_segments.json",
        backup: "drafts/episode_7/step1_segments.json.kept.v9-1",
        params: {},
      },
      {
        code: "placeholder_asset_added",
        episode: null,
        file: null,
        backup: null,
        params: { asset_type: "scene", name: "雨巷" },
      },
    ],
    skipped: [
      {
        kind: "episode-audio",
        episode: 7,
        resource_id: "E7S01",
        artifact_path: "audio/segment_E7S01.wav",
        reason: "legacy audio version has no TTS settings to project a basis from",
      },
    ],
    acknowledged_at: null,
    ...overrides,
  };
}

const EPISODES = [{ episode: 7, title: "雨夜" }];

describe("MigrationReportNotice", () => {
  beforeEach(() => {
    useAppStore.setState(useAppStore.getInitialState(), true);
  });

  it("opens the read-only report grouped into changed and not carried over", async () => {
    render(<MigrationReportNotice projectName="demo" report={report()} episodes={EPISODES} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "本次升级调整了 3 项内容" }));

    const dialog = await screen.findByRole("dialog", { name: "升级调整了哪些内容" });
    expect(within(dialog).getByRole("heading", { name: /已调整/ })).toBeInTheDocument();
    expect(within(dialog).getByRole("heading", { name: /未能带入/ })).toBeInTheDocument();
    expect(
      within(dialog).getByText("雨夜的脚本规划草稿有新旧两个文件名的两份，保留新的一份，旧的一份另存为备份。"),
    ).toBeInTheDocument();
    expect(within(dialog).getByText("剧本用到的场景「雨巷」没有定义，已补一条空白定义。")).toBeInTheDocument();
    expect(within(dialog).getByText("旁白音频 · 雨夜")).toBeInTheDocument();
    expect(within(dialog).getByText("audio/segment_E7S01.wav")).toBeInTheDocument();
    expect(within(dialog).queryByText(/补录|认领/)).not.toBeInTheDocument();
  });

  it("disappears once dismissed and records the acknowledgement", async () => {
    const acknowledge = vi.spyOn(API, "acknowledgeMigrationReport").mockResolvedValue({ success: true });
    render(<MigrationReportNotice projectName="demo" report={report()} episodes={EPISODES} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "关闭升级提示" }));

    expect(acknowledge).toHaveBeenCalledWith("demo");
    expect(screen.queryByRole("button", { name: "本次升级调整了 3 项内容" })).not.toBeInTheDocument();
  });

  it("stays and explains why when the acknowledgement cannot be saved", async () => {
    vi.spyOn(API, "acknowledgeMigrationReport").mockRejectedValue(new Error("磁盘已满"));
    render(<MigrationReportNotice projectName="demo" report={report()} episodes={EPISODES} />);
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "关闭升级提示" }));

    expect(screen.getByRole("button", { name: "本次升级调整了 3 项内容" })).toBeInTheDocument();
    expect(useAppStore.getState().toast?.text).toBe("没能关闭升级提示：磁盘已满");
  });

  it("shows nothing for an empty report", () => {
    const { container } = render(
      <MigrationReportNotice projectName="demo" report={report({ adjusted: [], skipped: [] })} episodes={EPISODES} />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
