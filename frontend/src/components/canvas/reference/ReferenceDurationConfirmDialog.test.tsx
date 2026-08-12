import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ReferenceDurationConfirmDialog } from "./ReferenceDurationConfirmDialog";

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
  });
});
