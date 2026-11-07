import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useProjectsStore } from "@/stores/projects-store";
import type { EpisodeScript, ReferenceDurationPrecheck } from "@/types";
import { ReferenceDurationConfirmDialog } from "./ReferenceDurationConfirmDialog";

/** Nonblocking preflight notice that references use the first N images; backend messages already use current languages. */
function clampedProblem(unitId: string, message: string) {
  return {
    code: "reference_images_clamped",
    blocking: false,
    unit_id: unitId,
    locations: [{ path: ["text"], line: null }],
    params: { count: 3, max_count: 1, provider: "openai", model: "sora-2" },
    action: "review_reference_selection",
    message,
  };
}

function precheck(overrides: Partial<ReferenceDurationPrecheck> = {}): ReferenceDurationPrecheck {
  return {
    needs_confirmation: false,
    script_duration: 4,
    duration_input: 4,
    request_duration: 4,
    adjustment: "exact",
    declared_capability: "r2v",
    hydrated_capability: "r2v",
    provider_id: "openai",
    model_id: "sora-2",
    problems: [],
    ...overrides,
  };
}

/** Formal-script units: another precedes E1U1/E1U2, so positions differ from ID numbers. */
function seedReferenceScript() {
  const script = {
    episode: 1,
    title: "",
    content_mode: "reference_video",
    video_units: [
      { unit_id: "E1U5", text: "开场" },
      { unit_id: "E1U1", text: "推门进来" },
      { unit_id: "E1U2", text: "雨夜里走向吧台" },
    ],
  };
  useProjectsStore.setState({ currentScripts: { "episode_1.json": script as unknown as EpisodeScript } });
}

describe("ReferenceDurationConfirmDialog", () => {
  afterEach(() => useProjectsStore.setState({ currentScripts: {} }));

  it("compares the script tier with the requested tier", () => {
    render(
      <ReferenceDurationConfirmDialog
        open
        items={[
          {
            unitId: "E1U1",
            precheck: {
              needs_confirmation: true,
              script_duration: 4,
              duration_input: 4,
              request_duration: 8,
              adjustment: "up",
              declared_capability: "i2v",
              hydrated_capability: "i2v",
              provider_id: "openai",
              model_id: "sora-2",
              problems: [],
            },
          },
        ]}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("4 秒")).toBeInTheDocument();
    expect(screen.getByText("8 秒")).toBeInTheDocument();
    expect(screen.getByText("（长 4 秒）")).toBeInTheDocument();
    expect(screen.queryByText("（长 0 秒）")).not.toBeInTheDocument();
    // Duration-only: title/body exclude the nonblocking-notice paragraph.
    expect(screen.getByText("确认视频生成档位变化")).toBeInTheDocument();
    expect(screen.queryByText("以下调整将在本次生成中生效：")).not.toBeInTheDocument();
  });

  it("lists advisory problems per unit under the duration rows", () => {
    seedReferenceScript();
    render(
      <ReferenceDurationConfirmDialog
        open
        items={[
          {
            unitId: "E1U1",
            precheck: precheck({
              needs_confirmation: true,
              request_duration: 8,
              problems: [clampedProblem("E1U1", "参考图数量 3 超出 openai/sora-2 上限 1")],
            }),
          },
          {
            unitId: "E1U2",
            precheck: precheck({
              problems: [clampedProblem("E1U2", "参考图数量 5 超出 openai/sora-2 上限 1")],
            }),
          },
        ]}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    // Duration comparisons include only units needing tier decisions.
    expect(screen.getByText("1 个单元将改用不同的视频时长档位：")).toBeInTheDocument();
    expect(screen.getByText("以下调整将在本次生成中生效：")).toBeInTheDocument();
    expect(screen.getByText("参考图数量 3 超出 openai/sora-2 上限 1")).toBeInTheDocument();
    expect(screen.getByText("参考图数量 5 超出 openai/sora-2 上限 1")).toBeInTheDocument();
    // Address multiple units by position per row: E1U1 has duration/notice rows, E1U2 notice only.
    expect(screen.getAllByRole("img", { name: "视频单元 2" })).toHaveLength(2);
    expect(screen.getByRole("img", { name: "视频单元 3" })).toBeInTheDocument();
    expect(screen.getByText("雨夜里走向吧台")).toBeInTheDocument();
    expect(screen.queryByText("U2")).not.toBeInTheDocument();
  });

  it("stands on its own when only advisory problems need confirming", () => {
    seedReferenceScript();
    render(
      <ReferenceDurationConfirmDialog
        open
        items={[
          {
            unitId: "E1U1",
            precheck: precheck({
              problems: [clampedProblem("E1U1", "参考图数量 3 超出 openai/sora-2 上限 1")],
            }),
          },
        ]}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("确认视频生成")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续生成" })).toBeInTheDocument();
    expect(screen.getByText("参考图数量 3 超出 openai/sora-2 上限 1")).toBeInTheDocument();
    // Without tier deviations, show neither comparison rows nor unit labels.
    expect(screen.queryByText("4 秒")).not.toBeInTheDocument();
    expect(screen.queryByRole("img", { name: "视频单元 2" })).not.toBeInTheDocument();
  });

  it("renders nothing when no item needs confirming", () => {
    const { container } = render(
      <ReferenceDurationConfirmDialog
        open
        items={[{ unitId: "E1U1", precheck: precheck() }]}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(container).toBeEmptyDOMElement();
  });
});
