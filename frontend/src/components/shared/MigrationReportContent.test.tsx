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

  it("names the asset a shared reference file belonged to and the copy that replaced it", () => {
    render(
      <MigrationReportContent
        report={report([
          {
            code: "shared_reference_file_copied",
            episode: null,
            file: "characters/refs_audio/阿青.wav",
            backup: null,
            params: { asset_type: "character", name: "阿青", owner: "阿离", from: "characters/refs_audio/阿离.wav" },
          },
        ])}
        episodes={EPISODES}
      />,
    );

    expect(
      screen.getByText(
        "角色「阿青」用的是「阿离」名下的文件 characters/refs_audio/阿离.wav，已复制一份为 characters/refs_audio/阿青.wav 改用。",
      ),
    ).toBeInTheDocument();
  });

  it("says where references and derivatives of a removed case variant went", () => {
    const variant = { name: "ALICE", kept: "Alice" };
    render(
      <MigrationReportContent
        report={report([
          {
            code: "duplicate_asset_references_rewritten",
            episode: 2,
            file: null,
            backup: null,
            params: { asset_type: "character", ...variant },
          },
          {
            code: "duplicate_asset_derivative_merged",
            episode: null,
            file: null,
            backup: "project.json.kept.v17-1",
            params: { ...variant, derivative: "Hat", into: "Hat" },
          },
          {
            code: "duplicate_asset_derivative_merged",
            episode: null,
            file: null,
            backup: "project.json.kept.v17-1",
            params: { ...variant, derivative: "Coat", into: "coat", folded: "true" },
          },
        ])}
        episodes={EPISODES}
      />,
    );

    expect(screen.getByText("雨夜里引用角色「ALICE」的地方已改为「Alice」。")).toBeInTheDocument();
    expect(screen.getByText("角色「ALICE」的衍生「Hat」已移到「Alice」名下。")).toBeInTheDocument();
    expect(
      screen.getByText("角色「ALICE」的衍生「Coat」与「Alice」已有的衍生「coat」重名，已并入「coat」。"),
    ).toBeInTheDocument();
  });
});
