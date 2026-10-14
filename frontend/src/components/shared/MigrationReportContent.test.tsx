import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { WorkflowMigrationAdjustment, WorkflowMigrationReport } from "@/types/workflow";

import { MigrationReportContent } from "./MigrationReportContent";

function report(adjusted: WorkflowMigrationAdjustment[]): WorkflowMigrationReport {
  return {
    schema_version: 2,
    migrated_at: "2026-10-13T00:00:00Z",
    from_schema_version: 6,
    to_schema_version: 18,
    complete: true,
    registered: {},
    adjusted,
    skipped: [],
    acknowledged_at: null,
  };
}

const EPISODES = [
  { episode: 1, title: "开端" },
  { episode: 2, title: "雨夜" },
];

describe("MigrationReportContent", () => {
  it("names both episodes when a shared script stays with the episode it records", () => {
    render(
      <MigrationReportContent
        report={report([
          {
            code: "shared_script_binding_split",
            episode: 1,
            file: "scripts/episode_1.json",
            backup: null,
            params: { from: "scripts/custom.json", to: "scripts/episode_1.json", owner: "2" },
          },
        ])}
        episodes={EPISODES}
      />,
    );

    expect(
      screen.getByText("开端与雨夜共用剧本 scripts/custom.json。剧本归雨夜，开端改用 scripts/episode_1.json。"),
    ).toBeInTheDocument();
  });

  it("tells a missing episode number apart from a different one", () => {
    render(
      <MigrationReportContent
        report={report([
          {
            code: "script_episode_corrected",
            episode: 1,
            file: "scripts/episode_1.json",
            backup: null,
            params: { from: "", to: "1" },
          },
          {
            code: "script_episode_corrected",
            episode: 2,
            file: "scripts/episode_2.json",
            backup: null,
            params: { from: "3", to: "2" },
          },
        ])}
        episodes={EPISODES}
      />,
    );

    expect(screen.getByText("剧本 scripts/episode_1.json 没有记录所属的集，已补为开端。")).toBeInTheDocument();
    expect(
      screen.getByText("剧本 scripts/episode_2.json 记录的集与使用它的雨夜不一致，已改为雨夜。"),
    ).toBeInTheDocument();
  });
});
