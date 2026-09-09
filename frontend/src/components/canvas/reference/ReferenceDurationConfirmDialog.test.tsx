import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReferenceDurationPrecheck } from "@/types";
import { ReferenceDurationConfirmDialog } from "./ReferenceDurationConfirmDialog";

/** 预检对参考图取前 N 张的非阻断告知；后端已把 message 渲染成当前语言。 */
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
    current_visual_duration: 4,
    adjustment: "exact",
    declared_capability: "r2v",
    hydrated_capability: "r2v",
    provider_id: "openai",
    model_id: "sora-2",
    problems: [],
    ...overrides,
  };
}

describe("ReferenceDurationConfirmDialog", () => {
  it("shows the exact server quote and provider request coordinates", () => {
    render(
      <ReferenceDurationConfirmDialog
        open
        items={[
          {
            unitId: "E1U1",
            precheck: {
              needs_confirmation: true,
              script_duration: 4,
              duration_input: 8,
              request_duration: 8,
              current_visual_duration: 4,
              adjustment: "exact",
              declared_capability: "i2v",
              hydrated_capability: "i2v",
              provider_id: "openai",
              model_id: "sora-2",
              request_cost: {
                amount: 0.8,
                currency: "USD",
                provider_id: "openai",
                model_id: "sora-2",
                request_duration_seconds: 8,
              },
              problems: [],
            },
          },
        ]}
        onConfirm={vi.fn()}
        onCancel={vi.fn()}
      />,
    );

    expect(screen.getByText("新视频请求费用：$0.80 · openai/sora-2 · 8 秒")).toBeInTheDocument();
    expect(screen.getByText("4 秒")).toBeInTheDocument();
    expect(screen.getByText("（长 4 秒）")).toBeInTheDocument();
    expect(screen.queryByText("（长 0 秒）")).not.toBeInTheDocument();
    // 仅时长项：标题与正文都不掺入非阻断告知那一段
    expect(screen.getByText("确认视频生成档位变化")).toBeInTheDocument();
    expect(screen.queryByText("以下调整将在本次生成中生效：")).not.toBeInTheDocument();
  });

  it("lists advisory problems per unit under the duration rows", () => {
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

    // 时长对照行只收需要拍板档位的单元
    expect(screen.getByText("1 个单元将改用不同的视频时长档位：")).toBeInTheDocument();
    expect(screen.getByText("以下调整将在本次生成中生效：")).toBeInTheDocument();
    expect(screen.getByText("参考图数量 3 超出 openai/sora-2 上限 1")).toBeInTheDocument();
    expect(screen.getByText("参考图数量 5 超出 openai/sora-2 上限 1")).toBeInTheDocument();
    expect(screen.getByText("E1U2")).toBeInTheDocument();
  });

  it("stands on its own when only advisory problems need confirming", () => {
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
    // 没有档位偏离就不画对照行，也不出现单元标签
    expect(screen.queryByText("4 秒")).not.toBeInTheDocument();
    expect(screen.queryByText("E1U1")).not.toBeInTheDocument();
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
