import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ReferenceDurationPrecheck } from "@/types";
import { ReferenceDurationConfirmDialog } from "./ReferenceDurationConfirmDialog";

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

describe("ReferenceDurationConfirmDialog", () => {
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
    expect(screen.getByText("确认视频生成档位变化")).toBeInTheDocument();
    expect(screen.queryByText("以下调整将在本次生成中生效：")).not.toBeInTheDocument();
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
