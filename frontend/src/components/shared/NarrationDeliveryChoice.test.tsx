import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { NarrationDeliveryChoice } from "./NarrationDeliveryChoice";

describe("NarrationDeliveryChoice", () => {
  it("两种交付方式默认都可选", () => {
    render(<NarrationDeliveryChoice value="post_production" onChange={vi.fn()} />);

    expect(screen.getByRole("button", { name: "使用当前 TTS" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "后期配音" })).toBeEnabled();
  });

  it("成片时长由端点固定时禁用 TTS 并给出可见原因，后期配音仍可选", async () => {
    const onChange = vi.fn();
    render(
      <NarrationDeliveryChoice value="post_production" onChange={onChange} ttsDurationEndpointFixed />,
    );

    const tts = screen.getByRole("button", { name: "使用当前 TTS" });
    expect(tts).toBeDisabled();
    expect(screen.getByRole("button", { name: "后期配音" })).toBeEnabled();
    expect(screen.getByText(/成片时长由端点固定/)).toBeInTheDocument();

    await userEvent.click(tts);
    expect(onChange).not.toHaveBeenCalled();
  });
});
