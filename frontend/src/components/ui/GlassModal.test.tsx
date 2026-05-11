import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { GlassModal } from "./GlassModal";

describe("GlassModal", () => {
  it("renders nothing when open=false", () => {
    render(
      <GlassModal open={false} onClose={() => {}} ariaLabel="x">
        <p data-testid="body">x</p>
      </GlassModal>,
    );
    expect(screen.queryByTestId("body")).toBeNull();
  });

  it("renders glass panel chrome class on the dialog wrapper", () => {
    render(
      <GlassModal open onClose={() => {}} ariaLabel="x">
        <p>body</p>
      </GlassModal>,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("arc-glass-panel");
    expect(dialog.className).toContain("rounded-2xl");
  });

  it("defaults hairline tone to accent", () => {
    render(
      <GlassModal open onClose={() => {}} ariaLabel="x">
        <p>body</p>
      </GlassModal>,
    );
    const hairline = screen
      .getByRole("dialog")
      .querySelector(".arc-glass-hairline");
    expect(hairline).not.toBeNull();
    expect(hairline).toHaveAttribute("data-tone", "accent");
  });

  it("sets hairline tone='warm' for warning-styled dialogs", () => {
    render(
      <GlassModal open onClose={() => {}} ariaLabel="x" hairlineTone="warm">
        <p>body</p>
      </GlassModal>,
    );
    const hairline = screen
      .getByRole("dialog")
      .querySelector(".arc-glass-hairline");
    expect(hairline).toHaveAttribute("data-tone", "warm");
  });

  it("propagates onClose to backdrop click and Esc", () => {
    const onClose = vi.fn();
    render(
      <GlassModal open onClose={onClose} ariaLabel="x">
        <p>body</p>
      </GlassModal>,
    );
    fireEvent.click(screen.getByTestId("modal-backdrop"));
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("respects widthClassName + appends panelClassName", () => {
    render(
      <GlassModal
        open
        onClose={() => {}}
        ariaLabel="x"
        widthClassName="w-[680px]"
        panelClassName="max-h-[80vh]"
      >
        <p>body</p>
      </GlassModal>,
    );
    const dialog = screen.getByRole("dialog");
    expect(dialog.className).toContain("w-[680px]");
    expect(dialog.className).toContain("max-h-[80vh]");
  });
});
