import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import "@/i18n";
import { StylePicker, type StylePickerValue } from "./StylePicker";

const liveValue: StylePickerValue = {
  mode: "template",
  templateId: "live_premium_drama",
  activeCategory: "live",
  uploadedFile: null,
  uploadedPreview: null,
};

describe("StylePicker", () => {
  it("selects a template from the active category", () => {
    const onChange = vi.fn();
    render(<StylePicker value={liveValue} onChange={onChange} />);
    expect(screen.getByRole("button", { name: /精品短剧|premium/i })).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(screen.getByRole("button", { name: /张艺谋/ }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ mode: "template", templateId: "live_zhang_yimou" }));
  });

  it("keeps the chosen template when switching to the custom tab", () => {
    const onChange = vi.fn();
    render(<StylePicker value={liveValue} onChange={onChange} />);
    fireEvent.click(screen.getByRole("tab", { name: "自定义" }));
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ mode: "custom", templateId: "live_premium_drama" }));
  });

  it("keeps an uploaded image when switching back to a template category", () => {
    const onChange = vi.fn();
    const file = new File([""], "x.png", { type: "image/png" });
    render(
      <StylePicker
        value={{ ...liveValue, mode: "custom", templateId: null, uploadedFile: file, uploadedPreview: "blob:test" }}
        onChange={onChange}
      />,
    );
    fireEvent.click(screen.getByRole("tab", { name: /漫剧|动画/ }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ mode: "template", activeCategory: "anim", uploadedFile: file, uploadedPreview: "blob:test" }),
    );
  });

  it("previews a picked file with an address it owns: never handed to the caller, freed on unmount and rebuilt on remount", () => {
    let seq = 0;
    const created = vi.spyOn(URL, "createObjectURL").mockImplementation(() => `blob:picker-${++seq}`);
    const revoked = vi.spyOn(URL, "revokeObjectURL").mockImplementation(() => {});
    const file = new File([""], "x.png", { type: "image/png" });
    const onChange = vi.fn();
    const { unmount } = render(<StylePicker value={{ ...liveValue, mode: "custom" }} onChange={onChange} />);
    const input = document.querySelector("input[type='file']") as HTMLInputElement;
    fireEvent.change(input, { target: { files: [file] } });
    expect(onChange).toHaveBeenLastCalledWith(expect.objectContaining({ uploadedFile: file, uploadedPreview: null }));
    unmount();

    const picked = { ...liveValue, mode: "custom" as const, templateId: null, uploadedFile: file };
    const first = render(<StylePicker value={picked} onChange={onChange} />);
    expect(screen.getByRole("img", { name: "上传风格参考图" })).toHaveAttribute("src", "blob:picker-1");
    first.unmount();
    expect(revoked).toHaveBeenCalledWith("blob:picker-1");

    render(<StylePicker value={picked} onChange={onChange} />);
    expect(screen.getByRole("img", { name: "上传风格参考图" })).toHaveAttribute("src", "blob:picker-2");
    expect(created).toHaveBeenCalledTimes(2);
    created.mockRestore();
    revoked.mockRestore();
  });

  it("does not mark a template from another category as selected", () => {
    render(<StylePicker value={{ ...liveValue, activeCategory: "anim" }} onChange={() => {}} />);
    for (const card of screen.getAllByRole("button", { pressed: false })) {
      expect(card).toHaveAttribute("aria-pressed", "false");
    }
    expect(screen.queryByRole("button", { pressed: true })).not.toBeInTheDocument();
  });
});
