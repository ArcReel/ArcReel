import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PreviewableImageFrame } from "./PreviewableImageFrame";

describe("PreviewableImageFrame", () => {
  it("opens a fullscreen preview and closes from both the close button and backdrop", () => {
    render(
      <PreviewableImageFrame src="/demo.png" alt="Sample image">
        <img src="/demo.png" alt="Sample image" />
      </PreviewableImageFrame>,
    );

    const trigger = screen.getByRole("button", { name: "Sample image fullscreen preview" });

    fireEvent.click(trigger);
    expect(
      screen.getByRole("dialog", { name: "Sample image fullscreen preview" }),
    ).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close fullscreen preview" }));
    expect(
      screen.queryByRole("dialog", { name: "Sample image fullscreen preview" }),
    ).not.toBeInTheDocument();

    fireEvent.click(trigger);
    const dialog = screen.getByRole("dialog", { name: "Sample image fullscreen preview" });
    const backdrop = dialog.parentElement?.parentElement;
    expect(backdrop).not.toBeNull();

    fireEvent.click(backdrop as HTMLElement);

    expect(
      screen.queryByRole("dialog", { name: "Sample image fullscreen preview" }),
    ).not.toBeInTheDocument();
  }, 10_000);
});
