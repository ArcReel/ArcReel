import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { PreviewableImageFrame } from "./PreviewableImageFrame";

describe("PreviewableImageFrame", () => {
  it("opens the full-size image in a viewer and closes it with Escape", async () => {
    const user = userEvent.setup();
    render(
      <PreviewableImageFrame src="/demo.png" alt="示例图">
        <img src="/demo.png" alt="示例图" />
      </PreviewableImageFrame>,
    );

    await user.click(screen.getByRole("button", { name: "查看「示例图」的大图" }));
    const dialog = await screen.findByRole("dialog", { name: "示例图" });
    expect(within(dialog).getByRole("img", { name: "示例图" })).toHaveAttribute("src", "/demo.png");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(screen.queryByRole("dialog")).not.toBeInTheDocument());
  });

  it("offers no preview while there is no image", () => {
    render(
      <PreviewableImageFrame src={null} alt="示例图">
        <span>占位</span>
      </PreviewableImageFrame>,
    );

    expect(screen.queryByRole("button")).not.toBeInTheDocument();
  });
});
